"""Verifica se este Python atende ao MVP. Sem dependências externas.

  python3 -m controladoria.verificar_ambiente             antes da instalação
  python3 -m controladoria.verificar_ambiente --completo  depois (testa Flask/Werkzeug e o hash de senha)
Saída 0 = ok; 1 = falta algo (a mensagem diz o quê).
"""
import sys

VERSAO_MINIMA = (3, 10)
VERSAO_RECOMENDADA = "3.12"


def verificar(completo: bool = False, versao=None) -> list:
    erros = []
    v = tuple(versao or sys.version_info[:3])
    if v[:2] < VERSAO_MINIMA:
        erros.append(f"Python {'.'.join(map(str, v[:3]))} é antigo; mínimo 3.10, recomendado {VERSAO_RECOMENDADA}.")
    try:
        import hashlib
        hashlib.pbkdf2_hmac("sha256", b"teste", b"sal", 1000)
    except Exception as e:  # noqa: BLE001
        erros.append(f"hashlib.pbkdf2_hmac(sha256) indisponível: {e}")
    try:
        import sqlite3
        sqlite3.connect(":memory:").execute("SELECT json('{}')")
    except Exception as e:  # noqa: BLE001
        erros.append(f"SQLite sem suporte necessário: {e}")
    try:
        import venv  # noqa: F401
        import ensurepip  # noqa: F401
    except ImportError as e:
        erros.append(f"módulo de ambiente virtual ausente: {e}")
    try:
        import ssl
        if "OpenSSL" not in ssl.OPENSSL_VERSION:
            erros.append(f"Python compilado com {ssl.OPENSSL_VERSION} (não OpenSSL); use o Python do python.org.")
    except ImportError:
        erros.append("módulo ssl ausente")
    if completo and not erros:
        try:
            from werkzeug.security import check_password_hash, generate_password_hash
            import controladoria.autenticacao as a
            h = generate_password_hash("verificacao-1", method=a.METODO_HASH)
            if not (check_password_hash(h, "verificacao-1") and not check_password_hash(h, "outra")):
                erros.append("verificação de senha não confere")
            import flask, jsonschema, pdfplumber  # noqa: F401,E401
        except Exception as e:  # noqa: BLE001
            erros.append(f"dependências ou hash de senha com problema: {e}")
    return erros


if __name__ == "__main__":
    problemas = verificar("--completo" in sys.argv)
    if problemas:
        print("Ambiente incompatível:")
        for p in problemas:
            print(" -", p)
        sys.exit(1)
    print(f"Ambiente ok: Python {sys.version.split()[0]}")
