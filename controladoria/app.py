"""Interface web local (Flask). Rodar: python -m controladoria.app

Uso local, um computador. Cada usuário entra com a própria senha (cli definir-senha).
Permissões por tela em PERMISSOES; o serviço de decisão revalida o papel de quem decide.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from . import servicos
from .autenticacao import FalhaAutenticacao, autenticar
from .db import conectar, inicializar
from .servicos import Recusado

BASE = Path(os.environ.get("CONTROLADORIA_DADOS", Path(__file__).resolve().parent.parent / "dados"))
DB = Path(os.environ.get("CONTROLADORIA_DB", BASE / "controladoria.db"))

app = Flask(__name__)
# Chave nova a cada início: sessões antigas deixam de valer quando o servidor reinicia.
app.secret_key = os.environ.get("CONTROLADORIA_SECRET") or secrets.token_bytes(32)
app.config.update(
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_NAME="controladoria_sessao",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),   # expira após 30 min sem uso
    SESSION_REFRESH_EACH_REQUEST=True,
)

# Matriz de acesso por tela. Tudo o que não estiver aqui exige APROVADOR.
APROVADOR, OPERADOR = "APROVADOR", "OPERADOR"
PERMISSOES = {
    "painel": {APROVADOR, OPERADOR},
    "importar": {APROVADOR, OPERADOR},
    "proposta": {APROVADOR, OPERADOR},      # OPERADOR: só as propostas que ele mesmo importou
    "fila": {APROVADOR},
    "decidir": {APROVADOR},
    "lote": {APROVADOR},
    "reabrir": {APROVADOR},
    "lancamentos": {APROVADOR},
    "cadastros_view": {APROVADOR},
    "auditoria": {APROVADOR},
}


def con():
    if "con" not in g:
        g.con = conectar(DB)
        inicializar(g.con)
    return g.con


@app.teardown_appcontext
def _fechar(_):
    c = g.pop("con", None)
    if c:
        c.close()


def usuario():
    uid = session.get("uid")
    if not uid:
        return None
    r = con().execute("SELECT id, nome, papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not r or r["papel"] not in (APROVADOR, OPERADOR):
        session.clear()
        return None
    return dict(r)


def acesso(f):
    """Exige sessão válida e papel autorizado para a tela (PERMISSOES)."""
    @wraps(f)
    def w(*a, **k):
        u = usuario()
        if not u:
            return redirect(url_for("entrar"))
        if u["papel"] not in PERMISSOES.get(f.__name__, {APROVADOR}):
            abort(403)
        return f(*a, **k)
    return w


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def _protecoes():
    session.permanent = True
    if request.method == "POST":
        enviado = request.form.get("csrf", "")
        if not enviado or not hmac.compare_digest(enviado, session.get("csrf", "")):
            abort(400, "Formulário expirado ou inválido. Recarregue a página.")


@app.after_request
def _cabecalhos(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
        "frame-ancestors 'none'")
    return resp


@app.context_processor
def _ctx():
    u = usuario()
    return {"u": u, "csrf": _csrf_token(),
            "pode": lambda tela: bool(u) and u["papel"] in PERMISSOES.get(tela, {APROVADOR})}


@app.errorhandler(403)
def _proibido(_):
    return render_template("erro.html", titulo="Acesso negado", msg="Seu papel não tem acesso a esta tela."), 403


@app.errorhandler(500)
def _falha(_):
    return render_template("erro.html", titulo="Erro inesperado",
                           msg="A operação foi interrompida e nada foi gravado. Volte à tela anterior e tente de novo; "
                               "se repetir, anote o que foi digitado e informe."), 500


ERRO_INESPERADO = "erro inesperado; nada foi gravado. Confira os dados digitados e tente de novo."


def _falha_inesperada(contexto):
    """Qualquer erro não previsto vira mensagem na tela, com desfazimento, em vez de derrubar a página."""
    logging.getLogger(__name__).exception("falha em %s", contexto)
    con().rollback()


def _percentual(txt):
    return float(txt.strip().rstrip("%").strip().replace(",", "."))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    if request.method == "POST":
        try:
            u = autenticar(con(), request.form.get("uid", ""), request.form.get("senha", ""))
        except FalhaAutenticacao as e:
            flash(str(e).capitalize() + ".", "erro")
        else:
            session.clear()                     # nova sessão a cada login
            session["uid"] = u["id"]
            return redirect(url_for("painel"))
    return render_template("entrar.html")


@app.post("/sair")
def sair():
    session.clear()
    return redirect(url_for("entrar"))


@app.route("/")
@acesso
def painel():
    p = servicos.painel(con())
    if usuario()["papel"] != APROVADOR:
        p["dividas_historicas"] = []            # valores e contrapartes só para o aprovador
    return render_template("painel.html", p=p)


@app.route("/importar", methods=["GET", "POST"])
@acesso
def importar():
    instrumentos = [dict(r) for r in con().execute("SELECT id, instituicao, tipo FROM instrumentos ORDER BY id")]
    if request.method == "POST":
        arq = request.files.get("arquivo")
        if not arq or not arq.filename.lower().endswith(".pdf"):
            flash("Envie um PDF de extrato.", "erro")
            return redirect(url_for("importar"))
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "doc.pdf"
            arq.save(caminho)
            try:
                r = servicos.importar_extrato(con(), ator_id=usuario()["id"], caminho=caminho,
                                              nome_original=arq.filename,
                                              instrumento_id=request.form.get("instrumento_id") or None,
                                              simulacao=bool(request.form.get("simulacao")))
            except Recusado as e:
                flash(f"Bloqueado: {e}", "erro")
                return redirect(url_for("importar"))
        if r["estado"] == "QUARENTENA":
            flash(f"Lote em QUARENTENA, nada foi lançado: {r['erro']}", "erro")
            return redirect(url_for("importar"))
        flash(f"Proposta criada com {r['linhas']} linhas ({r['duplicadas']} duplicadas ignoradas).", "ok")
        return redirect(url_for("proposta", pid=r["proposta_id"]))
    return render_template("importar.html", instrumentos=instrumentos)


@app.route("/propostas/<pid>")
@acesso
def proposta(pid):
    p = con().execute("SELECT * FROM propostas WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    u = usuario()
    if u["papel"] != APROVADOR and p["criado_por"] != u["id"]:
        abort(403)
    lancs = [dict(r) for r in con().execute("SELECT * FROM lancamentos WHERE proposta_id=? ORDER BY data, id", (pid,))]
    return render_template("proposta.html", p=dict(p), js=json.dumps(json.loads(p["json"]), ensure_ascii=False,
                                                                       indent=1), lancs=lancs)


@app.route("/fila")
@acesso
def fila():
    sim = request.args.get("simulacao") == "1"
    tarefas = [dict(r) for r in con().execute(
        "SELECT t.*, l.data, l.valor, l.historico, l.contraparte, l.linha_origem, l.evento_sugerido,"
        " l.classe_sugerida, l.confianca FROM tarefas_aprovacao t LEFT JOIN lancamentos l"
        " ON t.alvo_tabela='lancamentos' AND l.id=t.alvo_id WHERE t.estado='ABERTA' AND t.simulacao=?"
        " ORDER BY t.exige_individual DESC, t.id", (int(sim),))]
    for t in tarefas:
        if t["tipo"] == "IF09_ATRIBUICAO_ECONOMICA":
            t["titulares"] = servicos.titulares_sugeridos(con(), t["alvo_id"])
            codigos = [x.split()[0] for x in t["titulares"].split(", ") if len(x.split()) > 1]
            t["exemplo"] = ";".join(f"{c}={100 if i == 0 else 0}" for i, c in enumerate(codigos))
    lote = [t for t in tarefas if t["elegivel_lote"] and not t["exige_individual"]]
    return render_template("fila.html", tarefas=tarefas, lote=lote, sim=sim)


@app.post("/fila/<int:tid>")
@acesso
def decidir(tid):
    f = request.form
    volta = redirect(url_for("fila", simulacao=f.get("simulacao") or None))
    dados = {}
    for k in ("evento", "classe", "instrumento_id", "vigencia_inicio", "vigencia_fim"):
        if f.get(k):
            dados[k] = f[k].strip()
    if f.get("alocacoes"):
        try:
            dados["alocacoes"] = [{"pessoa_id": p.split("=")[0].strip().upper(), "percentual": _percentual(p.split("=")[1])}
                                  for p in f["alocacoes"].split(";") if p.strip()]
        except (IndexError, ValueError):
            flash(f"Tarefa {tid} bloqueada: alocações devem seguir o formato P001=100;P002=0", "erro")
            return volta
    try:
        servicos.decidir(con(), tid, usuario()["id"], f.get("decisao"), f.get("motivo", ""), dados)
        flash(f"Tarefa {tid}: decisão registrada ({f.get('decisao')}).", "ok")
    except Recusado as e:
        flash(f"Tarefa {tid} bloqueada, nada foi registrado e ela continua pendente: {e}", "erro")
    except Exception:  # noqa: BLE001
        _falha_inesperada(f"decidir {tid}")
        flash(f"Tarefa {tid}: {ERRO_INESPERADO}", "erro")
    return volta


@app.post("/fila/lote")
@acesso
def lote():
    try:
        ids = [int(i) for i in request.form.getlist("ids")]
        if not ids:
            raise Recusado("nenhuma tarefa marcada")
        servicos.aprovar_lote(con(), ids, usuario()["id"], request.form.get("motivo", ""))
        flash(f"{len(ids)} tarefas aprovadas em lote.", "ok")
    except Recusado as e:
        flash(f"Lote bloqueado, nada aprovado: {e}", "erro")
    except ValueError:
        flash("Lote bloqueado: seleção inválida.", "erro")
    except Exception:  # noqa: BLE001
        _falha_inesperada("lote")
        flash(f"Lote: {ERRO_INESPERADO}", "erro")
    return redirect(url_for("fila"))


@app.post("/lancamentos/<lid>/reabrir")
@acesso
def reabrir(lid):
    try:
        servicos.reabrir_lancamento(con(), lid, usuario()["id"], request.form.get("motivo", ""))
        flash("Lançamento reaberto; nova tarefa criada na fila.", "ok")
    except Recusado as e:
        flash(f"Bloqueado: {e}", "erro")
    except Exception:  # noqa: BLE001
        _falha_inesperada("reabrir")
        flash(f"Reabertura: {ERRO_INESPERADO}", "erro")
    return redirect(url_for("lancamentos"))


@app.route("/cadastros")
@acesso
def cadastros_view():
    c = con()
    return render_template(
        "cadastros.html",
        pessoas=[dict(r) for r in c.execute("SELECT * FROM pessoas_entidades ORDER BY id")],
        instrumentos=[dict(r) for r in c.execute("SELECT * FROM instrumentos ORDER BY id")],
        vigencias=[dict(r) for r in c.execute("SELECT * FROM tit_economica_vigencias ORDER BY instrumento_id")],
        posicoes=[dict(r) for r in c.execute("SELECT * FROM posicoes ORDER BY id")],
        ativos=[dict(r) for r in c.execute("SELECT * FROM ativos ORDER BY id")],
        referencias=[dict(r) for r in c.execute("SELECT * FROM referencias_declaratorias ORDER BY id")],
        ver_restrito=True,
    )


@app.route("/lancamentos")
@acesso
def lancamentos():
    rows = [dict(r) for r in con().execute("SELECT * FROM lancamentos ORDER BY data DESC, id LIMIT 500")]
    return render_template("lancamentos.html", lancs=rows)


@app.route("/auditoria")
@acesso
def auditoria():
    rows = [dict(r) for r in con().execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT 300")]
    return render_template("auditoria.html", rows=rows)


if __name__ == "__main__":
    BASE.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8765)), debug=False)
