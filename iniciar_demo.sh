#!/bin/bash
# Demonstração do MVP com dados EXCLUSIVAMENTE fictícios.
# Uso: abra o Terminal nesta pasta e rode:  bash iniciar_demo.sh
# Para encerrar: Control + C no Terminal.
set -e
cd "$(dirname "$0")"

versao_ok() {  # $1 = executável Python; ok se >= 3.10
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

# 1. Escolher um Python compatível (python.org, Homebrew ou outro já instalado), do mais novo ao mais antigo.
PY=""
for c in python3.13 python3.12 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 && versao_ok "$c"; then PY="$(command -v "$c")"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "Não encontrei Python 3.10 ou mais novo neste Mac."
  if command -v python3 >/dev/null 2>&1; then echo "O python3 atual é: $(python3 --version 2>&1) ($(command -v python3))."; fi
  echo ""
  echo "Instale o Python 3.12 (recomendado):"
  echo "  1. Acesse https://www.python.org/downloads/macos/"
  echo "  2. Baixe 'macOS 64-bit universal2 installer' da versão 3.12 mais recente e abra o arquivo .pkg."
  echo "  3. Siga o instalador até o fim. Depois abra a pasta Aplicativos > Python 3.12"
  echo "     e dê dois cliques em 'Install Certificates.command'."
  echo "  4. Feche e abra o Terminal de novo e rode: bash iniciar_demo.sh"
  exit 1
fi

# 2. Conferir os recursos necessários antes de instalar qualquer coisa.
echo "Usando $("$PY" --version) em $PY"
"$PY" -m controladoria.verificar_ambiente || exit 1

# 3. Ambiente isolado. Um .venv criado por um Python antigo é descartado e recriado.
if [ -d .venv ] && ! { versao_ok .venv/bin/python && .venv/bin/python -m controladoria.verificar_ambiente >/dev/null 2>&1; }; then
  echo "Ambiente anterior foi criado com um Python incompatível; recriando .venv..."
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "Criando ambiente Python (só na primeira vez)..."
  "$PY" -m venv .venv
fi
echo "Instalando dependências (pode levar 1-2 minutos na primeira vez)..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m controladoria.verificar_ambiente --completo || exit 1

# 4. Banco da demonstração com cadastros fictícios.
export CONTROLADORIA_DADOS="$PWD/dados/demo"
export CONTROLADORIA_DB="$CONTROLADORIA_DADOS/controladoria.db"
mkdir -p "$CONTROLADORIA_DADOS"

echo "Carregando cadastros fictícios..."
.venv/bin/python -m controladoria.cli carregar tests/fixtures/cadastros > /dev/null
# Senhas SOMENTE da demonstração (dados fictícios). Para uso real, defina senhas próprias com
#   .venv/bin/python -m controladoria.cli definir-senha danilo
echo "demo-aprovador" | .venv/bin/python -m controladoria.cli definir-senha danilo --stdin > /dev/null
echo "demo-operador" | .venv/bin/python -m controladoria.cli definir-senha operador --stdin > /dev/null

echo ""
echo "Sistema no ar: http://127.0.0.1:8765"
echo "Aprovador:  usuário danilo    senha demo-aprovador"
echo "Operador:   usuário operador  senha demo-operador"
echo "Arquivo de teste: tests/fixtures/extrato_ficticio.pdf"
echo "Banco da demonstração: $CONTROLADORIA_DB"
echo "Para encerrar: Control + C"
(sleep 2; open "http://127.0.0.1:8765" 2>/dev/null || true) &
.venv/bin/python -m controladoria.app
