"""Carga dos cadastros preliminares (03_CADASTROS) em staging.

Regras aplicadas na carga:
- IF02 R01: posições do IRPF viram ABERTURA_HISTORICA_IRPF com alerta obrigatório.
- IF09: cada conta conjunta gera tarefa individual de validação econômica; nada é dividido.
- DG08: créditos meramente declaratórios viram referência sem efeito econômico + tarefa individual.
- IF06: dívida Posto Agudos fica como referência histórica com quitação apenas relatada.
A carga nunca sobrescreve registro existente (INSERT OR IGNORE) e é auditada.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .db import auditar

ALERTA_IF02 = (
    "IF02 R01: valor fiscal histórico (IRPF 31/12/2025). Atualizar por extrato atual; "
    "não é saldo disponível contemporâneo."
)

NUCLEO = {"titular", "conjuge", "filha", "filho"}



def _ler(caminho: Path):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _num(v):
    v = (v or "").strip()
    return float(v) if v else None


def garantir_usuarios(con, aprovador_nome="Danilo"):
    """Cria os usuários-base SEM senha: ninguém entra na interface até ter senha própria definida."""
    sql = "INSERT OR IGNORE INTO usuarios (id, nome, papel) VALUES (?,?,?)"
    con.execute(sql, ("danilo", aprovador_nome, "APROVADOR"))
    con.execute(sql, ("operador", "Operador", "OPERADOR"))
    con.execute(sql, ("claude", "Agente Claude", "AGENTE"))
    con.execute(sql, ("admin", "Administração técnica", "ADMIN_TECNICO"))


def _tarefa(con, tipo, tabela, alvo, justificativa, individual):
    existe = con.execute(
        "SELECT 1 FROM tarefas_aprovacao WHERE tipo=? AND alvo_tabela=? AND alvo_id=? AND simulacao=0",
        (tipo, tabela, alvo),
    ).fetchone()
    if not existe:
        con.execute(
            "INSERT INTO tarefas_aprovacao (tipo, alvo_tabela, alvo_id, justificativa, exige_individual, elegivel_lote)"
            " VALUES (?,?,?,?,?,0)",
            (tipo, tabela, alvo, justificativa, 1 if individual else 0),
        )


def carregar(con, pasta: str | Path, ator_id: str = "claude") -> dict:
    pasta = Path(pasta)
    garantir_usuarios(con)
    cont = {}

    for r in _ler(pasta / "pessoas.csv"):
        con.execute(
            "INSERT OR IGNORE INTO pessoas_entidades (id, tipo, nome, papel, nucleo_familiar, origem, observacoes)"
            " VALUES (?, 'PESSOA', ?, ?, ?, ?, ?)",
            (r["pessoa_id"], r["nome"], r["papel"], 1 if r["papel"] in NUCLEO else 0, r["origem"], r["observacoes"]),
        )
    emp = pasta / "empresas_relacionadas.csv"   # opcional: id,nome,alias
    for r in (_ler(emp) if emp.exists() else []):
        con.execute(
            "INSERT OR IGNORE INTO pessoas_entidades (id, tipo, nome, papel, alias, origem)"
            " VALUES (?, 'EMPRESA', ?, 'empresa_relacionada', ?, 'REGRA_APROVADA')",
            (r["id"], r["nome"], r["alias"]),
        )
    cont["pessoas"] = con.execute("SELECT count(*) FROM pessoas_entidades").fetchone()[0]

    for r in _ler(pasta / "instrumentos.csv"):
        con.execute(
            "INSERT OR IGNORE INTO instrumentos (id, instituicao, tipo, titularidade_legal_relato, conjunta,"
            " titularidade_economica_status, origem, observacoes) VALUES (?,?,?,?,?,?,?,?)",
            (r["instrumento_id"], r["instituicao"], r["tipo"], r["titulares_relatados"], r["conjunta"],
             r["validacao_economica"], r["origem"], r["observacoes"]),
        )
        if r["conjunta"].strip().lower() == "sim":
            _tarefa(con, "IF09_ATRIBUICAO_ECONOMICA", "instrumentos", r["instrumento_id"],
                    f"IF09: validar expressamente a proporção econômica de {r['instituicao']} "
                    f"({r['titulares_relatados']}) com data de vigência. Cotitularidade não basta; 50/50 não é presumido.",
                    individual=True)
    cont["instrumentos"] = con.execute("SELECT count(*) FROM instrumentos").fetchone()[0]

    for r in _ler(pasta / "posicoes_irpf_2025.csv"):
        con.execute(
            "INSERT OR IGNORE INTO posicoes (id, instituicao, produto, data_base, valor, natureza_valor,"
            " evidencia_estado, alerta_atualizacao, saldo_atual_validado, observacoes)"
            " VALUES (?,?,?,?,?,'ABERTURA_HISTORICA_IRPF',?,?,0,?)",
            (r["id_posicao"], r["instituicao"], r["produto"], r["data_base"], _num(r["valor_fiscal_brl"]),
             r["origem"], ALERTA_IF02, r["observacoes"]),
        )
    cont["posicoes"] = con.execute("SELECT count(*) FROM posicoes").fetchone()[0]

    for r in _ler(pasta / "bens_historicos.csv"):
        con.execute(
            "INSERT OR IGNORE INTO ativos (id, classe, descricao, matricula, valor_fiscal_declarado, data_base,"
            " origem, status_relato) VALUES (?,?,?,?,?,?,?,?)",
            (r["ativo_id"], r["tipo"], r["descricao"], r["matricula"] or None, _num(r["valor_fiscal_declarado_brl"]),
             r["data_base"], r["origem"], r["observacoes"]),
        )
    cont["ativos"] = con.execute("SELECT count(*) FROM ativos").fetchone()[0]

    for r in _ler(pasta / "direitos_obrigacoes_referencia.csv"):
        estado = r["estado"]
        if r["natureza"] == "divida_historica":
            estado = "QUITACAO_RELATADA_DOCUMENTOS_PENDENTES"  # IF06: nunca quitação aprovada nem dívida atual
        con.execute(
            "INSERT OR IGNORE INTO referencias_declaratorias (id, natureza, contraparte, valor_declarado, estado,"
            " evidencia, observacao) VALUES (?,?,?,?,?,?,?)",
            (r["referencia_id"], r["natureza"], r["contraparte"], _num(r["valor_declarado_2025_brl"]), estado,
             r["evidencia"], r["observacao"]),
        )
        if r["natureza"] == "direito_apenas_declaratorio":
            _tarefa(con, "DG08_SOMENTE_CONTABIL", "referencias_declaratorias", r["referencia_id"],
                    f"DG08: classificar referência IRPF {r['referencia_id']} como Somente Contábil. Decisão individual "
                    "e exclusiva do aprovador, com justificativa; encaminhar revisão fiscal.",
                    individual=True)
    cont["referencias"] = con.execute("SELECT count(*) FROM referencias_declaratorias").fetchone()[0]

    auditar(con, ator_id, "CARGA_CADASTROS_STAGING", "cadastros", str(pasta.name), depois=cont,
            motivo="Carga inicial dos cadastros preliminares em staging")
    con.commit()
    return cont
