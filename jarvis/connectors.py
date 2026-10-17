"""Data sources for the weather, news, calendar and email digest."""

from __future__ import annotations

import datetime as dt
import email.header
import imaplib
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .http_utils import http_json, http_text

if TYPE_CHECKING:
    from .config import Config


def get_weather(location: str = "") -> str:
    d = http_json(f"https://wttr.in/{urllib.parse.quote(location or '')}?format=j1", timeout=15) or {}
    cur = d["current_condition"][0]
    area = (d.get("nearest_area") or [{}])[0]
    place = ", ".join(x[0]["value"] for x in (area.get("areaName"), area.get("country")) if x) or location
    out = [f"{place}: {cur['weatherDesc'][0]['value']}, {cur['temp_C']}°C (feels {cur['FeelsLikeC']}°C), "
           f"humidity {cur['humidity']}%, wind {cur['windspeedKmph']} km/h"]
    for day in d.get("weather", [])[:3]:
        rain = max(int(h.get("chanceofrain", 0)) for h in day.get("hourly", [{"chanceofrain": 0}]))
        desc = day["hourly"][len(day["hourly"]) // 2]["weatherDesc"][0]["value"] if day.get("hourly") else ""
        out.append(f"{day['date']}: {desc}, {day['mintempC']}-{day['maxtempC']}°C, rain chance {rain}%")
    return "\n".join(out)


def get_news(feeds: list[str], per_feed: int = 5) -> list[tuple[str, str, str]]:
    items = []
    for url in feeds:
        try:
            text, _ = http_text(url, timeout=15, max_bytes=2_000_000)
            root = ET.fromstring(text.encode("utf-8", "replace"))
        except Exception:
            continue
        src = ""
        for el in root.iter():
            if el.tag.split("}")[-1] == "title" and el.text:
                src = el.text.strip()
                break
        n = 0
        for el in root.iter():
            if el.tag.split("}")[-1] not in ("item", "entry"):
                continue
            title = link = ""
            for ch in el:
                t = ch.tag.split("}")[-1]
                if t == "title":
                    title = (ch.text or "").strip()
                elif t == "link":
                    link = (ch.text or ch.get("href") or "").strip()
            if title:
                items.append((src, title, link))
                n += 1
            if n >= per_feed:
                break
    return items


def _ics_events_today(url: str) -> list[str]:
    text, _ = http_text(url, timeout=20, max_bytes=8_000_000)
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold lines
    today = dt.date.today()
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields = {}
        for line in block.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.split(";")[0].upper()] = (k, v.strip())
        if "DTSTART" not in fields:
            continue
        key, val = fields["DTSTART"]
        try:
            if "VALUE=DATE" in key.upper() or len(val) == 8:
                start = dt.datetime.strptime(val[:8], "%Y%m%d")
                when = "all day"
            elif val.endswith("Z"):
                start = dt.datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
                when = start.strftime("%H:%M")
            else:
                start = dt.datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
                when = start.strftime("%H:%M")
        except ValueError:
            continue
        rrule = fields.get("RRULE", ("", ""))[1]
        hit = start.date() == today
        if not hit and rrule and start.date() <= today:
            if "FREQ=DAILY" in rrule:
                hit = True
            elif "FREQ=WEEKLY" in rrule:
                byday = re.search(r"BYDAY=([A-Z,]+)", rrule)
                codes = byday.group(1).split(",") if byday else [["MO", "TU", "WE", "TH", "FR", "SA", "SU"][start.weekday()]]
                hit = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][today.weekday()] in codes
            m = re.search(r"UNTIL=(\d{8})", rrule)
            if hit and m and dt.datetime.strptime(m.group(1), "%Y%m%d").date() < today:
                hit = False
        if hit:
            summ = fields.get("SUMMARY", ("", "(no title)"))[1].replace("\\,", ",")
            events.append((when, summ))
    events.sort(key=lambda e: (e[0] != "all day", e[0]))
    return [f"{w} - {s}" for w, s in events]


def _imap_unread(cfg: Config) -> list[str]:
    c = cfg.get("digest.imap") or {}
    pw = os.environ.get(c.get("password_env") or "", "")
    if not (c.get("host") and c.get("user") and pw):
        return []
    M = imaplib.IMAP4_SSL(c["host"])
    try:
        M.login(c["user"], pw)
        M.select(c.get("folder", "INBOX"), readonly=True)
        _, data = M.search(None, "UNSEEN")
        ids = data[0].split()[-int(c.get("max", 10)):]
        out = []
        for i in reversed(ids):
            _, msg = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            raw = msg[0][1].decode("utf-8", "replace") if msg and msg[0] else ""
            hdr = {}
            for line in re.sub(r"\r?\n[ \t]", " ", raw).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdr[k.lower()] = str(email.header.make_header(email.header.decode_header(v.strip())))
            out.append(f"From {hdr.get('from', '?')}: {hdr.get('subject', '(no subject)')}")
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass
