"""Long-term facts and a document index with hybrid keyword and embedding search."""

from __future__ import annotations

import array
import math
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .documents import SKIP_DIRS, TEXT_EXT, chunk_text, extract_text

if TYPE_CHECKING:
    from .app import App


STOP = set("a an the and or of to in on for with is are was were be been it this that what which who how why "
           "when where do does did can could should would i you me my your we our they them he she his her at by "
           "from as about into than then there their please tell show give find".split())


def pack_vec(v) -> bytes:
    return array.array("f", v).tobytes()


def unpack_vec(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


try:
    import numpy as _np  # optional speed-up, never required
except Exception:
    _np = None


def cosine_top(query: list[float], rows: list, k: int) -> list[tuple[int, float]]:
    if not rows:
        return []
    if _np is not None:
        ids = [r[0] for r in rows]
        mat = _np.vstack([_np.frombuffer(r[1], dtype=_np.float32) for r in rows])
        q = _np.asarray(query, dtype=_np.float32)
        sims = mat @ q / (_np.linalg.norm(mat, axis=1) * (_np.linalg.norm(q) or 1) + 1e-9)
        order = _np.argsort(-sims)[:k]
        return [(ids[i], float(sims[i])) for i in order]
    qn = math.sqrt(sum(x * x for x in query)) or 1.0
    scored = []
    for rid, blob in rows:
        v = unpack_vec(blob)
        if len(v) != len(query):
            continue
        dot = sum(a * b for a, b in zip(v, query))
        vn = math.sqrt(sum(a * a for a in v)) or 1.0
        scored.append((rid, dot / (qn * vn)))
    scored.sort(key=lambda t: -t[1])
    return scored[:k]


def fts_query(q: str) -> str:
    toks = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 1 and t not in STOP][:12]
    return " OR ".join(f'"{t}"' for t in toks)


