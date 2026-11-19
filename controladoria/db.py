"""Conexão SQLite, criação do esquema e trilha de auditoria."""
import json
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def conectar(caminho: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(caminho))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def inicializar(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.commit()


def auditar(con, ator_id, acao, tabela, registro_id, antes=None, depois=None, motivo=None):
    con.execute(
        "INSERT INTO auditoria (ator_id, acao, tabela, registro_id, antes, depois, motivo)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            ator_id,
            acao,
            tabela,
            str(registro_id),
            None if antes is None else json.dumps(antes, ensure_ascii=False, default=str),
            None if depois is None else json.dumps(depois, ensure_ascii=False, default=str),
            motivo,
        ),
    )


def linha(con, sql, *args):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None
