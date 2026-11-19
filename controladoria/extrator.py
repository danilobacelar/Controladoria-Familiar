"""Leitura de extrato de conta corrente (PDF ou texto) e montagem da proposta.

O extrator só lê. Ele devolve linhas com origem (página/linha) e confere o saldo
corrente linha a linha; se a conferência falhar, a importação inteira vai para
QUARENTENA e nenhum lançamento é gravado (T13).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import jsonschema

CONTRATO = json.loads(Path(__file__).with_name("proposta_extracao.schema.json").read_text(encoding="utf-8"))

MOEDA = r"-?R\$\s?-?[\d\.]+,\d{2}"
RE_LINHA = re.compile(rf"^\s*(\d{{2}}/\d{{2}}/\d{{4}})\s+(\S+)\s+(.*?)\s+({MOEDA})\s+({MOEDA})\s*$")
RE_SALDO_ANT = re.compile(rf"Saldo Anterior:\s*({MOEDA})")
RE_PERIODO = re.compile(r"Per[ií]odo do extrato:\s*(\d{2}/\d{2}/\d{4})\s*at[ée]\s*(\d{2}/\d{2}/\d{4})")
RE_AGENCIA = re.compile(r"Ag[êe]ncia:\s*(\d+)")
RE_CONTA = re.compile(r"Conta:\s*(\d+)")
RE_NOME = re.compile(r"Nome:\s*(.+?)\s*$", re.M)
RE_SALDO_FINAL = re.compile(rf"^\s*Saldo:\s*({MOEDA})", re.M)
RE_LIMITE = re.compile(rf"Limite de Cr[ée]dito.*?({MOEDA})")
# Texto que parece instrução dentro do documento é tratado como dado e só gera alerta (T14).
RE_INSTRUCAO = re.compile(r"ignor\w* (as )?regras|fa[cç]a upload|envie (os )?dados|ignore (all|previous)", re.I)


class ErroConferencia(Exception):
    pass


def moeda(txt: str) -> Decimal:
    neg = "-" in txt
    n = re.sub(r"[^\d,]", "", txt).replace(",", ".")
    v = Decimal(n)
    return -v if neg else v


def data_iso(d: str) -> str:
    dd, mm, aa = d.split("/")
    return f"{aa}-{mm}-{dd}"


def texto_pdf(caminho: str | Path) -> str:
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-layout", str(caminho), "-"], check=True,
                              capture_output=True, text=True).stdout
    import pdfplumber  # alternativa sem poppler (layout menos fiel)
    with pdfplumber.open(str(caminho)) as pdf:
        return "\n\f".join(p.extract_text(layout=True) or "" for p in pdf.pages)


def sha256_arquivo(caminho: str | Path) -> str:
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


@dataclass
class Linha:
    seq: int
    pagina: int
    linha_texto: int
    data: str
    documento: str
    historico: str
    contraparte: str
    valor: Decimal
    saldo: Decimal

    @property
    def origem(self):
        return f"p{self.pagina} lin{self.linha_texto}"


@dataclass
class Extrato:
    titular: str | None = None
    agencia: str | None = None
    conta: str | None = None
    inicio: str | None = None
    fim: str | None = None
    saldo_anterior: Decimal | None = None
    saldo_final: Decimal | None = None
    limite: Decimal | None = None
    linhas: list = field(default_factory=list)
    alertas: list = field(default_factory=list)


# Históricos conhecidos, usados quando o texto do PDF não preserva colunas (leitura sem pdftotext).
RE_HISTORICO = re.compile(
    r"^(D[ée]bito Pix|Cr[ée]dito Pix|Pagamento Pix|Pix Enviado|Pix Recebido|TED|Ted|DOC|Transfer[êe]ncia\S*"
    r"|D[ée]b\.? Cart[ãa]o \S+|Fat\.? cart[ãa]o \S+|Tarifa\S*|Rendimento\S*)\s+(.*)$")


def _separar(meio: str):
    partes = re.split(r"\s{2,}", meio.strip(), maxsplit=1)
    if len(partes) == 2:
        return partes[0].strip(), partes[1].strip()
    if m := RE_HISTORICO.match(meio.strip()):
        return m.group(1), m.group(2).strip()
    return partes[0].strip(), ""


def ler_texto(texto: str) -> Extrato:
    ex = Extrato()
    if m := RE_NOME.search(texto):
        ex.titular = m.group(1)
    if m := RE_AGENCIA.search(texto):
        ex.agencia = m.group(1)
    if m := RE_CONTA.search(texto):
        ex.conta = m.group(1)
    if m := RE_PERIODO.search(texto):
        ex.inicio, ex.fim = data_iso(m.group(1)), data_iso(m.group(2))
    if m := RE_SALDO_ANT.search(texto):
        ex.saldo_anterior = moeda(m.group(1))
    if m := RE_SALDO_FINAL.search(texto):
        ex.saldo_final = moeda(m.group(1))
    if m := RE_LIMITE.search(texto):
        ex.limite = moeda(m.group(1))
    if ex.saldo_anterior is None:
        raise ErroConferencia("Saldo anterior não encontrado; layout não reconhecido")

    saldo = ex.saldo_anterior
    for p, pagina in enumerate(texto.split("\f"), 1):
        for n, bruto in enumerate(pagina.splitlines(), 1):
            if RE_INSTRUCAO.search(bruto):
                ex.alertas.append(f"Texto com aparência de instrução em p{p} lin{n}; tratado como dado e ignorado.")
            m = RE_LINHA.match(bruto)
            if not m:
                continue
            data, doc, meio, v_txt, s_txt = m.groups()
            valor_abs, novo_saldo = moeda(v_txt), moeda(s_txt)
            delta = novo_saldo - saldo
            if abs(delta) != abs(valor_abs):
                raise ErroConferencia(
                    f"Saldo não confere em p{p} lin{n}: anterior {saldo}, valor {valor_abs}, saldo {novo_saldo}")
            historico, contraparte = _separar(meio)
            ex.linhas.append(Linha(len(ex.linhas) + 1, p, n, data_iso(data), doc, historico, contraparte,
                                   delta, novo_saldo))
            saldo = novo_saldo
    if not ex.linhas:
        raise ErroConferencia("Nenhum lançamento reconhecido")
    if ex.saldo_final is not None and saldo != ex.saldo_final:
        raise ErroConferencia(f"Saldo final recalculado {saldo} difere do informado {ex.saldo_final}")
    return ex


def mascarar_conta(conta: str | None) -> str | None:
    return None if not conta else "****" + conta[-2:]


def chave_lancamento(instrumento_id, ln: Linha) -> str:
    base = f"{instrumento_id}|{ln.data}|{ln.documento}|{ln.valor}|{ln.saldo}"
    return "LC-" + hashlib.sha256(base.encode()).hexdigest()[:16]


def montar_proposta(ex: Extrato, proposta_id: str, nome_seguro: str, fonte: str, hash_sha256: str | None,
                    sugestoes: dict, tarefas: list) -> dict:
    """Monta o JSON no contrato proposta_extracao.schema.json e valida."""
    D = "DOCUMENTADO_EXTRATO"

    def c(nome, valor, unidade, evid, nivel=D, conf="ALTA"):
        return {"nome": nome, "valor": valor, "unidade": unidade, "evidencia": evid,
                "nivel_evidencia": nivel, "confianca": conf}

    campos = [
        c("conta.agencia", ex.agencia, None, "cabeçalho"),
        c("conta.numero", mascarar_conta(ex.conta), None, "cabeçalho (mascarado)"),
        c("conta.atribuicao_economica", None, None, "IF09: validação expressa por conta e vigência",
          "PENDENTE", "INDETERMINADA"),
        c("periodo.inicio", ex.inicio, "data", "cabeçalho"),
        c("periodo.fim", ex.fim, "data", "cabeçalho"),
        c("saldo.anterior", float(ex.saldo_anterior), "BRL", "cabeçalho"),
    ]
    for ln in ex.linhas:
        campos.append(c(f"lancamento.L{ln.seq:02d}", float(ln.valor), "BRL",
                        f"{ln.origem}: {ln.data} {ln.historico} | {ln.contraparte}"))
    for ln in ex.linhas:
        s = sugestoes[ln.seq]
        campos.append(c(f"classe_proposta.L{ln.seq:02d}", s["descricao"], None,
                        f"{ln.origem}; estado: SUGERIDO, não CLASSIFICADO", "PENDENTE", s["confianca"]))
    if ex.saldo_final is not None:
        campos.append(c("saldo.final", float(ex.saldo_final), "BRL", "posição final"))
    if ex.limite is not None:
        campos.append(c("limite.cheque_especial", float(ex.limite), "BRL", "posição (não é ativo)"))
    deb = sum(-l.valor for l in ex.linhas if l.valor < 0)
    cred = sum(l.valor for l in ex.linhas if l.valor > 0)
    campos.append(c("conferencia.saldo_recalculado", float(ex.saldo_anterior - deb + cred), "BRL",
                    f"{len(ex.linhas)}/{len(ex.linhas)} linhas conferem"))

    proposta = {
        "proposta_id": proposta_id,
        "documento": {"nome_seguro": nome_seguro, "tipo": "EXTRATO_CONTA_CORRENTE", "fonte": fonte,
                      "hash_sha256": hash_sha256},
        "data_base": ex.fim,
        "status": "PROPOSTA_NAO_GRAVADA",
        "campos": campos,
        "efeitos_economicos": {
            "gera_caixa": False, "gera_ativo": False, "gera_passivo": False,
            "nota": "Proposta sem efeito até decisão humana. Pagamento de fatura e transferência interna não geram "
                    "despesa nem receita. Saldo do extrato não altera abertura IRPF (IF02 R01).",
        },
        "tarefas_aprovacao": [{"tipo": t["tipo"], "responsavel": "APROVADOR", "justificativa": t["justificativa"]}
                              for t in tarefas],
        "alertas": list(ex.alertas),
    }
    jsonschema.Draft202012Validator(CONTRATO).validate(proposta)
    return proposta
