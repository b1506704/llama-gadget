# Llama Gadget

Small PyQt6 desktop utility for CachyOS: toggle the always-on `llama-services` systemd user unit
(Qwen3-8B on the second GPU, `127.0.0.1:8080`) and chat with it.

- Service on/off button + tray icon (green = online, amber = loading, red = offline)
- Live stats: context used/window, turns, tok/s (generation + prompt), per-GPU util/VRAM/temp
- Markdown-rendered replies (tables, code, lists) with a timestamp on every message
- **Chat history** in SQLite (default `llama_gadget.db` in the project root, gitignored); History tab lists/open/deletes sessions
- **Settings** tab: user name (default = PC hostname), per-user editable system prompt, DB browse/select with live status (integrity, size, counts)
- **Attach files** (button or drag & drop): txt/md/code/json/csv/html/docx/pdf (pdf needs `pdftotext` or `pypdf`); capped at 40k chars each
- **Folder context** (📁 Folder button or drop a folder): git-aware scan (respects .gitignore, skips binaries, node_modules, lockfiles…), ranks docs → config → source, review dialog with a token budget sized to the free context window; stored in the session and sent as part of the system prompt
- **Clipboard memory**: everything you copy is remembered (last 50, in the DB); 📋 Clips or Ctrl+Shift+V pastes one fast
- Streaming chat, **New session** to reset the conversation
- **Gadget mode**: frameless, always-on-top, drag to move (Wayland compositors may ignore on-top/move; use your WM's rules)
- Closing the window hides it to the tray

## Run
```
sudo pacman -S python-pyqt6   # or: pip install -r requirements.txt
./run.sh
./install.sh [--autostart]    # app-menu entry / start at login
```
Edit `llama_gadget/service.py` to change the unit name or port.
