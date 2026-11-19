"""Regras de negócio: importação de documento, fila de aprovação, decisões e painel."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path

from .classificador import Contexto, normalizar, sugerir
from .db import auditar, linha
from .extrator import (ErroConferencia, chave_lancamento, ler_texto, mascarar_conta, montar_proposta,
                       sha256_arquivo, texto_pdf)


class Recusado(Exception):
    """Operação bloqueada por regra (permissão, duplicidade, decisão individual etc.)."""


# ---------------------------------------------------------------- importação

def _contexto(con, titular):
    familia = {}
    for r in con.execute("SELECT nome, papel FROM pessoas_entidades WHERE tipo='PESSOA' AND nucleo_familiar=1"):
        familia[normalizar(r["nome"]).split()[0]] = r["papel"]
    empresas = {normalizar(r["alias"]): r["nome"] for r in con.execute(
        "SELECT nome, alias FROM pessoas_entidades WHERE tipo='EMPRESA' AND alias IS NOT NULL AND alias <> ''")}
    return Contexto(titular=titular, familia=familia, empresas=empresas)


def importar_extrato(con, *, ator_id, instrumento_id=None, caminho=None, texto=None, nome_original=None,
                     simulacao=False):
    """Lê um extrato e grava uma PROPOSTA (nunca aprovação). Falha => QUARENTENA, nada parcial."""
    if linha(con, "SELECT id FROM usuarios WHERE id=?", ator_id) is None:
        raise Recusado("usuário desconhecido")
    if instrumento_id and linha(con, "SELECT id FROM instrumentos WHERE id=?", instrumento_id) is None:
        raise Recusado("instrumento desconhecido")
    if caminho:
        hash_ = sha256_arquivo(caminho)
        nome_original = nome_original or Path(caminho).name
    else:
        hash_ = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    if linha(con, "SELECT id FROM fontes_documentais WHERE hash_sha256=?", hash_):
        raise Recusado("documento já importado (mesmo hash): duplicidade bloqueada")

    fonte_id = "F-" + uuid.uuid4().hex[:10]
    lote_id = "LT-" + uuid.uuid4().hex[:10]
    proposta_id = "PR-" + uuid.uuid4().hex[:10]
    try:
        bruto = texto if texto is not None else texto_pdf(caminho)
        ex = ler_texto(bruto)
        ctx = _contexto(con, ex.titular)
        sugestoes, tarefas = {}, []
        for ln in ex.linhas:
            s = sugerir(ln.historico, ln.contraparte, ln.valor, ctx)
            sugestoes[ln.seq] = {"descricao": s.descricao, "confianca": s.confianca, "obj": s}
            tarefas.append({"tipo": s.tarefa, "justificativa": f"L{ln.seq:02d} ({ln.data}): {s.justificativa}"})
        if instrumento_id is None:
            tarefas.insert(0, {"tipo": "VINCULAR_INSTRUMENTO",
                               "justificativa": "Instituição/conta não vinculada a um instrumento do cadastro."})
        nome_seguro = f"extrato_{mascarar_conta(ex.conta) or 'conta'}_{ex.inicio}_{ex.fim}.pdf"
        proposta = montar_proposta(ex, proposta_id, nome_seguro, f"Importação por {ator_id}", hash_,
                                   sugestoes, tarefas)

        con.execute("INSERT INTO fontes_documentais (id, nome_seguro, tipo, origem_uri, hash_sha256, data_base)"
                    " VALUES (?,?,?,?,?,?)", (fonte_id, nome_seguro, "EXTRATO_CONTA_CORRENTE",
                                              nome_original, hash_, ex.fim))
        con.execute("INSERT INTO lotes_importacao (id, fonte_id, estado, simulacao) VALUES (?,?,?,?)",
                    (lote_id, fonte_id, "PROCESSADO", int(simulacao)))
        con.execute("INSERT INTO propostas (id, fonte_id, lote_id, status, simulacao, json, criado_por)"
                    " VALUES (?,?,?,?,?,?,?)", (proposta_id, fonte_id, lote_id, "PROPOSTA_NAO_GRAVADA",
                                                int(simulacao), json.dumps(proposta, ensure_ascii=False), ator_id))
        duplicados = 0
        for ln in ex.linhas:
            lid = chave_lancamento(instrumento_id or f"conta:{ex.agencia}/{ex.conta}", ln)
            if linha(con, "SELECT id FROM lancamentos WHERE id=?", lid):
                duplicados += 1
                continue
            s = sugestoes[ln.seq]["obj"]
            con.execute(
                "INSERT INTO lancamentos (id, proposta_id, instrumento_id, linha_origem, data, documento, historico,"
                " contraparte, valor, saldo_extrato, evento_sugerido, classe_sugerida, confianca, simulacao)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lid, proposta_id, instrumento_id, ln.origem, ln.data, ln.documento, ln.historico, ln.contraparte,
                 float(ln.valor), float(ln.saldo), s.evento, s.classe, s.confianca, int(simulacao)))
            con.execute(
                "INSERT INTO tarefas_aprovacao (tipo, alvo_tabela, alvo_id, justificativa, exige_individual,"
                " elegivel_lote, simulacao) VALUES (?,?,?,?,?,?,?)",
                (s.tarefa, "lancamentos", lid, f"L{ln.seq:02d} ({ln.data}): {s.justificativa}",
                 int(s.exige_individual), int(s.elegivel_lote and not s.exige_individual), int(simulacao)))
        if instrumento_id is None:
            con.execute("INSERT INTO tarefas_aprovacao (tipo, alvo_tabela, alvo_id, justificativa, simulacao)"
                        " VALUES ('VINCULAR_INSTRUMENTO','propostas',?,?,?)",
                        (proposta_id, "Vincular o extrato a um instrumento do cadastro (instituição não "
                                      "consta no documento ou não foi informada).", int(simulacao)))
        auditar(con, ator_id, "IMPORTACAO_PROPOSTA", "propostas", proposta_id,
                depois={"linhas": len(ex.linhas), "duplicadas": duplicados, "simulacao": simulacao},
                motivo="Proposta de extração; sem efeito econômico até decisão")
        con.commit()
        return {"estado": "PROCESSADO", "proposta_id": proposta_id, "linhas": len(ex.linhas),
                "duplicadas": duplicados, "alertas": proposta["alertas"]}
    except Exception as e:  # noqa: BLE001 — qualquer falha vira quarentena, sem gravação parcial
        con.rollback()
        if isinstance(e, Recusado):
            raise
        con.execute("INSERT INTO fontes_documentais (id, nome_seguro, tipo, origem_uri, hash_sha256)"
                    " VALUES (?,?,?,?,?)", (fonte_id, "quarentena", "DESCONHECIDO", nome_original, hash_))
        con.execute("INSERT INTO lotes_importacao (id, fonte_id, estado, erro, simulacao) VALUES (?,?,?,?,?)",
                    (lote_id, fonte_id, "QUARENTENA", str(e), int(simulacao)))
        auditar(con, ator_id, "IMPORTACAO_QUARENTENA", "lotes_importacao", lote_id, depois={"erro": str(e)})
        con.commit()
        return {"estado": "QUARENTENA", "lote_id": lote_id, "erro": str(e),
                "conferencia": isinstance(e, ErroConferencia)}


# ---------------------------------------------------------------- aprovação

def _exigir_aprovador(con, ator_id):
    u = linha(con, "SELECT papel FROM usuarios WHERE id=?", ator_id)
    if not u or u["papel"] != "APROVADOR":
        raise Recusado("somente o APROVADOR decide; agente e administração técnica não aprovam")


# Catálogo de trabalho dos eventos não econômicos (03_EVENTOS_E_CLASSIFICACOES).
# Só eventos de movimentação de caixa sem resultado podem ser aprovados como neutros a partir de um extrato.
# Os demais dependem de vínculo/documento em módulos ainda não implementados e ficam em revisão.
EVENTOS_NEUTROS = {"TRANSFERENCIA_INTERNA", "APORTE_DOMESTICO", "PAGAMENTO_FATURA", "APLICACAO_PRINCIPAL",
                   "RESGATE_PRINCIPAL", "BAIXA_REEMBOLSO"}
EVENTOS_EXIGEM_VINCULO = {"COMPRA_CARTAO", "PARCELA_CARTAO", "FECHAMENTO_FATURA", "DESPESA_TERCEIRO",
                          "CREDITO_REEMBOLSO", "CONSTITUICAO_MUTUO", "AMORTIZACAO_PRINCIPAL", "QUITACAO_DIVIDA",
                          "AQUISICAO_ATIVO", "ALIENACAO_ATIVO", "REAVALIACAO_NAO_REALIZADA", "AJUSTE_ABERTURA",
                          "REFERENCIA_DECLARATORIA_SEM_EFEITO"}
CLASSES_VALIDAS = {f"01.{i:02d}" for i in range(1, 9)} | {f"02.{i:02d}" for i in range(1, 20)}


def _codigo_classe(classe):
    m = re.match(r"\s*(0[12]\.\d{2})\b", classe or "")
    return m.group(1) if m else None


def _efeito(evento, classe):
    """Efeito econômico de uma aprovação. Nada desconhecido é tratado como neutro: recusa e mantém em revisão."""
    if evento and classe:
        raise Recusado("informe evento OU classe, não ambos")
    if evento:
        if evento in EVENTOS_NEUTROS:
            return "NENHUM"
        if evento in EVENTOS_EXIGEM_VINCULO:
            raise Recusado(f"evento {evento} exige vínculo com documento ou módulo ainda não implementado; "
                           "a tarefa continua em revisão")
        raise Recusado(f"evento {evento} fora do catálogo: exige revisão e não é considerado neutro")
    if classe:
        codigo = _codigo_classe(classe)
        if codigo not in CLASSES_VALIDAS:
            raise Recusado(f"classe '{classe}' fora do catálogo 01/02: exige revisão")
        return "RECEITA" if codigo.startswith("01") else "DESPESA"
    raise Recusado("informe evento ou classe para aprovar este lançamento")


def decidir(con, tarefa_id, ator_id, decisao, motivo, dados=None):
    try:
        return _decidir(con, tarefa_id, ator_id, decisao, motivo, dados)
    except Exception:
        con.rollback()  # nenhuma decisão parcial
        raise


def _decidir(con, tarefa_id, ator_id, decisao, motivo, dados=None):
    _exigir_aprovador(con, ator_id)
    if decisao not in ("APROVAR", "REJEITAR", "CONTESTAR"):
        raise Recusado("decisão deve ser APROVAR, REJEITAR ou CONTESTAR")
    if not motivo or not motivo.strip():
        raise Recusado("motivo é obrigatório")
    t = linha(con, "SELECT * FROM tarefas_aprovacao WHERE id=?", tarefa_id)
    if not t:
        raise Recusado("tarefa inexistente")
    if t["estado"] != "ABERTA":
        raise Recusado("tarefa já decidida; use reabertura")
    dados = dados or {}

    cur = con.execute("INSERT INTO decisoes (tarefa_id, ator_id, decisao, motivo, dados_json) VALUES (?,?,?,?,?)",
                      (tarefa_id, ator_id, decisao, motivo, json.dumps(dados, ensure_ascii=False)))
    decisao_id = cur.lastrowid
    con.execute("UPDATE tarefas_aprovacao SET estado='DECIDIDA' WHERE id=?", (tarefa_id,))

    if t["simulacao"]:
        auditar(con, ator_id, "DECISAO_SIMULADA_SEM_EFEITO", t["alvo_tabela"], t["alvo_id"],
                depois={"decisao": decisao, "dados": dados}, motivo=motivo)
        con.commit()
        return {"decisao_id": decisao_id, "efeito": "NENHUM (simulação)"}

    if decisao == "APROVAR":
        _aplicar(con, t, ator_id, motivo, dados, decisao_id)
    elif t["alvo_tabela"] == "lancamentos":
        antes = linha(con, "SELECT estado_aprovacao FROM lancamentos WHERE id=?", t["alvo_id"])
        novo = "REJEITADO" if decisao == "REJEITAR" else "CONTESTACAO"
        con.execute("UPDATE lancamentos SET estado_aprovacao=? WHERE id=?", (novo, t["alvo_id"]))
        auditar(con, ator_id, decisao, "lancamentos", t["alvo_id"], antes, {"estado_aprovacao": novo}, motivo)
    else:
        auditar(con, ator_id, decisao, t["alvo_tabela"], t["alvo_id"], None, {"decisao": decisao}, motivo)
    con.commit()
    return {"decisao_id": decisao_id}


def _aplicar(con, t, ator_id, motivo, dados, decisao_id):
    tipo, alvo = t["tipo"], t["alvo_id"]
    if tipo == "IF09_ATRIBUICAO_ECONOMICA":
        aloc = dados.get("alocacoes") or []
        inicio = dados.get("vigencia_inicio")
        if not aloc or not inicio:
            raise Recusado("IF09 exige alocações por pessoa e data de início de vigência")
        _validar_data(inicio, "data de início da vigência")
        if dados.get("vigencia_fim"):
            _validar_data(dados["vigencia_fim"], "data de fim da vigência")
        aloc = [{"pessoa_id": str(a["pessoa_id"]).strip().upper(), "percentual": float(a["percentual"])}
                for a in aloc]
        ids = [a["pessoa_id"] for a in aloc]
        if len(set(ids)) != len(ids):
            raise Recusado("IF09: a mesma pessoa aparece mais de uma vez nas alocações")
        desconhecidas = [i for i in ids if not linha(con, "SELECT 1 FROM pessoas_entidades WHERE id=?", i)]
        if desconhecidas:
            raise Recusado(f"IF09: pessoa não cadastrada: {', '.join(desconhecidas)}. "
                           f"Use os códigos do cadastro ({titulares_sugeridos(con, alvo)})")
        if any(not 0 <= a["percentual"] <= 100 for a in aloc):
            raise Recusado("IF09: cada percentual deve estar entre 0 e 100")
        if round(sum(a["percentual"] for a in aloc), 6) != 100:
            raise Recusado("IF09: a soma dos percentuais deve ser 100")
        antes = linha(con, "SELECT titularidade_economica_status FROM instrumentos WHERE id=?", alvo)
        for a in aloc:
            con.execute("INSERT INTO tit_economica_vigencias (instrumento_id, pessoa_id, percentual,"
                        " vigencia_inicio, vigencia_fim, decisao_id) VALUES (?,?,?,?,?,?)",
                        (alvo, a["pessoa_id"], a["percentual"], inicio, dados.get("vigencia_fim"), decisao_id))
        con.execute("UPDATE instrumentos SET titularidade_economica_status='VALIDADA_DANILO' WHERE id=?", (alvo,))
        auditar(con, ator_id, "IF09_VALIDACAO", "instrumentos", alvo, antes,
                {"alocacoes": aloc, "vigencia_inicio": inicio}, motivo)
    elif tipo == "DG08_SOMENTE_CONTABIL":
        antes = linha(con, "SELECT estado FROM referencias_declaratorias WHERE id=?", alvo)
        con.execute("UPDATE referencias_declaratorias SET estado='SOMENTE_CONTABIL_APROVADO_REVISAO_FISCAL_PENDENTE'"
                    " WHERE id=?", (alvo,))
        auditar(con, ator_id, "DG08_CLASSIFICACAO", "referencias_declaratorias", alvo, antes,
                {"estado": "SOMENTE_CONTABIL_APROVADO_REVISAO_FISCAL_PENDENTE"}, motivo)
    elif t["alvo_tabela"] == "lancamentos":
        l = linha(con, "SELECT * FROM lancamentos WHERE id=?", alvo)
        evento = dados.get("evento", l["evento_sugerido"])
        classe = dados.get("classe", l["classe_sugerida"])
        if "evento" in dados or "classe" in dados:      # escolha explícita substitui toda a sugestão
            evento, classe = dados.get("evento") or None, dados.get("classe") or None
        efeito = _efeito(evento, classe)
        if efeito == "RECEITA" and l["valor"] <= 0:
            raise Recusado("receita exige entrada positiva")
        antes = {k: l[k] for k in ("evento", "classe", "efeito_economico", "estado_aprovacao")}
        depois = {"evento": evento, "classe": classe, "efeito_economico": efeito, "estado_aprovacao": "APROVADO"}
        con.execute("UPDATE lancamentos SET evento=?, classe=?, efeito_economico=?, estado_classificacao='CLASSIFICADO',"
                    " estado_aprovacao='APROVADO' WHERE id=?", (evento, classe, efeito, alvo))
        auditar(con, ator_id, "APROVACAO_LANCAMENTO", "lancamentos", alvo, antes, depois, motivo)
    elif tipo == "VINCULAR_INSTRUMENTO":
        inst = dados.get("instrumento_id")
        if not inst or not linha(con, "SELECT id FROM instrumentos WHERE id=?", inst):
            raise Recusado("informe um instrumento_id existente")
        con.execute("UPDATE lancamentos SET instrumento_id=? WHERE proposta_id=?", (inst, alvo))
        auditar(con, ator_id, "VINCULO_INSTRUMENTO", "propostas", alvo, None, {"instrumento_id": inst}, motivo)
    else:
        auditar(con, ator_id, "APROVAR", t["alvo_tabela"], alvo, None, dados, motivo)


def _validar_data(valor, nome):
    try:
        date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise Recusado(f"{nome} inválida: use o formato AAAA-MM-DD") from None


def titulares_sugeridos(con, instrumento_id):
    """Titulares relatados da conta com seus códigos de cadastro, ex.: 'P001 Ana, P002 Bruno'."""
    inst = linha(con, "SELECT titularidade_legal_relato FROM instrumentos WHERE id=?", instrumento_id)
    nomes = [n.strip() for n in ((inst and inst["titularidade_legal_relato"]) or "").split(";") if n.strip()]
    partes = []
    for n in nomes:
        p = linha(con, "SELECT id FROM pessoas_entidades WHERE lower(nome)=lower(?)", n)
        partes.append(f"{p['id']} {n}" if p else n)
    return ", ".join(partes) or "consulte Cadastros"


def aprovar_lote(con, tarefa_ids, ator_id, motivo):
    """Aprovação em lote só para tarefas elegíveis. DG08/IF09/empresa relacionada nunca entram (T15)."""
    _exigir_aprovador(con, ator_id)
    inelegiveis = [i for i in tarefa_ids if not linha(
        con, "SELECT 1 FROM tarefas_aprovacao WHERE id=? AND elegivel_lote=1 AND exige_individual=0"
             " AND estado='ABERTA'", i)]
    if inelegiveis:
        raise Recusado(f"tarefas não elegíveis para lote: {inelegiveis}")
    return [decidir(con, i, ator_id, "APROVAR", motivo) for i in tarefa_ids]


def reabrir_lancamento(con, lancamento_id, ator_id, motivo):
    """T18: revisão de dado aprovado exige autoridade, motivo e nova decisão, com trilha."""
    _exigir_aprovador(con, ator_id)
    if not motivo or not motivo.strip():
        raise Recusado("motivo é obrigatório")
    l = linha(con, "SELECT * FROM lancamentos WHERE id=?", lancamento_id)
    if not l or l["estado_aprovacao"] != "APROVADO":
        raise Recusado("só lançamentos aprovados podem ser reabertos")
    con.execute("UPDATE lancamentos SET estado_aprovacao='CONTESTACAO' WHERE id=?", (lancamento_id,))
    cur = con.execute("INSERT INTO tarefas_aprovacao (tipo, alvo_tabela, alvo_id, justificativa, exige_individual)"
                      " VALUES ('REVISAO_APOS_APROVACAO','lancamentos',?,?,1)",
                      (lancamento_id, f"Reabertura: {motivo}"))
    auditar(con, ator_id, "REABERTURA", "lancamentos", lancamento_id,
            {"estado_aprovacao": "APROVADO", "evento": l["evento"], "classe": l["classe"]},
            {"estado_aprovacao": "CONTESTACAO"}, motivo)
    con.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- painel

def painel(con):
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    validados = q("SELECT count(*) FROM posicoes WHERE saldo_atual_validado=1")
    return {
        "patrimonio_liquido": "NAO_APURADO" if validados == 0 else "VER_POSICOES_VALIDADAS",
        "motivo_pl": "Sem saldos atuais validados; valores IRPF são abertura histórica (IF02 R01)."
                     if validados == 0 else "",
        "tarefas_abertas_reais": q("SELECT count(*) FROM tarefas_aprovacao WHERE estado='ABERTA' AND simulacao=0"),
        "tarefas_abertas_simulacao": q("SELECT count(*) FROM tarefas_aprovacao WHERE estado='ABERTA' AND simulacao=1"),
        "lotes_quarentena": q("SELECT count(*) FROM lotes_importacao WHERE estado='QUARENTENA'"),
        "contas_conjuntas_sem_validacao": q("SELECT count(*) FROM instrumentos WHERE conjunta='sim'"
                                            " AND titularidade_economica_status NOT LIKE 'VALIDADA%'"),
        "referencias_dg08_pendentes": q("SELECT count(*) FROM referencias_declaratorias"
                                        " WHERE estado='SOMENTE_CONTABIL_PENDENTE_APROVACAO'"),
        "dividas_historicas": [dict(r) for r in con.execute(
            "SELECT id, contraparte, valor_declarado, estado FROM referencias_declaratorias"
            " WHERE natureza='divida_historica'")],
    }
