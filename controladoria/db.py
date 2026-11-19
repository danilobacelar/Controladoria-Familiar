"""Conexão SQLite, criação do esquema e trilha de auditoria."""
from __future__ import annotations

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
    _migrar(con)
    con.commit()


def _migrar(con: sqlite3.Connection) -> None:
    """Acrescenta colunas novas em bancos criados por versões anteriores."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(usuarios)")}
    for nome, tipo in (("senha_hash", "TEXT"), ("falhas_login", "INTEGER NOT NULL DEFAULT 0"),
                       ("bloqueado_ate", "TIMESTAMP")):
        if nome not in cols:
            con.execute(f"ALTER TABLE usuarios ADD COLUMN {nome} {tipo}")


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
