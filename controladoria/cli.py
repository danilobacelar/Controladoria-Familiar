"""Linha de comando (sem poder de decisão).

  python -m controladoria.cli iniciar
  python -m controladoria.cli carregar dados/cadastros
  python -m controladoria.cli importar caminho/extrato.pdf --instrumento A001 [--simulacao]
  python -m controladoria.cli painel
  python -m controladoria.cli definir-senha danilo        (pede a senha sem mostrar na tela)
  python -m controladoria.cli backup dados/backups        (cópia consistente + verificação de integridade)

Decisões só pela interface web, autenticada como APROVADOR.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import cadastros, servicos
from .app import DB
from .autenticacao import FalhaAutenticacao, definir_senha
from .db import auditar
from .db import conectar, inicializar


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controladoria")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("iniciar")
    c = sub.add_parser("carregar"); c.add_argument("pasta")
    i = sub.add_parser("importar"); i.add_argument("pdf"); i.add_argument("--instrumento")
    i.add_argument("--simulacao", action="store_true"); i.add_argument("--ator", default="claude")
    sub.add_parser("painel")
    d = sub.add_parser("definir-senha"); d.add_argument("usuario")
    d.add_argument("--stdin", action="store_true", help="lê a senha da entrada padrão (scripts)")
    b = sub.add_parser("backup"); b.add_argument("pasta")
    a = ap.parse_args(argv)

    DB.parent.mkdir(parents=True, exist_ok=True)
    con = conectar(DB)
    inicializar(con)
    cadastros.garantir_usuarios(con)
    con.commit()
    if a.cmd == "carregar":
        r = cadastros.carregar(con, a.pasta)
    elif a.cmd == "importar":
        r = servicos.importar_extrato(con, ator_id=a.ator, caminho=a.pdf, instrumento_id=a.instrumento,
                                      simulacao=a.simulacao)
    elif a.cmd == "definir-senha":
        senha = sys.stdin.readline().rstrip("\n") if a.stdin else getpass.getpass("Nova senha: ")
        if not a.stdin and senha != getpass.getpass("Repita a senha: "):
            sys.exit("As senhas não conferem.")
        try:
            definir_senha(con, a.usuario, senha)
        except FalhaAutenticacao as e:
            sys.exit(f"Erro: {e}")
        r = {"usuario": a.usuario, "senha": "definida"}
    elif a.cmd == "backup":
        r = backup(con, Path(a.pasta))
    elif a.cmd == "painel":
        r = servicos.painel(con)
    else:
        r = {"banco": str(DB), "estado": "pronto"}
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


def backup(con, pasta: Path) -> dict:
    """Cópia consistente do banco (API de backup do SQLite) seguida de verificação de integridade.
    Não criptografa: guarde a pasta de destino em disco protegido (ver SEGURANCA.md)."""
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"controladoria-{datetime.now():%Y%m%d-%H%M%S}.db"
    alvo = sqlite3.connect(str(destino))
    with alvo:
        con.backup(alvo)
    ok = alvo.execute("PRAGMA integrity_check").fetchone()[0]
    alvo.close()
    auditar(con, "admin", "BACKUP", "banco", destino.name, depois={"integridade": ok})
    con.commit()
    if ok != "ok":
        sys.exit(f"Backup gerado com falha de integridade: {ok}")
    return {"backup": str(destino), "integridade": ok}


if __name__ == "__main__":
    main()
