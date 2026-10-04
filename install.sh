#!/bin/sh
# Installs a launcher entry into the application menu / autostart (optional: --autostart).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
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
if [ "$1" = "--autostart" ]; then
  mkdir -p ~/.config/autostart
  cp ~/.local/share/applications/llama-gadget.desktop ~/.config/autostart/
fi
echo "Installed."
