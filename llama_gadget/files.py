"""Parse user-supplied files into plain text for the chat."""
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 40_000  # ~10k tokens; keeps a couple of files inside the 32k context


class UnsupportedFile(Exception):
    pass


class _Strip(HTMLParser):
    SKIP = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, d):
        if not self._skip:
            self.out.append(d)


def _decode(data: bytes) -> str:
    head = data[:4096]
    if head and sum(b < 9 or 13 < b < 32 for b in head) / len(head) > 0.05:
        if not head.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise UnsupportedFile("binary file")
    if b"\0" in head and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedFile("binary file")
    for enc in ("utf-8-sig", "utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def _docx(path):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    return "\n".join("".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p"))


def _pdf(path):
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return r.stdout
    try:
        from pypdf import PdfReader
    except ImportError:
        raise UnsupportedFile("PDF needs `pdftotext` (poppler) or `pypdf`") from None
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def parse_file(path):
    """Return dict(name, text, truncated, chars). Raises UnsupportedFile / OSError."""
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise UnsupportedFile("file larger than 20 MB")
    ext = p.suffix.lower()
    if ext == ".pdf":
        text = _pdf(p)
    elif ext == ".docx":
        text = _docx(p)
    else:
        text = _decode(p.read_bytes())
        if ext in (".html", ".htm", ".xhtml"):
            s = _Strip()
            s.feed(text)
            text = "".join(s.out)
        elif ext == ".json":
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except ValueError:
                pass
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise UnsupportedFile("no text content found")
    chars = len(text)
    truncated = chars > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    return dict(name=p.name, text=text, truncated=truncated, chars=chars)
