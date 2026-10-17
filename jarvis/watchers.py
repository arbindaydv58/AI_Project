"""Watchers: proactive alerts for battery, disk, memory, websites, news and failing commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import TYPE_CHECKING

from .common import dim
from .connectors import get_news
from .errors import EngineError
from .http_utils import http_open
from .sysutils import _battery, memory_info, notify

if TYPE_CHECKING:
    from .app import App


def _interval(s: str) -> float:
    m = re.fullmatch(r"\s*(\d+)\s*(s|sec|m|min|h|hr)?\w*\s*", str(s or "5m"))
    if not m:
        return 300
    n, u = int(m.group(1)), (m.group(2) or "m")[0]
    return max(15, n * {"s": 1, "m": 60, "h": 3600}[u])


class Watchers:
    def __init__(self, app: "App"):
        self.app = app
        self.state: dict[str, bool] = {}
        self.last: dict[str, float] = {}
        self.seen_titles: set[str] = set()

    def list(self) -> list[dict]:
        return list(self.app.cfg.get("watchers") or [])

    @staticmethod
    def key(w: dict) -> str:
        return json.dumps(w, sort_keys=True)

    def describe(self, w: dict) -> str:
        t = w.get("type")
        return {"battery": f"battery below {w.get('below', 20)}%", "disk": f"disk free below {w.get('below_gb', 5)} GB",
                "url": f"website {w.get('url')} down", "rss": f"news about {', '.join(w.get('keywords', []))}",
                "command": f"command fails: {w.get('command')}",
                "memory": f"RAM use above {w.get('above_pct', 90)}%"}.get(t, t) + f" (every {w.get('every', '5m')})"

    def check(self, w: dict) -> tuple[bool, str]:
        t, addr = w.get("type"), (self.app.cfg.get("agent.address") or "sir").capitalize()
        if t == "battery":
            b = _battery()
            if b is None:
                return False, ""
            pct, charging = b
            return (pct < int(w.get("below", 20)) and not charging), f"{addr}, battery is at {pct}% and not charging."
        if t == "disk":
            path = os.path.expanduser(w.get("path") or "~")
            free = shutil.disk_usage(path).free / 2**30
            return free < float(w.get("below_gb", 5)), f"{addr}, only {free:.1f} GB of disk space left on {path}."
        if t == "memory":
            mem = memory_info()
            if mem is None:
                return False, ""
            used = 100 * (1 - mem[1] / max(1, mem[0]))
            return used > float(w.get("above_pct", 90)), f"{addr}, memory usage is at {used:.0f}%."
        if t == "url":
            url = w["url"]
            try:
                with http_open(url, timeout=float(w.get("timeout", 10))) as r:
                    code = r.status
                ok = code == int(w.get("expect", 200))
                return (not ok), f"{addr}, {url} returned status {code}."
            except EngineError as e:
                return True, f"{addr}, {url} appears to be down ({str(e)[:80]})."
        if t == "rss":
            kws = [k.lower() for k in w.get("keywords", [])]
            feeds = [w["feed"]] if w.get("feed") else self.app.cfg.get("digest.feeds", [])
            fresh = []
            first = not self.seen_titles
            for src, title, _ in get_news(feeds, 30):
                if title in self.seen_titles:
                    continue
                self.seen_titles.add(title)
                if not first and any(k in title.lower() for k in kws):
                    fresh.append(f"{title} ({src})")
            self.state[self.key(w)] = False  # every new matching headline is news
            return bool(fresh), f"{addr}, news you asked me to watch: " + "; ".join(fresh[:3])
        if t == "command":
            r = subprocess.run(w["command"], shell=True, capture_output=True, text=True, timeout=60)
            return r.returncode != 0, w.get("message") or f"{addr}, check failed: {w['command']} (exit {r.returncode})."
        return False, ""

    def fire(self, source: str, message: str):
        self.app.db.x("INSERT INTO alerts(ts, source, message) VALUES(?,?,?)", (time.time(), source, message))
        notify("Jarvis", message)

    def run_due(self, log=print, force: bool = False) -> list[str]:
        fired = []
        for w in self.list():
            k = self.key(w)
            if not force and time.time() - self.last.get(k, 0) < _interval(w.get("every", "5m")):
                continue
            self.last[k] = time.time()
            try:
                alert, msg = self.check(w)
            except Exception as e:
                log(dim(f"[watch] {w.get('type')} check failed: {e}"))
                continue
            if alert and (not self.state.get(k) or force):
                self.fire(w.get("type", "watch"), msg)
                fired.append(msg)
            self.state[k] = alert
        return fired
