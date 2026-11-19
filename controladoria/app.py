"""Interface web local (Flask). Rodar: python -m controladoria.app

Uso local, um computador. O APROVADOR entra com o PIN definido em CONTROLADORIA_PIN.
"""
from __future__ import annotations

import hmac
import json
import os
import tempfile
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from . import servicos
from .db import conectar, inicializar
from .servicos import Recusado

BASE = Path(os.environ.get("CONTROLADORIA_DADOS", Path(__file__).resolve().parent.parent / "dados"))
DB = Path(os.environ.get("CONTROLADORIA_DB", BASE / "controladoria.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("CONTROLADORIA_SECRET") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


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
    r = con().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def login_exigido(f):
    @wraps(f)
    def w(*a, **k):
        if not usuario():
            return redirect(url_for("entrar"))
        return f(*a, **k)
    return w


@app.context_processor
def _ctx():
    return {"u": usuario()}


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    usuarios = [dict(r) for r in con().execute("SELECT * FROM usuarios WHERE papel IN ('APROVADOR','OPERADOR')")]
    if request.method == "POST":
        uid = request.form.get("uid")
        u = con().execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        pin = os.environ.get("CONTROLADORIA_PIN", "")
        if not u or u["papel"] not in ("APROVADOR", "OPERADOR"):
            flash("Usuário inválido.")
        elif u["papel"] == "APROVADOR" and (not pin or not hmac.compare_digest(pin, request.form.get("pin", ""))):
            flash("PIN incorreto ou CONTROLADORIA_PIN não definido.")
        else:
            session["uid"] = uid
            return redirect(url_for("painel"))
    return render_template("entrar.html", usuarios=usuarios)


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("entrar"))


@app.route("/")
@login_exigido
def painel():
    return render_template("painel.html", p=servicos.painel(con()))


@app.route("/importar", methods=["GET", "POST"])
@login_exigido
def importar():
    instrumentos = [dict(r) for r in con().execute("SELECT id, instituicao, tipo FROM instrumentos ORDER BY id")]
    if request.method == "POST":
        arq = request.files.get("arquivo")
        if not arq or not arq.filename.lower().endswith(".pdf"):
            flash("Envie um PDF de extrato.")
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
                flash(f"Bloqueado: {e}")
                return redirect(url_for("importar"))
        if r["estado"] == "QUARENTENA":
            flash(f"Lote em QUARENTENA, nada foi lançado: {r['erro']}")
            return redirect(url_for("importar"))
        flash(f"Proposta criada com {r['linhas']} linhas ({r['duplicadas']} duplicadas ignoradas).")
        return redirect(url_for("proposta", pid=r["proposta_id"]))
    return render_template("importar.html", instrumentos=instrumentos)


@app.route("/propostas/<pid>")
@login_exigido
def proposta(pid):
    p = con().execute("SELECT * FROM propostas WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    lancs = [dict(r) for r in con().execute("SELECT * FROM lancamentos WHERE proposta_id=? ORDER BY data, id", (pid,))]
    return render_template("proposta.html", p=dict(p), js=json.dumps(json.loads(p["json"]), ensure_ascii=False,
                                                                       indent=1), lancs=lancs)


@app.route("/fila")
@login_exigido
def fila():
    sim = request.args.get("simulacao") == "1"
    tarefas = [dict(r) for r in con().execute(
        "SELECT t.*, l.data, l.valor, l.historico, l.contraparte, l.linha_origem, l.evento_sugerido,"
        " l.classe_sugerida, l.confianca FROM tarefas_aprovacao t LEFT JOIN lancamentos l"
        " ON t.alvo_tabela='lancamentos' AND l.id=t.alvo_id WHERE t.estado='ABERTA' AND t.simulacao=?"
        " ORDER BY t.exige_individual DESC, t.id", (int(sim),))]
    return render_template("fila.html", tarefas=tarefas, sim=sim)


@app.post("/fila/<int:tid>")
@login_exigido
def decidir(tid):
    f = request.form
    dados = {}
    for k in ("evento", "classe", "instrumento_id", "vigencia_inicio", "vigencia_fim"):
        if f.get(k):
            dados[k] = f[k].strip()
    if f.get("alocacoes"):
        try:
            dados["alocacoes"] = [{"pessoa_id": p.split("=")[0].strip(), "percentual": float(p.split("=")[1])}
                                  for p in f["alocacoes"].split(";") if p.strip()]
        except (IndexError, ValueError):
            flash("Alocações no formato P001=100;P002=0")
            return redirect(request.referrer or url_for("fila"))
    try:
        servicos.decidir(con(), tid, usuario()["id"], f.get("decisao"), f.get("motivo", ""), dados)
        flash(f"Tarefa {tid}: decisão registrada.")
    except Recusado as e:
        flash(f"Tarefa {tid} bloqueada: {e}")
    return redirect(request.referrer or url_for("fila"))


@app.post("/fila/lote")
@login_exigido
def lote():
    ids = [int(i) for i in request.form.getlist("ids")]
    try:
        servicos.aprovar_lote(con(), ids, usuario()["id"], request.form.get("motivo", ""))
        flash(f"{len(ids)} tarefas aprovadas em lote.")
    except Recusado as e:
        flash(f"Lote bloqueado: {e}")
    return redirect(request.referrer or url_for("fila"))


@app.post("/lancamentos/<lid>/reabrir")
@login_exigido
def reabrir(lid):
    try:
        servicos.reabrir_lancamento(con(), lid, usuario()["id"], request.form.get("motivo", ""))
        flash("Lançamento reaberto; nova tarefa criada.")
    except Recusado as e:
        flash(f"Bloqueado: {e}")
    return redirect(request.referrer or url_for("fila"))


@app.route("/cadastros")
@login_exigido
def cadastros_view():
    c = con()
    ver_restrito = usuario()["papel"] == "APROVADOR"
    return render_template(
        "cadastros.html",
        pessoas=[dict(r) for r in c.execute("SELECT * FROM pessoas_entidades ORDER BY id")] if ver_restrito else [],
        instrumentos=[dict(r) for r in c.execute("SELECT * FROM instrumentos ORDER BY id")],
        vigencias=[dict(r) for r in c.execute("SELECT * FROM tit_economica_vigencias ORDER BY instrumento_id")],
        posicoes=[dict(r) for r in c.execute("SELECT * FROM posicoes ORDER BY id")],
        ativos=[dict(r) for r in c.execute("SELECT * FROM ativos ORDER BY id")],
        referencias=[dict(r) for r in c.execute("SELECT * FROM referencias_declaratorias ORDER BY id")]
        if ver_restrito else [],
        ver_restrito=ver_restrito,
    )


@app.route("/lancamentos")
@login_exigido
def lancamentos():
    rows = [dict(r) for r in con().execute("SELECT * FROM lancamentos ORDER BY data DESC, id LIMIT 500")]
    return render_template("lancamentos.html", lancs=rows)


@app.route("/auditoria")
@login_exigido
def auditoria():
    rows = [dict(r) for r in con().execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT 300")]
    return render_template("auditoria.html", rows=rows)


if __name__ == "__main__":
    BASE.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8765)), debug=False)
