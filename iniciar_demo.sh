#!/bin/bash
# Demonstração do MVP com dados EXCLUSIVAMENTE fictícios.
# Uso: abra o Terminal nesta pasta e rode:  bash iniciar_demo.sh
# Para encerrar: Control + C no Terminal.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 não encontrado. Rode 'xcode-select --install', conclua a instalação e tente de novo."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Criando ambiente Python (só na primeira vez)..."
  python3 -m venv .venv
fi
echo "Instalando dependências (pode levar 1-2 minutos na primeira vez)..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

export CONTROLADORIA_DADOS="$PWD/dados/demo"
export CONTROLADORIA_DB="$CONTROLADORIA_DADOS/controladoria.db"
export CONTROLADORIA_PIN="${CONTROLADORIA_PIN:-1234}"
mkdir -p "$CONTROLADORIA_DADOS"

echo "Carregando cadastros fictícios..."
.venv/bin/python -m controladoria.cli carregar tests/fixtures/cadastros > /dev/null

echo ""
echo "Sistema no ar: http://127.0.0.1:8765"
echo "Entrar como: Danilo (APROVADOR)   PIN: $CONTROLADORIA_PIN"
echo "Arquivo de teste: tests/fixtures/extrato_ficticio.pdf"
echo "Banco da demonstração: $CONTROLADORIA_DB"
echo "Para encerrar: Control + C"
(sleep 2; open "http://127.0.0.1:8765" 2>/dev/null || true) &
.venv/bin/python -m controladoria.app
