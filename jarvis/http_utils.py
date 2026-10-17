"""HTTP helpers built on urllib, plus HTML-to-text conversion."""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .common import VERSION
from .errors import EngineError, EngineUnavailable


UA = f"Mozilla/5.0 (compatible; Jarvis/{VERSION})"


def http_open(url: str, data: Any = None, headers: dict | None = None, timeout: float = 60,
              method: str | None = None):
    h = {"User-Agent": UA}
    body = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        raise EngineError(f"HTTP {e.code} from {url.split('?')[0]}: {detail}") from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise EngineUnavailable(f"Cannot reach {url.split('?')[0]}: {reason}") from None


def http_json(url, data=None, headers=None, timeout=60, method=None):
    with http_open(url, data, headers, timeout, method) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else None


def http_text(url, timeout=20, max_bytes=3_000_000, headers=None) -> tuple[str, str]:
    with http_open(url, headers=headers, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read(max_bytes)
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    try:
        return raw.decode(charset, "replace"), ctype
    except LookupError:
        return raw.decode("utf-8", "replace"), ctype


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
             "article", "header", "footer", "pre", "blockquote", "table", "ul", "ol", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(s: str) -> tuple[str, str]:
    p = _HTMLText()
    try:
        p.feed(s)
    except Exception:
        pass
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return p.title.strip(), text.strip()