class Memory:
    def __init__(self, app: "App"):
        self.app, self.db = app, app.db
        self._embed_ok: bool | None = None

    # -- embeddings ---------------------------------------------------------
    def embed(self, texts: list[str]):
        if self._embed_ok is False or not texts:
            return None
        model = self.app.cfg.get("engine.embed_model")
        eng = self.app.router.local
        if not model or eng is None or not eng.available():
            return None
        vecs = eng.embed(texts, model)
        if not vecs or len(vecs) != len(texts):
            self._embed_ok = False
            return None
        self._embed_ok = True
        return vecs

    # -- generic hybrid search -----------------------------------------------
    def _search(self, table: str, query: str, k: int) -> list[tuple[sqlite3.Row, float]]:
        scores: Counter = Counter()
        if self.db.fts:
            fq = fts_query(query)
            if fq:
                try:
                    rows = self.db.q(f"SELECT rowid FROM {table}_fts WHERE {table}_fts MATCH ? "
                                     f"ORDER BY bm25({table}_fts) LIMIT 30", (fq,))
                    for r, row in enumerate(rows):
                        scores[row[0]] += 1 / (60 + r)
                except sqlite3.OperationalError:
                    pass
        else:
            terms = [t for t in re.findall(r"\w+", query.lower()) if t not in STOP and len(t) > 1]
            if terms:
                rows = self.db.q(f"SELECT id, text FROM {table} ORDER BY id DESC LIMIT 5000")
                ranked = sorted(((sum(r[1].lower().count(t) for t in terms), r[0]) for r in rows), reverse=True)
                for r, (s, rid) in enumerate([x for x in ranked if x[0] > 0][:30]):
                    scores[rid] += 1 / (60 + r)
        qv = self.embed([query])
        if qv:
            rows = self.db.q(f"SELECT id, emb FROM {table} WHERE emb IS NOT NULL")
            floor = float(self.app.cfg.get("engine.embed_min_similarity", 0.47))
            for r, (rid, sim) in enumerate(cosine_top(qv[0], [(x[0], x[1]) for x in rows], 30)):
                if sim > floor:
                    scores[rid] += 1 / (60 + r)
        if not scores:
            return []
        top = scores.most_common(k)
        out = []
        for rid, s in top:
            row = self.db.q(f"SELECT * FROM {table} WHERE id=?", (rid,))
            if row:
                out.append((row[0], s))
        return out

    # -- facts ------------------------------------------------------------------
    def add_fact(self, text: str) -> int:
        text = text.strip()
        dup = self.db.q("SELECT id FROM facts WHERE lower(text)=lower(?)", (text,))
        if dup:
            return dup[0][0]
        v = self.embed([text])
        fid = self.db.x("INSERT INTO facts(text, emb, ts) VALUES(?,?,?)", (text, pack_vec(v[0]) if v else None, time.time()))
        if self.db.fts:
            self.db.x("INSERT INTO facts_fts(rowid, text) VALUES(?,?)", (fid, text))
        return fid

    def forget(self, fid: int) -> bool:
        row = self.db.q("SELECT text FROM facts WHERE id=?", (fid,))
        if not row:
            return False
        if self.db.fts:
            self.db.x("INSERT INTO facts_fts(facts_fts, rowid, text) VALUES('delete', ?, ?)", (fid, row[0][0]))
        self.db.x("DELETE FROM facts WHERE id=?", (fid,))
        return True

    def facts(self, limit: int = 200) -> list:
        return self.db.q("SELECT id, text, ts FROM facts ORDER BY id DESC LIMIT ?", (limit,))

    def search_facts(self, query: str, k: int = 5):
        return self._search("facts", query, k)

    def context_for(self, query: str) -> list[str]:
        n = self.db.q("SELECT COUNT(*) FROM facts")[0][0]
        if n == 0:
            return []
        if n <= 15:
            return [r["text"] for r in self.facts(15)][::-1]
        return [r["text"] for r, _ in self.search_facts(query, 6)]

    # -- documents ----------------------------------------------------------------
    def remove_path(self, path: str):
        rows = self.db.q("SELECT id, text FROM chunks WHERE path=?", (path,))
        if self.db.fts:
            for r in rows:
                self.db.x("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)", (r[0], r[1]))
        self.db.x("DELETE FROM chunks WHERE path=?", (path,))

    def ingest(self, target: str, on_file: Callable[[str, int], None] | None = None) -> dict:
        root = Path(target).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        if root.is_file():
            files = [root]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix.lower() in TEXT_EXT and not fn.startswith("."):
                        files.append(p)
        stats = {"files": 0, "chunks": 0, "skipped": 0, "unchanged": 0, "embedded": False}
        for f in files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            prev = self.db.q("SELECT mtime FROM chunks WHERE path=? LIMIT 1", (str(f),))
            if prev and abs((prev[0][0] or 0) - mtime) < 1e-6:
                stats["unchanged"] += 1
                continue
            text = extract_text(f)
            if not text.strip():
                stats["skipped"] += 1
                continue
            self.remove_path(str(f))
            chunks = chunk_text(text)
            vecs: list = []
            for i in range(0, len(chunks), 32):
                v = self.embed([f"{f.name}\n{c}" for c in chunks[i:i + 32]])
                vecs.extend(v if v else [None] * len(chunks[i:i + 32]))
            for i, (c, v) in enumerate(zip(chunks, vecs)):
                cid = self.db.x("INSERT INTO chunks(path, idx, text, emb, mtime) VALUES(?,?,?,?,?)",
                                (str(f), i, c, pack_vec(v) if v else None, mtime))
                if self.db.fts:
                    self.db.x("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cid, c))
            stats["files"] += 1
            stats["chunks"] += len(chunks)
            stats["embedded"] = stats["embedded"] or any(vecs)
            if on_file:
                on_file(str(f), len(chunks))
        return stats

    def search_docs(self, query: str, k: int = 5):
        return self._search("chunks", query, k)

    def doc_stats(self) -> tuple[int, int]:
        r = self.db.q("SELECT COUNT(DISTINCT path), COUNT(*) FROM chunks")[0]
        return r[0], r[1]
