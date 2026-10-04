"""SQLite persistence: users (system prompt), sessions, messages, clipboard memory."""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# Project root (gitignored): <repo>/llama_gadget.db
DEFAULT_DB = Path(__file__).resolve().parent.parent / "llama_gadget.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    name TEXT PRIMARY KEY, system_prompt TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, attachments TEXT NOT NULL DEFAULT '[]',
    ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clips(
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL UNIQUE, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user, updated_at);
"""
MAX_CLIPS = 50


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self):
        self.conn = None
        self.path = None

    def open(self, path):
        """Open (creating if needed). On failure the previous connection is kept and an exception raised."""
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.commit()
        except Exception:
            conn.close()
            raise
        if self.conn:
            self.conn.close()
        self.conn, self.path = conn, path

    def status(self) -> dict:
        st = dict(path=str(self.path), ok=False)
        try:
            p = self.path
            st["size"] = p.stat().st_size
            st["writable"] = os.access(p, os.W_OK)
            st["integrity"] = self.conn.execute("PRAGMA quick_check").fetchone()[0]
            st["sessions"] = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            st["messages"] = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            st["clips"] = self.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
            st["ok"] = st["integrity"] == "ok" and st["writable"]
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e)
        return st

    # users / system prompt
    def get_prompt(self, user):
        r = self.conn.execute("SELECT system_prompt FROM users WHERE name=?", (user,)).fetchone()
        return r[0] if r else None

    def set_prompt(self, user, prompt):
        self.conn.execute("INSERT INTO users(name, system_prompt, updated_at) VALUES(?,?,?) "
                          "ON CONFLICT(name) DO UPDATE SET system_prompt=excluded.system_prompt, "
                          "updated_at=excluded.updated_at", (user, prompt, now()))
        self.conn.commit()

    def reset_prompt(self, user):
        self.conn.execute("DELETE FROM users WHERE name=?", (user,))
        self.conn.commit()

    # sessions / messages
    def create_session(self, user, title):
        t = now()
        cur = self.conn.execute("INSERT INTO sessions(user,title,created_at,updated_at) VALUES(?,?,?,?)",
                                (user, title, t, t))
        self.conn.commit()
        return cur.lastrowid

    def add_message(self, sid, role, content, attachments=None, ts=None):
        ts = ts or now()
        cur = self.conn.execute(
            "INSERT INTO messages(session_id,role,content,attachments,ts) VALUES(?,?,?,?,?)",
            (sid, role, content, json.dumps(attachments or []), ts))
        self.conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (ts, sid))
        self.conn.commit()
        return cur.lastrowid

    def delete_message(self, mid):
        self.conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        self.conn.commit()

    def list_sessions(self, user):
        return self.conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) "
            "FROM sessions s WHERE s.user=? ORDER BY s.updated_at DESC, s.id DESC", (user,)).fetchall()

    def load_messages(self, sid):
        rows = self.conn.execute(
            "SELECT id, role, content, attachments, ts FROM messages WHERE session_id=? ORDER BY id", (sid,))
        return [dict(id=i, role=r, content=c, attachments=json.loads(a), ts=t) for i, r, c, a, t in rows]

    def delete_session(self, sid):
        self.conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        self.conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.conn.commit()

    # clipboard memory
    def add_clip(self, text):
        self.conn.execute("DELETE FROM clips WHERE text=?", (text,))
        self.conn.execute("INSERT INTO clips(text, ts) VALUES(?,?)", (text, now()))
        self.conn.execute("DELETE FROM clips WHERE id NOT IN (SELECT id FROM clips ORDER BY id DESC LIMIT ?)",
                          (MAX_CLIPS,))
        self.conn.commit()

    def list_clips(self, limit=20):
        return [r[0] for r in self.conn.execute("SELECT text FROM clips ORDER BY id DESC LIMIT ?", (limit,))]

    def clear_clips(self):
        self.conn.execute("DELETE FROM clips")
        self.conn.commit()
