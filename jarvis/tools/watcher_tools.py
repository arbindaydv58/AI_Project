"""Tools for proactive watchers and recent alerts."""

from __future__ import annotations

import datetime as dt

from ..registry import S, tool


@tool("add_watcher", "Ask Jarvis to keep an eye on something and alert the user proactively. Types: "
      "battery {below}, disk {below_gb}, memory {above_pct}, url {url}, rss {keywords:[...], feed?}. "
      "Optional 'every' like '5m' or '1h'.",
      {"watcher": S("Watcher object, e.g. {\"type\": \"url\", \"url\": \"https://example.com\", \"every\": \"5m\"}",
                    "object")}, ["watcher"], risky=True)
def t_add_watcher(ag, watcher: dict):
    if not isinstance(watcher, dict) or watcher.get("type") not in ("battery", "disk", "memory", "url", "rss"):
        return "Error: unsupported watcher type (command watchers can only be added by the user in config)."
    ws = list(ag.app.cfg.get("watchers") or []) + [watcher]
    ag.app.cfg.set("watchers", ws)
    ag.app.cfg.save()
    return f"Watching: {ag.app.watchers.describe(watcher)}. (Alerts fire while `jarvis serve` or `jarvis daemon` runs.)"


@tool("recent_alerts", "Show recent proactive alerts.")
def t_recent_alerts(ag):
    rows = ag.app.db.q("SELECT ts, message FROM alerts ORDER BY id DESC LIMIT 10")
    return "\n".join(f"{dt.datetime.fromtimestamp(r['ts']):%m-%d %H:%M} {r['message']}" for r in rows) or "No alerts."
