# Llama Gadget

Small PyQt6 desktop gadget and utility for CachyOS to chat with, monitor, and toggle local `llama.cpp` inference profiles across multiple GPUs.

## Profiles

Switch profiles instantly via the header dropdown:

- **Services (Default, always-on)**: Qwen3-8B UD-Q4_K_XL pinned to **GPU 1** (RTX 3050), port `8080`, 32K context. Managed by `systemd --user` (`llama-services.service`).
- **Agent GPU0 (Agentic workflow)**: Qwen3-8B UD-Q4_K_XL pinned to **GPU 0** (RTX 5060 Ti), port `8081`, **64K context** (YaRN RoPE scaling), `--jinja` enabled for tool calling and agentic tasks. Runs alongside the services tier with ~60 tok/s generation.
- **Image Analysis (Vision)**: Qwen3.8-27B UD-IQ4_XS + `mmproj-F16` dual-GPU layer split across GPU 0 + GPU 1, port `8080`, 32K context. Supports image file attachment, image drag & drop, and direct clipboard image pasting (`Ctrl+V`). Automatically parks the services tier while active and restores it when switched away.

## Features

- **Profile switching & service toggle**: Start/Stop button and system tray icon (green = online, amber = loading, red = offline).
- **Live monitoring**:
  - Live generation speed (tok/s during streaming and final prompt/generation stats from `llama.cpp`).
  - Context tokens used vs context window limit (`n_ctx`), plus turn count.
  - Per-GPU hardware monitoring: GPU utilization %, VRAM used/total, and temperatures via `nvidia-smi`.
  - Endpoint URL and loaded model metadata (`n_params`, context size).
- **Editable Endpoint**: Modify the OpenAI-compatible endpoint URL directly using the ✎ button in the header or via the Settings tab. Persisted per profile with automatic URL sanitization and one-click reset to default.
- **Rich formatting**: Markdown rendering (tables, code blocks, lists) with per-message timestamps.
- **Persistent Chat History**: SQLite storage (`llama_gadget.db` in project root, gitignored). Browse, open, and delete past sessions.
- **Folder Context**: Smart git-aware folder scanner (skips binaries, node_modules, lockfiles, virtual environments). Priority ranking (docs → config → source) and interactive selection dialog sized to the available context window.
- **File & Image attachments**:
  - Text: `.txt`, `.md`, `.py`, `.json`, `.csv`, `.html`, `.docx`, `.pdf` (via `pdftotext` or `pypdf`).
  - Vision mode: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, clipboard image paste.
- **Clipboard memory**: Automatically saves text copies to an SQLite ring buffer (last 50 clips). Quick menu via `📋 Clips` button or `Ctrl+Shift+V`.
- **Customizable System Prompts**: Saved per user (defaults to PC hostname) and per profile.
- **Gadget mode**: Frameless, translucent background, stays-on-top, draggable. Closing window minimizes to the system tray.

## Run
```bash
sudo pacman -S python-pyqt6   # or: pip install -r requirements.txt
./run.sh
./install.sh [--autostart]    # add application menu entry or start at login
```
