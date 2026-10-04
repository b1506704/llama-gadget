"""Streaming client for the llama.cpp OpenAI-compatible endpoint."""
import json
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

from .service import BASE_URL


class ChatWorker(QThread):
    token = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, messages, base_url=BASE_URL, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.base_url = base_url
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        body = json.dumps({
            "messages": self.messages,
            "stream": True,
            "temperature": 0.7,
            # Qwen3: skip the long thinking phase for a snappy chat utility
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw in resp:
                    if self._cancel:
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                    if delta:
                        self.token.emit(delta)
            self.finished_ok.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
