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
