"""Application container that wires the parts together."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .common import HOME
from .config import Config
from .database import DB
from .memory import Memory
from .router import Router
from .scheduler import Scheduler
from .security import Security
from .watchers import Watchers


class App:
    def __init__(self, config_path: str | None = None):
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / "workspace").mkdir(parents=True, exist_ok=True)
        self.cfg = Config(Path(config_path) if config_path else None)
        self.db = DB(HOME / "jarvis.db")
        self.security = Security(self.cfg)
        self.router = Router(self)
        self.memory = Memory(self)
        self.scheduler = Scheduler(self)
        self.watchers = Watchers(self)
        self.text_tool_models: set = set()
        self.pending: dict[str, list] = {}  # approval id -> [Event, decision]

    def ensure_session(self, sid: str | None, preset: str) -> str:
        sid = sid or uuid.uuid4().hex[:12]
        if not self.db.q("SELECT 1 FROM sessions WHERE id=?", (sid,)):
            now = time.time()
            self.db.x("INSERT INTO sessions(id,title,preset,created,updated) VALUES(?,?,?,?,?)", (sid, "", preset, now, now))
        return sid

    def save_message(self, sid: str, role: str, content: str, meta: dict | None = None):
        now = time.time()
        self.db.x("INSERT INTO messages(session_id,role,content,meta,ts) VALUES(?,?,?,?,?)",
                  (sid, role, content, json.dumps(meta or {}, default=str), now))
        if role == "user":
            self.db.x("UPDATE sessions SET title=CASE WHEN title='' OR title IS NULL THEN ? ELSE title END, updated=? "
                      "WHERE id=?", (re.sub(r"\s+", " ", content)[:70], now, sid))
        else:
            self.db.x("UPDATE sessions SET updated=? WHERE id=?", (now, sid))

    def sessions(self, limit=100):
        return self.db.q("SELECT s.id, s.title, s.preset, s.updated, (SELECT COUNT(*) FROM messages m WHERE "
                         "m.session_id=s.id) AS n FROM sessions s WHERE s.title<>'' AND s.id NOT LIKE 'bench-%' "
                         "ORDER BY s.updated DESC LIMIT ?", (limit,))

    def delete_session(self, sid: str):
        self.db.x("DELETE FROM messages WHERE session_id=?", (sid,))
        self.db.x("DELETE FROM sessions WHERE id=?", (sid,))

    def feedback(self, trace_id: str, value: int) -> bool:
        if not self.db.q("SELECT 1 FROM traces WHERE id=?", (trace_id,)):
            return False
        self.db.x("UPDATE traces SET feedback=? WHERE id=?", (int(value), trace_id))
        return True

    def stats(self, days: int = 30) -> dict:
        since = time.time() - days * 86400
        rows = self.db.q("SELECT engine, model, local, COUNT(*) n, SUM(prompt_tokens) pt, SUM(completion_tokens) ct, "
                         "AVG(latency) lat, AVG(ttft) ttft, SUM(joules) j, SUM(cost) cost, "
                         "SUM(CASE WHEN latency>ttft THEN latency-ttft ELSE 0 END) gen, "
                         "GROUP_CONCAT(DISTINCT energy_method) em FROM telemetry WHERE ts>=? GROUP BY engine, model "
                         "ORDER BY n DESC", (since,))
        models = []
        for r in rows:
            models.append({"engine": r["engine"], "model": r["model"], "local": bool(r["local"]), "calls": r["n"],
                           "prompt_tokens": r["pt"] or 0, "completion_tokens": r["ct"] or 0,
                           "avg_latency": r["lat"] or 0, "avg_ttft": r["ttft"] or 0, "joules": r["j"],
                           "tok_per_s": (r["ct"] or 0) / r["gen"] if r["gen"] else 0, "cost": r["cost"] or 0,
                           "energy_method": r["em"] or ""})
        tot_local = sum(m["calls"] for m in models if m["local"])
        tot = sum(m["calls"] for m in models)
        fb = self.db.q("SELECT SUM(feedback>0), SUM(feedback<0), COUNT(*) FROM traces WHERE ts>=?", (since,))[0]
        nfiles, nchunks = self.memory.doc_stats()
        return {"days": days, "models": models, "calls": tot, "local_share": (tot_local / tot) if tot else 1.0,
                "joules": sum(m["joules"] or 0 for m in models), "cost": sum(m["cost"] for m in models),
                "turns": fb[2] or 0, "thumbs_up": fb[0] or 0, "thumbs_down": fb[1] or 0,
                "facts": self.db.q("SELECT COUNT(*) FROM facts")[0][0], "doc_files": nfiles, "doc_chunks": nchunks}
