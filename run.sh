#!/bin/sh
cd "$(dirname "$(readlink -f "$0")")"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python -m llama_gadget "$@"
else
  exec python3 -m llama_gadget "$@"
fi
