"""Morning digest built from weather, calendar, email and news."""

from __future__ import annotations

import datetime as dt
import threading
from typing import TYPE_CHECKING

from .common import HOME, TagFilter, strip_think
from .connectors import _ics_events_today, _imap_unread, get_news, get_weather
from .presets import PRESETS

if TYPE_CHECKING:
    from .app import App


def gather_digest(app: "App") -> dict[str, str]:
    cfg = app.cfg
    sections: dict[str, str] = {}
    jobs = {
        "weather": lambda: get_weather(cfg.get("digest.location", "")),
        "news": lambda: "\n".join(f"- [{s}] {t}" for s, t, _ in get_news(cfg.get("digest.feeds", []), 5)),
        "calendar": lambda: "\n".join(_ics_events_today(cfg.get("digest.ics_url"))) or "No events today."
        if cfg.get("digest.ics_url") else "",
        "email": lambda: "\n".join(_imap_unread(cfg)),
    }
    results: dict[str, str] = {}

    def run(k, fn):
        try:
            results[k] = fn() or ""
        except Exception as e:
            results[k] = f"(unavailable: {e})"
    threads = [threading.Thread(target=run, args=(k, f), daemon=True) for k, f in jobs.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    sections.update({k: v for k, v in results.items() if v})
    tasks = [f"{r['name']} at {r['next_run'][11:16]}" for r in app.scheduler.list()
             if r["next_run"] and r["next_run"][:10] == dt.date.today().isoformat()]
    if tasks:
        sections["scheduled"] = "\n".join(tasks)
    facts = [r["text"] for r in app.memory.facts(8)]
    if facts:
        sections["about_user"] = "\n".join(facts)
    return sections


def run_digest(app: "App", on_token=None, quiet=False) -> str:
    data = gather_digest(app)
    name = app.cfg.get("agent.user_name") or "there"
    now = dt.datetime.now()
    ctx = "\n\n".join(f"## {k}\n{v}" for k, v in data.items())
    prompt = (f"Write a friendly morning briefing for {name} for {now:%A %d %B %Y}. Start with a one-line greeting, "
              "then cover: weather (with a practical tip), today's calendar, important unread email, a few top "
              "headlines, and scheduled tasks. Skip any section with no data. Keep it under 250 words, natural "
              "to read aloud, using short Markdown sections.\n\nDATA:\n" + ctx)
    msgs = [{"role": "system", "content": PRESETS["digest"]["prompt"]}, {"role": "user", "content": prompt}]
    filt = TagFilter()
    res = app.router.chat(msgs, on_token=(lambda s: on_token(filt.feed(s))) if on_token else None, session_id="digest")
    if on_token:
        on_token(filt.flush())
    text = strip_think(res.content)
    out = HOME / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{now:%Y%m%d-%H%M}-digest.md").write_text(text, "utf-8")
    return text
