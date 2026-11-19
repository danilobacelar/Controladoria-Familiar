"""Interface web com dados fictícios: autenticação por usuário, permissões por tela, CSRF e sessão."""
import importlib
import re
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
SENHAS = {"danilo": "senha-aprovador-1", "operador": "senha-operador-1"}
TELAS_SO_APROVADOR = ["/fila", "/fila?simulacao=1", "/lancamentos", "/cadastros", "/auditoria"]


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLADORIA_DB", str(tmp_path / "w.db"))
    from controladoria import app as modulo
    modulo = importlib.reload(modulo)
    from controladoria import cadastros
    from controladoria.autenticacao import definir_senha
    from controladoria.db import conectar, inicializar
    c = conectar(modulo.DB)
    inicializar(c)
    cadastros.carregar(c, FIX / "cadastros")
    for uid, s in SENHAS.items():
        definir_senha(c, uid, s)
    c.close()
    modulo.app.config["TESTING"] = True
    return modulo


def csrf(cl, pagina="/entrar"):
    return re.search(r'name="csrf" value="([^"]+)"', cl.get(pagina).get_data(as_text=True)).group(1)


def entrar(cl, uid, senha):
    return cl.post("/entrar", data={"uid": uid, "senha": senha, "csrf": csrf(cl)}, follow_redirects=True)


def importar_pdf(cl):
    with open(FIX / "extrato_ficticio.pdf", "rb") as f:
        return cl.post("/importar", data={"arquivo": (f, "extrato.pdf"), "instrumento_id": "A001",
                                          "csrf": csrf(cl, "/importar")},
                       content_type="multipart/form-data", follow_redirects=True)


def banco(modulo):
    from controladoria.db import conectar
    return conectar(modulo.DB)


# ------------------------------------------------------------ autenticação

def test_sem_login_tudo_redireciona(ambiente):
    cl = ambiente.app.test_client()
    for p in ["/", "/importar", "/propostas/x"] + TELAS_SO_APROVADOR:
        assert cl.get(p).status_code == 302


def test_operador_precisa_da_propria_senha(ambiente):
    cl = ambiente.app.test_client()
    assert "inválidos" in entrar(cl, "operador", "").get_data(as_text=True)
    assert "inválidos" in entrar(cl, "operador", SENHAS["danilo"]).get_data(as_text=True)
    assert cl.get("/").status_code == 302
    assert "OPERADOR" in entrar(cl, "operador", SENHAS["operador"]).get_data(as_text=True)


def test_usuario_sem_senha_definida_nao_entra(ambiente):
    c = banco(ambiente)
    c.execute("INSERT INTO usuarios (id, nome, papel) VALUES ('op2','Outro','OPERADOR')")
    c.commit()
    cl = ambiente.app.test_client()
    assert "inválidos" in entrar(cl, "op2", "").get_data(as_text=True)
    assert cl.get("/").status_code == 302


@pytest.mark.parametrize("uid", ["claude", "admin"])
def test_agente_e_admin_nunca_entram(ambiente, uid):
    cl = ambiente.app.test_client()
    entrar(cl, uid, "qualquer-senha")
    assert cl.get("/").status_code == 302


# ------------------------------------------------------------ permissões

def test_operador_nao_acessa_telas_restritas(ambiente):
    cl = ambiente.app.test_client()
    entrar(cl, "operador", SENHAS["operador"])
    for p in TELAS_SO_APROVADOR:
        assert cl.get(p).status_code == 403, p
    html = cl.get("/").get_data(as_text=True)
    for link in ("Fila de aprovação", "Lançamentos", "Cadastros", "Auditoria"):
        assert link not in html
    assert "Empresa Delta" not in html                     # dívida histórica oculta do operador
    t = csrf(cl, "/importar")
    assert cl.post("/fila/1", data={"decisao": "APROVAR", "motivo": "x", "csrf": t}).status_code == 403
    assert cl.post("/fila/lote", data={"ids": ["1"], "motivo": "x", "csrf": t}).status_code == 403
    assert cl.post("/lancamentos/x/reabrir", data={"motivo": "x", "csrf": t}).status_code == 403
    c = banco(ambiente)
    assert c.execute("SELECT count(*) FROM decisoes").fetchone()[0] == 0


def test_operador_importa_e_ve_so_a_propria_proposta(ambiente):
    op = ambiente.app.test_client()
    entrar(op, "operador", SENHAS["operador"])
    assert "Proposta criada com 8 linhas" in importar_pdf(op).get_data(as_text=True)
    c = banco(ambiente)
    propria = c.execute("SELECT id FROM propostas").fetchone()[0]
    assert op.get(f"/propostas/{propria}").status_code == 200
    c.execute("INSERT INTO fontes_documentais (id, nome_seguro, tipo) VALUES ('F-x','x','X')")
    c.execute("INSERT INTO lotes_importacao (id, fonte_id, estado) VALUES ('LT-x','F-x','PROCESSADO')")
    c.execute("INSERT INTO propostas (id, fonte_id, lote_id, status, json, criado_por)"
              " VALUES ('PR-outra','F-x','LT-x','PROPOSTA_NAO_GRAVADA','{}','danilo')")
    c.commit()
    assert op.get("/propostas/PR-outra").status_code == 403


# ------------------------------------------------------------ sessão e CSRF

def test_post_sem_csrf_e_recusado(ambiente):
    cl = ambiente.app.test_client()
    assert cl.post("/entrar", data={"uid": "danilo", "senha": SENHAS["danilo"]}).status_code == 400
    entrar(cl, "danilo", SENHAS["danilo"])
    assert cl.post("/fila/1", data={"decisao": "APROVAR", "motivo": "x"}).status_code == 400
    assert cl.post("/fila/1", data={"decisao": "APROVAR", "motivo": "x", "csrf": "forjado"}).status_code == 400


def test_cookie_e_cabecalhos_de_protecao(ambiente):
    cl = ambiente.app.test_client()
    r = entrar(cl, "danilo", SENHAS["danilo"])
    r = cl.post("/entrar", data={"uid": "danilo", "senha": SENHAS["danilo"], "csrf": csrf(cl)})
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    h = cl.get("/").headers
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert ambiente.app.permanent_session_lifetime.total_seconds() == 30 * 60


def test_sair_encerra_sessao(ambiente):
    cl = ambiente.app.test_client()
    entrar(cl, "danilo", SENHAS["danilo"])
    cl.post("/sair", data={"csrf": csrf(cl, "/")})
    assert cl.get("/").status_code == 302


# ------------------------------------------------------------ fluxo do aprovador

def test_fluxo_completo_do_aprovador(ambiente):
    cl = ambiente.app.test_client()
    assert "Não apurado" in entrar(cl, "danilo", SENHAS["danilo"]).get_data(as_text=True)
    html = importar_pdf(cl).get_data(as_text=True)
    assert "Proposta criada com 8 linhas" in html and "PROPOSTA_NAO_GRAVADA" in html
    c = banco(ambiente)
    tid = c.execute("SELECT id FROM tarefas_aprovacao WHERE tipo='CONFIRMAR_PAGAMENTO_FATURA'").fetchone()[0]
    dg08 = c.execute("SELECT id FROM tarefas_aprovacao WHERE tipo='DG08_SOMENTE_CONTABIL'").fetchone()[0]
    t = csrf(cl, "/fila")
    r = cl.post(f"/fila/{tid}", data={"decisao": "APROVAR", "motivo": "fatura conferida", "csrf": t},
                follow_redirects=True)
    assert "decisão registrada" in r.get_data(as_text=True)
    r = cl.post("/fila/lote", data={"ids": [str(dg08)], "motivo": "lote", "csrf": t}, follow_redirects=True)
    assert "Lote bloqueado" in r.get_data(as_text=True)
    for p in TELAS_SO_APROVADOR:
        assert cl.get(p).status_code == 200
    aud = cl.get("/auditoria").get_data(as_text=True)
    assert "APROVACAO_LANCAMENTO" in aud and "LOGIN" in aud
