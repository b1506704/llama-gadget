# Llama Gadget

Small PyQt6 desktop utility for CachyOS: toggle the always-on `llama-services` systemd user unit
(Qwen3-8B on the second GPU, `127.0.0.1:8080`) and chat with it.

- Service on/off button + tray icon (green = online, amber = loading, red = offline)
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
