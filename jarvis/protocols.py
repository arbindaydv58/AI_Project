"""Protocols: named routines such as 'initiate night protocol'."""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Callable

from .common import strip_think
from .digest import run_digest
from .registry import TOOLS

if TYPE_CHECKING:
    from .agent import Agent
    from .app import App


BUILTIN_PROTOCOLS: dict[str, dict] = {
    "status": {"description": "Full status report: computer, tasks and alerts",
               "steps": [{"tool": "system_info"}, {"tool": "list_tasks"}, {"alerts": True},
                         {"ask": "Give me a crisp spoken status report from the data above in 2-4 sentences. "
                                 "Mention anything that needs my attention first."}]},
    "morning": {"description": "Morning briefing: weather, calendar, email, news", "steps": [{"digest": True}]},
    "night": {"description": "A look at tomorrow and a good night",
              "steps": [{"tool": "list_tasks"},
                        {"ask": "Wish me good night in one or two sentences, mentioning anything scheduled for tomorrow."}]},
    "focus": {"description": "Focus mode: reminder in 50 minutes",
              "steps": [{"remind_in_minutes": 50, "message": "Focus session complete. Time for a short break."},
                        {"say": "Focus mode engaged. I'll hold your questions and remind you in fifty minutes."}]},
}

PROTOCOL_RE = re.compile(r"\b(?:initiate|initialise|initialize|run|start|execute|activate|engage|begin|launch)\s+"
                         r"(?:the\s+)?([a-z0-9][a-z0-9 _-]{0,40}?)\s+protocol\b", re.I)


def all_protocols(app: "App") -> dict[str, dict]:
    out = {k: dict(v, builtin=True) for k, v in BUILTIN_PROTOCOLS.items()}
    for k, v in (app.cfg.get("protocols") or {}).items():
        out[k.lower().strip()] = dict(v, builtin=False)
    return out


def match_protocol(app: "App", name: str) -> str | None:
    name = re.sub(r"[\s_-]+", " ", (name or "").lower()).strip()
    protos = all_protocols(app)
    if name in protos:
        return name
    for k in protos:
        if re.sub(r"[\s_-]+", " ", k) == name or name.endswith(" " + k):
            return k
    return None


def run_protocol(ag: "Agent", name: str, on_line: Callable[[str], None] | None = None) -> str:
    app = ag.app
    p = all_protocols(app).get(name)
    if not p:
        return f"No protocol named '{name}'."
    collected: list[str] = []
    said: list[str] = []
    final = ""
    every_tool = dict(TOOLS)
    seen: Counter = Counter()
    addr = app.cfg.get("agent.address") or "sir"
    for step in p.get("steps", []):
        try:
            if "tool" in step:
                out = ag._execute({"name": step["tool"], "arguments": step.get("args") or {}}, every_tool, seen)
                collected.append(f"[{step['tool']}]\n{out}")
            elif step.get("alerts"):
                rows = app.db.q("SELECT message, ts FROM alerts WHERE ts>? ORDER BY id DESC LIMIT 10", (time.time() - 86400,))
                collected.append("[alerts in last 24h]\n" + ("\n".join(f"- {r['message']}" for r in rows) or "none"))
            elif step.get("digest"):
                final = run_digest(app)
            elif "remind_in_minutes" in step:
                when = dt.datetime.now() + dt.timedelta(minutes=float(step["remind_in_minutes"]))
                app.db.x("INSERT INTO tasks(name,prompt,schedule,preset,next_run,enabled) VALUES(?,?,?,?,?,1)",
                         (f"Reminder: {step.get('message', '')[:40]}", f"Remind me: {step.get('message', '')}",
                          f"once {when:%Y-%m-%d %H:%M}", "reminder", when.isoformat(timespec="seconds")))
                collected.append(f"[reminder set for {when:%H:%M}]")
            elif "say" in step:
                said.append(step["say"].replace("{address}", addr))
            elif "ask" in step:
                ctx = "\n\n".join(collected) or "(no data)"
                msgs = [{"role": "system", "content": ag.system_prompt(step["ask"])},
                        {"role": "user", "content": f"[Current date and time: {ag.clock()}]\n"
                                                    f"Protocol '{name}' gathered this data:\n\n{ctx}\n\n{step['ask']}"}]
                res = app.router.chat(msgs, session_id=ag.session_id)
                final = strip_think(res.content)
            if on_line:
                on_line(step.get("tool") or step.get("say") or next(iter(step)))
        except Exception as e:
            collected.append(f"[step failed: {e}]")
    text = " ".join(said + ([final] if final else []))
    if not text:
        text = f"{name.title()} protocol complete, {addr}.\n\n" + "\n\n".join(collected)
    return text
