"""SQLite schema and a thread-safe connection wrapper."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, preset TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    content TEXT, meta TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, emb BLOB, ts REAL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, idx INTEGER, text TEXT,
    emb BLOB, mtime REAL);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session_id TEXT,
    engine TEXT, model TEXT, local INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
    ttft REAL, latency REAL, joules REAL, energy_method TEXT, cost REAL, route_reason TEXT);
CREATE TABLE IF NOT EXISTS traces(id TEXT PRIMARY KEY, ts REAL, session_id TEXT, preset TEXT, model TEXT,
    system TEXT, prompt TEXT, response TEXT, tools TEXT, feedback INTEGER);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, prompt TEXT,
    schedule TEXT, preset TEXT, next_run TEXT, last_run TEXT, last_result TEXT, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, source TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session_id TEXT, tool TEXT,
    args TEXT, decision TEXT, result TEXT);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            self.conn.executescript(SCHEMA)
            try:
                self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING "
                                  "fts5(text, content='chunks', content_rowid='id')")
                self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING "
                                  "fts5(text, content='facts', content_rowid='id')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            self.conn.commit()

    def q(self, sql: str, params=()) -> list:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def x(self, sql: str, params=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid
