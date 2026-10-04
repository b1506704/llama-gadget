"""Parse user-supplied files into plain text for the chat."""
import json
import os
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


def parse_file(path, max_chars=MAX_CHARS):
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
    truncated = chars > max_chars
    if truncated:
        text = text[:max_chars]
    return dict(name=p.name, text=text, truncated=truncated, chars=chars)


# ----------------------------------------------------------------- folder scanning
IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox",
               ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist", "target", ".next", ".nuxt",
               ".cache", ".idea", ".vscode", "coverage", "site-packages", ".gradle", "vendor"}
SKIP_SUFFIXES = (".lock", ".map", ".min.js", ".min.css", ".svg", ".pyc", ".log", ".sqlite", ".db")
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", ".DS_Store"}
DOC_NAMES = ("readme", "contributing", "claude", "agents", "architecture", "overview", "design")
CONFIG_NAMES = {"package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "cargo.toml",
                "go.mod", "pom.xml", "build.gradle", "makefile", "dockerfile", "docker-compose.yml",
                "tsconfig.json", "cmakelists.txt"}
DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
FOLDER_MAX_FILE_BYTES = 1024 * 1024
FOLDER_FILE_CHARS = 8000   # per-file cap in folder mode
FOLDER_MAX_CANDIDATES = 400


def _git_files(root: Path):
    if not shutil.which("git"):
        return None
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, timeout=30)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]


def _walk_files(root: Path):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in IGNORE_DIRS and not x.startswith(".")]
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), root))
    return out


def _category(rel: str) -> int:
    p = Path(rel)
    name, ext = p.name.lower(), p.suffix.lower()
    if p.stem.lower() in DOC_NAMES or (ext in DOC_EXTS and len(p.parts) == 1):
        return 0
    if name in CONFIG_NAMES:
        return 1
    if ext in DOC_EXTS:
        return 3
    return 2


def scan_folder(root):
    """Find text files under `root` (git-aware), ranked by usefulness.

    Returns (entries, total_files_seen). Each entry: dict(rel, path, size, cat). Binary, huge, lock/minified
    and ignored files are dropped. Respects .gitignore when the folder is a git repo.
    """
    root = Path(root)
    rels = _git_files(root) or _walk_files(root)
    entries = []
    for rel in rels:
        parts = Path(rel).parts
        if any(x in IGNORE_DIRS for x in parts[:-1]) or parts[-1] in SKIP_NAMES \
                or rel.lower().endswith(SKIP_SUFFIXES):
            continue
        path = root / rel
        try:
            size = path.stat().st_size
            if not path.is_file() or size == 0 or size > FOLDER_MAX_FILE_BYTES:
                continue
            with open(path, "rb") as fh:
                _decode(fh.read(4096))
        except (OSError, UnsupportedFile):
            continue
        entries.append(dict(rel=rel, path=str(path), size=size, cat=_category(rel)))
    entries.sort(key=lambda e: (e["cat"], len(Path(e["rel"]).parts), e["size"], e["rel"]))
    return entries, len(rels)


def read_candidates(entries, limit=FOLDER_MAX_CANDIDATES):
    """Read (capped) text for the top `limit` entries; adds 'text', 'chars', 'truncated'."""
    out = []
    for e in entries[:limit]:
        try:
            r = parse_file(e["path"], FOLDER_FILE_CHARS)
        except (UnsupportedFile, OSError):
            continue
        out.append({**e, "text": r["text"], "chars": len(r["text"]), "truncated": r["truncated"]})
    return out


def build_folder_context(root, picked, all_rels):
    """Assemble the context block: file tree + selected file contents."""
    root = Path(root)
    tree = sorted(all_rels)
    lines = tree[:300] + ([f"... (+{len(tree) - 300} more)"] if len(tree) > 300 else [])
    out = [f"# Folder context: {root.name}  ({root})", "", "## Text files in folder", *lines, "",
           "## File contents"]
    for f in picked:
        note = " (truncated)" if f["truncated"] else ""
        out += [f"### {f['rel']}{note}", "~~~~", f["text"], "~~~~", ""]
    return "\n".join(out).strip()
