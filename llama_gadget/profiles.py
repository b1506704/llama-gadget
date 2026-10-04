"""Server profiles the gadget can switch between.

All servers come from the "Image Description GGUF" project (llama.cpp build + start-server.sh):
  services    - Qwen3-8B on GPU1 (RTX 3050), always-on systemd user unit, :8080
  agent-gpu0  - Qwen3-8B on GPU0 (RTX 5060 Ti), long context + --jinja tool calling, :8081.
                Runs next to `services` (different GPU, different port).
  vision      - Qwen3.8-27B + mmproj across both GPUs (image analysis), :8080.
                Needs both GPUs, so it stops the other two while it runs.
Non-systemd profiles run as transient `systemd-run --user` units (survive the app, die on reboot).
"""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(os.environ.get("LLAMA_PROJECT", Path.home() / "Production" / "Image Description GGUF"))
MODEL_DIR = Path(os.environ.get("QWEN_MODEL_DIR", "/home/lbaoanhdev97/comfy/ComfyUI/models/unet"))
SERVER_BIN = PROJECT / "build" / "bin" / "llama-server"
AGENT_CTX = int(os.environ.get("AGENT_GPU0_CTX", 65536))


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    port: int
    unit: str
    gpus: str
    vision: bool
    conflicts: tuple
    default_prompt: str
    managed: bool = False  # True: persistent unit installed by the other project

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


PROFILES = {p.key: p for p in (
    Profile("services", "Services · Qwen3-8B · GPU1 (always-on)", 8080, "llama-services.service", "GPU1", False,
            ("vision",), "You are a concise, helpful assistant.", managed=True),
    Profile("agent-gpu0", f"Agent · Qwen3-8B {AGENT_CTX // 1024}K · GPU0", 8081, "llama-agent-gpu0", "GPU0", False,
            ("vision",),
            "You are an agentic assistant. Work step by step: plan briefly, use the tools you are given, "
            "verify results, and ask when information is missing."),
    Profile("vision", "Image analysis · Qwen3.8-27B vision · GPU0+1", 8080, "llama-vision", "GPU0+GPU1", True,
            ("services", "agent-gpu0"),
            "You are an image analysis assistant. Examine the attached image(s) carefully: describe content, "
            "objects, layout, any visible text (OCR) and notable details, then answer the user's question "
            "precisely. If no image is attached, ask the user to attach one."),
)}
DEFAULT_PROFILE = "services"


def _sctl(*args, timeout=60):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def is_active(p: Profile) -> bool:
    return _sctl("is-active", p.unit, timeout=5).stdout.strip() in ("active", "activating")


def active_conflicts(p: Profile):
    return [PROFILES[c] for c in p.conflicts if is_active(PROFILES[c])]


def _run_transient(p: Profile, cmd, env=None, cwd=None):
    args = ["systemd-run", "--user", f"--unit={p.unit}", "--collect", "--quiet",
            "-p", "Restart=on-failure", "-p", "RestartSec=5", "-p", "TimeoutStopSec=15"]
    for k, v in (env or {}).items():
        args += ["-E", f"{k}={v}"]
    if cwd:
        args += ["-p", f"WorkingDirectory={cwd}"]
    return subprocess.run(args + ["--", *map(str, cmd)], capture_output=True, text=True, timeout=30)


def start(p: Profile):
    """Start a profile's server (idempotent). Conflicting profiles must be stopped by the caller."""
    if is_active(p):
        return
    if p.managed:
        _sctl("start", p.unit, timeout=240)
    elif p.key == "agent-gpu0":
        cmd = [SERVER_BIN, "-m", MODEL_DIR / "Qwen3-8B-UD-Q4_K_XL.gguf",
               "-c", AGENT_CTX, "--jinja", "-ngl", 99, "-sm", "none", "-mg", 0,
               # Qwen3-8B is trained at 32K; YaRN stretches it for longer agent contexts
               "--rope-scaling", "yarn", "--rope-scale", max(1, AGENT_CTX // 32768),
               "--yarn-orig-ctx", 32768,
               "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--flash-attn", "on",
               "--port", p.port, "--host", "127.0.0.1", "--parallel", 1, "-t", 12, "-tb", 16, "-ub", 1024]
        r = _run_transient(p, cmd, env={"CUDA_VISIBLE_DEVICES": "0"})
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "systemd-run failed")
    elif p.key == "vision":
        r = _run_transient(p, [PROJECT / "start-server.sh", p.port, "vision"], cwd=PROJECT)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "systemd-run failed")


def stop(p: Profile):
    _sctl("stop", p.unit, timeout=60)
    _sctl("reset-failed", p.unit, timeout=10)
