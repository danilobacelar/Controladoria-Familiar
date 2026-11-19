"""Autenticação, efeito econômico de eventos, backup e migração — dados fictícios."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from controladoria import cadastros, servicos
from controladoria.autenticacao import MAX_FALHAS, FalhaAutenticacao, autenticar, definir_senha
from controladoria.db import conectar, inicializar
from controladoria.servicos import Recusado

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def con(tmp_path):
    c = conectar(tmp_path / "s.db")
    inicializar(c)
    cadastros.carregar(c, FIX / "cadastros")
    return c


# ------------------------------------------------------------ autenticação

def test_senha_guardada_so_como_hash(con):
    definir_senha(con, "operador", "senha-segura-1")
    h = con.execute("SELECT senha_hash FROM usuarios WHERE id='operador'").fetchone()[0]
    assert h and "senha-segura-1" not in h


def test_senha_curta_e_papeis_sem_interface_recusados(con):
    with pytest.raises(FalhaAutenticacao):
        definir_senha(con, "operador", "curta")
    for uid in ("claude", "admin"):
        with pytest.raises(FalhaAutenticacao):
            definir_senha(con, uid, "senha-longa-1")


def test_operador_sem_senha_nao_autentica(con):
    with pytest.raises(FalhaAutenticacao):
        autenticar(con, "operador", "")
    with pytest.raises(FalhaAutenticacao):
        autenticar(con, "operador", "qualquer-coisa")


def test_bloqueio_apos_tentativas_e_liberacao(con):
    definir_senha(con, "danilo", "senha-aprovador-1")
    agora = datetime(2026, 11, 19, 20, 0)
    for _ in range(MAX_FALHAS):
        with pytest.raises(FalhaAutenticacao):
            autenticar(con, "danilo", "errada", agora)
    with pytest.raises(FalhaAutenticacao, match="bloqueado"):
        autenticar(con, "danilo", "senha-aprovador-1", agora + timedelta(minutes=5))
    u = autenticar(con, "danilo", "senha-aprovador-1", agora + timedelta(minutes=16))
    assert u["papel"] == "APROVADOR"
    acoes = [r[0] for r in con.execute("SELECT acao FROM auditoria WHERE tabela='usuarios'")]
    assert acoes.count("LOGIN_RECUSADO") == MAX_FALHAS + 1 and "LOGIN" in acoes


# ------------------------------------------------------------ efeito econômico (_efeito)

@pytest.fixture
def lanc(con):
    servicos.importar_extrato(con, ator_id="claude", instrumento_id="A001",
                              texto=(FIX / "extrato_ficticio.txt").read_text(encoding="utf-8"))
    return dict(con.execute("SELECT * FROM tarefas_aprovacao WHERE tipo='CLASSIFICAR_SAIDA'").fetchone())


def _estado(con, t):
    tarefa = con.execute("SELECT estado FROM tarefas_aprovacao WHERE id=?", (t["id"],)).fetchone()[0]
    l = con.execute("SELECT estado_aprovacao, efeito_economico FROM lancamentos WHERE id=?",
                    (t["alvo_id"],)).fetchone()
    return tarefa, l["estado_aprovacao"], l["efeito_economico"]


@pytest.mark.parametrize("dados", [
    {"evento": "EVENTO_INVENTADO"},                    # fora do catálogo
    {"evento": "QUITACAO_DIVIDA"},                     # exige vínculo (protege a dívida histórica)
    {"evento": "CREDITO_REEMBOLSO"},                   # exige vínculo com a despesa original
    {"classe": "99.99 qualquer"},                      # classe fora do catálogo
    {"classe": "02.03 saúde", "evento": "TRANSFERENCIA_INTERNA"},   # ambíguo
    {"evento": "", "classe": ""},                      # nada informado
])
def test_evento_ou_classe_desconhecidos_nao_sao_neutros(con, lanc, dados):
    with pytest.raises(Recusado):
        servicos.decidir(con, lanc["id"], "danilo", "APROVAR", "teste", dados)
    assert _estado(con, lanc) == ("ABERTA", "PENDENTE", "INDEFINIDO")
    assert con.execute("SELECT count(*) FROM decisoes").fetchone()[0] == 0


def test_classe_do_catalogo_gera_despesa(con, lanc):
    servicos.decidir(con, lanc["id"], "danilo", "APROVAR", "loja de consumo", {"classe": "02.09"})
    assert _estado(con, lanc) == ("DECIDIDA", "APROVADO", "DESPESA")


def test_evento_neutro_do_catalogo(con, lanc):
    servicos.decidir(con, lanc["id"], "danilo", "APROVAR", "aplicação", {"evento": "APLICACAO_PRINCIPAL"})
    assert _estado(con, lanc)[2] == "NENHUM"


# ------------------------------------------------------------ backup e migração

def test_backup_consistente_e_auditado(con, tmp_path):
    from controladoria.cli import backup
    r = backup(con, tmp_path / "bk")
    assert r["integridade"] == "ok"
    copia = sqlite3.connect(r["backup"])
    assert copia.execute("SELECT count(*) FROM instrumentos").fetchone()[0] == 3
    copia.close()
    assert con.execute("SELECT count(*) FROM auditoria WHERE acao='BACKUP'").fetchone()[0] == 1


def test_migracao_de_banco_antigo(tmp_path):
    antigo = sqlite3.connect(tmp_path / "antigo.db")
    antigo.execute("CREATE TABLE usuarios (id TEXT PRIMARY KEY, nome TEXT NOT NULL, papel TEXT NOT NULL)")
    antigo.execute("INSERT INTO usuarios VALUES ('danilo','Danilo','APROVADOR')")
    antigo.commit()
    antigo.close()
    c = conectar(tmp_path / "antigo.db")
    inicializar(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(usuarios)")}
    assert {"senha_hash", "falhas_login", "bloqueado_ate"} <= cols
    with pytest.raises(FalhaAutenticacao):
        autenticar(c, "danilo", "")


# ------------------------------------------------------------ compatibilidade do hash de senha (macOS)

def test_hash_pbkdf2_sha256_com_um_milhao_de_iteracoes(con):
    definir_senha(con, "danilo", "senha-aprovador-1")
    h = con.execute("SELECT senha_hash FROM usuarios WHERE id='danilo'").fetchone()[0]
    metodo, sal, digest = h.split("$")
    assert metodo == "pbkdf2:sha256:1000000" and len(sal) >= 16 and len(digest) == 64


def test_funciona_sem_hashlib_scrypt(con, monkeypatch):
    """Python da Apple (LibreSSL) não tem hashlib.scrypt: definir e verificar senha precisam funcionar."""
    import hashlib
    monkeypatch.delattr(hashlib, "scrypt", raising=False)
    definir_senha(con, "danilo", "senha-aprovador-1")
    assert autenticar(con, "danilo", "senha-aprovador-1")["papel"] == "APROVADOR"
    with pytest.raises(FalhaAutenticacao):
        autenticar(con, "danilo", "senha-errada-1")


def test_hash_antigo_com_algoritmo_indisponivel_falha_com_orientacao(con, monkeypatch):
    import hashlib
    monkeypatch.delattr(hashlib, "scrypt", raising=False)
    con.execute("UPDATE usuarios SET senha_hash='scrypt:32768:8:1$abc$def' WHERE id='danilo'")
    con.commit()
    with pytest.raises(FalhaAutenticacao, match="redefina"):
        autenticar(con, "danilo", "qualquer")


def test_verificacao_de_ambiente():
    from controladoria.verificar_ambiente import verificar
    assert verificar(completo=True) == []
    assert any("mínimo 3.10" in e for e in verificar(versao=(3, 9, 6)))
