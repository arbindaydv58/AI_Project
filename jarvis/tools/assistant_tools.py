"""Scheduling, system info, weather, news and desktop notifications."""

from __future__ import annotations

from ..connectors import get_news, get_weather
from ..registry import S, tool
from ..sysutils import _sysinfo, notify


@tool("schedule_task", "Schedule a recurring or one-off task that Jarvis will run in the background "
      "(requires `jarvis daemon` or `jarvis serve` to be running).",
      {"name": S("Short name"), "schedule": S("e.g. 'every 30m', 'hourly', 'daily 08:00', 'weekdays 07:30', "
                                               "'mon 09:00', 'once 2026-12-24 18:00'"),
       "prompt": S("What Jarvis should do each time"),
       "preset": S("Agent preset: lite, research, chat or digest")}, ["name", "schedule", "prompt"], risky=True)
def t_schedule(ag, name: str, schedule: str, prompt: str, preset: str = "lite"):
    tid = ag.app.scheduler.add(name, schedule, prompt, preset or "lite")
    row = ag.app.db.q("SELECT next_run FROM tasks WHERE id=?", (tid,))[0]
    return f"Scheduled task #{tid} '{name}' ({schedule}); next run {row[0]}."


@tool("list_tasks", "List scheduled background tasks.")
def t_list_tasks(ag):
    rows = ag.app.scheduler.list()
    return "\n".join(f"#{r['id']} {r['name']} [{r['schedule']}] next={r['next_run']} enabled={bool(r['enabled'])}"
                     for r in rows) or "No scheduled tasks."


@tool("system_info", "Get information about this computer: OS, CPU, memory, disk, battery.")
def t_sysinfo(ag):
    return _sysinfo()


@tool("get_weather", "Current weather and 3-day forecast.", {"location": S("City name; blank = auto-detect")})
def t_weather(ag, location: str = ""):
    return get_weather(location or ag.app.cfg.get("digest.location", ""))


@tool("get_news", "Latest headlines from the user's configured news feeds (or a given RSS/Atom feed URL).",
      {"feed": S("Optional RSS/Atom URL"), "max_items": S("Max headlines per feed (default 6)", "integer")})
def t_news(ag, feed: str = "", max_items: int = 6):
    feeds = [feed] if feed else ag.app.cfg.get("digest.feeds", [])
    items = get_news(feeds, max(1, min(int(max_items or 6), 20)))
    return "\n".join(f"- [{s}] {t} ({l})" for s, t, l in items) or "No headlines (feeds unreachable?)."


@tool("notify", "Show a desktop notification to the user.", {"title": S("Title"), "message": S("Body")}, ["message"])
def t_notify(ag, message: str, title: str = "Jarvis"):
    notify(title, message)
    return "Notification shown."
