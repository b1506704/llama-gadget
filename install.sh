#!/bin/sh
# Installs a launcher entry into the application menu / autostart (supports --auto-start or --autostart).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --autostart|--auto-start)
      AUTOSTART=1
      ;;
  esac
done

mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/llama-gadget.desktop <<D
[Desktop Entry]
Type=Application
Name=Llama Gadget
Comment=Chat with the local always-on LLM and toggle its service
Exec=$DIR/run.sh
Icon=utilities-terminal
Categories=Utility;
Terminal=false
D

if [ "$AUTOSTART" -eq 1 ]; then
  mkdir -p ~/.config/autostart
  cp ~/.local/share/applications/llama-gadget.desktop ~/.config/autostart/
fi

# Ensure virtual environment and dependencies are ready on Arch/CachyOS (PEP 668)
if [ ! -d "$DIR/.venv" ] && [ -f "$DIR/requirements.txt" ]; then
  if command -v python3 >/dev/null 2>&1; then
    echo "Creating virtual environment at $DIR/.venv..."
    python3 -m venv "$DIR/.venv" 2>/dev/null || true
    if [ -f "$DIR/.venv/bin/pip" ]; then
      "$DIR/.venv/bin/pip" install --upgrade pip 2>/dev/null || true
      "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt" 2>/dev/null || true
    fi
  fi
fi

echo "Installed."
