"""Scheduled background tasks."""

from __future__ import annotations

import datetime as dt
import re
import threading
from typing import TYPE_CHECKING

from .agent import Agent
from .common import HOME, dim, red, yellow
from .digest import run_digest
from .speech import strip_markdown

if TYPE_CHECKING:
    from .app import App


DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def next_run(schedule: str, after: dt.datetime | None = None) -> dt.datetime | None:
    after = after or dt.datetime.now()
    s = schedule.strip().lower()
    m = re.fullmatch(r"every\s+(\d+)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?)", s)
    if m:
        n, u = int(m.group(1)), m.group(2)[0]
        delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n), "d": dt.timedelta(days=n)}[u]
        if delta < dt.timedelta(minutes=1):
            raise ValueError("minimum interval is 1 minute")
        return after + delta
    if s == "hourly":
        return after + dt.timedelta(hours=1)
    m = re.fullmatch(r"(daily|every day|weekdays|weekends|[a-z]+)\s+(?:at\s+)?(\d{1,2}):(\d{2})", s)
    if m:
        word, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        if word in ("daily", "every day"):
            days = set(range(7))
        elif word == "weekdays":
            days = {0, 1, 2, 3, 4}
        elif word == "weekends":
            days = {5, 6}
        elif word[:3] in DAYS:
            days = {DAYS[word[:3]]}
        else:
            raise ValueError(f"unknown day '{word}'")
        for i in range(8):
            cand = (after + dt.timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand > after and cand.weekday() in days:
                return cand
    m = re.fullmatch(r"once\s+(\d{4}-\d{2}-\d{2})[ t](\d{1,2}:\d{2})", s)
    if m:
        t = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        return t if t > after else None
    raise ValueError("Unrecognised schedule. Use e.g. 'every 30m', 'hourly', 'daily 08:00', 'weekdays 07:30', "
                     "'mon 09:00', 'once 2026-12-24 18:00'.")


class Scheduler:
    def __init__(self, app: "App"):
        self.app, self.db = app, app.db

    def add(self, name, schedule, prompt, preset="lite") -> int:
        nr = next_run(schedule)
        if nr is None:
            raise ValueError("That time is in the past.")
        return self.db.x("INSERT INTO tasks(name,prompt,schedule,preset,next_run,enabled) VALUES(?,?,?,?,?,1)",
                         (name, prompt, schedule, preset, nr.isoformat(timespec="seconds")))

    def list(self):
        return self.db.q("SELECT * FROM tasks ORDER BY id")

    def remove(self, tid: int):
        self.db.x("DELETE FROM tasks WHERE id=?", (tid,))

    def due(self):
        return self.db.q("SELECT * FROM tasks WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?",
                         (dt.datetime.now().isoformat(timespec="seconds"),))

    def run_task(self, row, notify_user=True) -> str:
        nr = None
        try:
            nr = next_run(row["schedule"])
        except ValueError:
            pass
        # advance first so a crash can't cause a tight retry loop
        self.db.x("UPDATE tasks SET next_run=?, enabled=? WHERE id=?",
                  (nr.isoformat(timespec="seconds") if nr else None, 1 if nr else 0, row["id"]))
        try:
            if row["preset"] == "reminder":
                msg = re.sub(r"^Remind me:\s*", "", row["prompt"])
                self.db.x("UPDATE tasks SET last_run=?, last_result=? WHERE id=?",
                          (dt.datetime.now().isoformat(timespec="seconds"), msg, row["id"]))
                if notify_user:
                    self.app.watchers.fire("reminder", f"{(self.app.cfg.get('agent.address') or 'sir').capitalize()}, {msg}")
                return msg
            if row["preset"] == "digest":
                result = run_digest(self.app, quiet=True)
            else:
                ag = Agent(self.app, preset=row["preset"], session_id=f"task-{row['id']}",
                           approver=lambda n, a: n in (self.app.cfg.get("security.auto_approve") or []))
                result = ag.run(f"[Scheduled task '{row['name']}'] {row['prompt']}")
        except Exception as e:
            result = f"Task failed: {e}"
        self.db.x("UPDATE tasks SET last_run=?, last_result=? WHERE id=?",
                  (dt.datetime.now().isoformat(timespec="seconds"), result, row["id"]))
        out = HOME / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        fn = out / f"{dt.datetime.now():%Y%m%d-%H%M}-{re.sub(r'[^A-Za-z0-9]+', '-', row['name'])[:40]}.md"
        fn.write_text(f"# {row['name']}\n\n_{dt.datetime.now():%Y-%m-%d %H:%M}_\n\n{result}\n", "utf-8")
        if notify_user:
            self.app.watchers.fire("task", f"{row['name']}: {strip_markdown(result)[:400]}")
        return result

    def loop(self, stop: threading.Event, log=print):
        log(dim(f"[scheduler] running - {len(self.list())} task(s), {len(self.app.watchers.list())} watcher(s)"))
        while not stop.is_set():
            try:
                for row in self.due():
                    log(dim(f"[scheduler] running task #{row['id']} {row['name']}"))
                    self.run_task(row)
            except Exception as e:
                log(red(f"[scheduler] error: {e}"))
            try:
                for msg in self.app.watchers.run_due(log=log):
                    log(yellow(f"[alert] {msg}"))
            except Exception as e:
                log(red(f"[watch] error: {e}"))
            stop.wait(15)
