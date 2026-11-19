"""Autenticação por usuário: senha própria com hash, bloqueio por tentativas e auditoria."""
from __future__ import annotations

from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from .db import auditar, linha

# PBKDF2-HMAC-SHA256 com 1.000.000 de iterações e sal aleatório (o padrão do Werkzeug para PBKDF2,
# acima da recomendação OWASP de 600.000). Escolhido explicitamente em vez de scrypt porque funciona em
# qualquer Python (o Python da Apple, compilado com LibreSSL, não tem hashlib.scrypt) e o mesmo banco
# continua verificável se for aberto em outro computador.
METODO_HASH = "pbkdf2:sha256:1000000"

PAPEIS_INTERFACE = ("APROVADOR", "OPERADOR")   # AGENTE e ADMIN_TECNICO nunca entram na interface
MAX_FALHAS = 5
BLOQUEIO = timedelta(minutes=15)
SENHA_MIN = 8


class FalhaAutenticacao(Exception):
    pass


def definir_senha(con, uid: str, senha: str, ator_id: str = "admin") -> None:
    u = linha(con, "SELECT id, papel FROM usuarios WHERE id=?", uid)
    if not u:
        raise FalhaAutenticacao("usuário inexistente")
    if u["papel"] not in PAPEIS_INTERFACE:
        raise FalhaAutenticacao("este papel não acessa a interface e não recebe senha")
    if len(senha or "") < SENHA_MIN:
        raise FalhaAutenticacao(f"a senha precisa ter pelo menos {SENHA_MIN} caracteres")
    con.execute("UPDATE usuarios SET senha_hash=?, falhas_login=0, bloqueado_ate=NULL WHERE id=?",
                (generate_password_hash(senha, method=METODO_HASH), uid))
    auditar(con, ator_id, "SENHA_DEFINIDA", "usuarios", uid, motivo="definição ou troca de senha")
    con.commit()


def autenticar(con, uid: str, senha: str, agora: datetime | None = None) -> dict:
    """Devolve o usuário autenticado ou lança FalhaAutenticacao (mensagem genérica para quem tenta)."""
    agora = agora or datetime.now()
    u = linha(con, "SELECT * FROM usuarios WHERE id=?", uid or "")
    generica = FalhaAutenticacao("usuário ou senha inválidos")
    if not u or u["papel"] not in PAPEIS_INTERFACE or not u["senha_hash"]:
        auditar(con, "anonimo", "LOGIN_RECUSADO", "usuarios", uid or "-", depois={"motivo": "sem acesso"})
        con.commit()
        raise generica
    if u["bloqueado_ate"] and datetime.fromisoformat(u["bloqueado_ate"]) > agora:
        auditar(con, "anonimo", "LOGIN_RECUSADO", "usuarios", uid, depois={"motivo": "bloqueado"})
        con.commit()
        raise FalhaAutenticacao("acesso bloqueado temporariamente por tentativas inválidas")
    try:
        senha_ok = check_password_hash(u["senha_hash"], senha or "")
    except (AttributeError, ValueError) as e:   # hash criado com algoritmo indisponível neste Python
        auditar(con, "anonimo", "LOGIN_RECUSADO", "usuarios", uid, depois={"motivo": "algoritmo de hash indisponível"})
        con.commit()
        raise FalhaAutenticacao("a senha deste usuário foi criada com um algoritmo indisponível neste Python; "
                                "redefina com: python -m controladoria.cli definir-senha " + uid) from e
    if not senha_ok:
        falhas = u["falhas_login"] + 1
        bloqueio = (agora + BLOQUEIO).isoformat() if falhas >= MAX_FALHAS else None
        con.execute("UPDATE usuarios SET falhas_login=?, bloqueado_ate=? WHERE id=?",
                    (0 if bloqueio else falhas, bloqueio, uid))
        auditar(con, "anonimo", "LOGIN_RECUSADO", "usuarios", uid,
                depois={"motivo": "senha inválida", "bloqueado": bool(bloqueio)})
        con.commit()
        raise generica
    con.execute("UPDATE usuarios SET falhas_login=0, bloqueado_ate=NULL WHERE id=?", (uid,))
    auditar(con, uid, "LOGIN", "usuarios", uid)
    con.commit()
    return {k: u[k] for k in ("id", "nome", "papel")}
