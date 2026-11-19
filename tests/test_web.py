"""Fluxo web ponta a ponta com dados fictícios: entrar, importar PDF, decidir, auditar."""
import importlib
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLADORIA_DB", str(tmp_path / "w.db"))
    monkeypatch.setenv("CONTROLADORIA_PIN", "1234")
    from controladoria import app as modulo
    modulo = importlib.reload(modulo)
    from controladoria import cadastros
    from controladoria.db import conectar, inicializar
    c = conectar(modulo.DB)
    inicializar(c)
    cadastros.carregar(c, FIX / "cadastros")
    c.close()
    modulo.app.config["TESTING"] = True
    return modulo, modulo.app.test_client()


def test_fluxo_completo(cliente):
    modulo, cl = cliente
    assert cl.get("/").status_code == 302                                  # exige login
    r = cl.post("/entrar", data={"uid": "danilo", "pin": "errado"}, follow_redirects=True)
    assert "PIN incorreto" in r.get_data(as_text=True)
    r = cl.post("/entrar", data={"uid": "claude"}, follow_redirects=True)   # agente não entra
    assert "Usuário inválido" in r.get_data(as_text=True)
    cl.post("/entrar", data={"uid": "danilo", "pin": "1234"})
    assert "Não apurado" in cl.get("/").get_data(as_text=True)

    with open(FIX / "extrato_ficticio.pdf", "rb") as f:
        r = cl.post("/importar", data={"arquivo": (f, "extrato.pdf"), "instrumento_id": "A001"},
                    content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Proposta criada com 8 linhas" in html and "PROPOSTA_NAO_GRAVADA" in html

    from controladoria.db import conectar
    c = conectar(modulo.DB)
    tid = c.execute("SELECT id FROM tarefas_aprovacao WHERE tipo='CONFIRMAR_PAGAMENTO_FATURA'").fetchone()[0]
    dg08 = c.execute("SELECT id FROM tarefas_aprovacao WHERE tipo='DG08_SOMENTE_CONTABIL'").fetchone()[0]
    c.close()

    r = cl.post(f"/fila/{tid}", data={"decisao": "APROVAR", "motivo": "fatura conferida"}, follow_redirects=True)
    assert "decisão registrada" in r.get_data(as_text=True)
    r = cl.post("/fila/lote", data={"ids": [str(dg08)], "motivo": "lote"}, follow_redirects=True)
    assert "Lote bloqueado" in r.get_data(as_text=True)

    for pagina in ("/fila", "/fila?simulacao=1", "/lancamentos", "/cadastros", "/auditoria"):
        assert cl.get(pagina).status_code == 200
    assert "APROVACAO_LANCAMENTO" in cl.get("/auditoria").get_data(as_text=True)
