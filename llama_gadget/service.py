"""Control + health-check for the always-on llama.cpp systemd user unit."""
import subprocess
import urllib.request

UNIT = "llama-services.service"
BASE_URL = "http://127.0.0.1:8080"


def _systemctl(*args, timeout=60):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def unit_state() -> str:
    """systemd ActiveState: active, inactive, activating, failed, ..."""
    try:
        return _systemctl("is-active", UNIT, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def healthy(base_url=BASE_URL) -> bool:
    try:
        with urllib.request.urlopen(base_url + "/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def status(base_url=BASE_URL) -> str:
    """One of: online, loading, offline."""
    if healthy(base_url):
        return "online"
    return "loading" if unit_state() == "activating" else (
        "loading" if unit_state() == "active" else "offline")


def start():
    return _systemctl("start", UNIT, timeout=240)


def stop():
    return _systemctl("stop", UNIT, timeout=60)


def n_ctx(base_url=BASE_URL):
    """Server context window size, or None."""
    import json
    try:
        with urllib.request.urlopen(base_url + "/props", timeout=2) as r:
            return json.load(r)["default_generation_settings"]["n_ctx"]
    except Exception:
        return None


def gpu_stats():
    """List of dicts (index, name, util %, mem_used MiB, mem_total MiB, temp C) via nvidia-smi."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) == 6:
            gpus.append(dict(index=int(f[0]), name=f[1].replace("NVIDIA GeForce ", ""), util=int(f[2]),
                             used=int(f[3]), total=int(f[4]), temp=int(f[5])))
    return gpus
