#!/bin/sh
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Prioritize virtual environment python if present
if [ -x "$DIR/.venv/bin/python" ]; then
  exec "$DIR/.venv/bin/python" -m llama_gadget "$@"
elif [ -x "$DIR/.venv/bin/python3" ]; then
  exec "$DIR/.venv/bin/python3" -m llama_gadget "$@"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 -m llama_gadget "$@"
else
  exec python -m llama_gadget "$@"
fi
