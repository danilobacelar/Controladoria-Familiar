"""Linha de comando (sem poder de decisão).

  python -m controladoria.cli iniciar
  python -m controladoria.cli carregar dados/cadastros
  python -m controladoria.cli importar caminho/extrato.pdf --instrumento A001 [--simulacao]
  python -m controladoria.cli painel

Decisões só pela interface web, autenticada como APROVADOR.
"""
from __future__ import annotations

import argparse
import json

from . import cadastros, servicos
from .app import DB
from .db import conectar, inicializar


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controladoria")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("iniciar")
    c = sub.add_parser("carregar"); c.add_argument("pasta")
    i = sub.add_parser("importar"); i.add_argument("pdf"); i.add_argument("--instrumento")
    i.add_argument("--simulacao", action="store_true"); i.add_argument("--ator", default="claude")
    sub.add_parser("painel")
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
    elif a.cmd == "painel":
        r = servicos.painel(con)
    else:
        r = {"banco": str(DB), "estado": "pronto"}
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
