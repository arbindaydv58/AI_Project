"""Shared constants, console colours and small text helpers."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


VERSION = "1.0.0"
HOME = Path(os.environ.get("JARVIS_HOME") or (Path.home() / ".jarvis")).expanduser()
IS_WIN = os.name == "nt"
LAUNCH_CWD = Path.cwd()

if IS_WIN:
    os.system("")  # enable ANSI colours on Windows 10+


class C:
    on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    @staticmethod
    def w(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if C.on else s


def dim(s): return C.w("2", s)
def bold(s): return C.w("1", s)
def cyan(s): return C.w("36", s)
def green(s): return C.w("32", s)
def yellow(s): return C.w("33", s)
def red(s): return C.w("31", s)
def magenta(s): return C.w("35", s)


def truncate(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + f"\n... [truncated {len(s) - n} chars]"


def short_args(args: Any, n: int = 90) -> str:
    try:
        s = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in (args or {}).items())
    except Exception:
        s = str(args)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "..."


def loads_lenient(s: Any) -> dict:
    if isinstance(s, dict):
        return s
    s = (s or "").strip() if isinstance(s, str) else ""
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {"_raw": v}
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"_raw": s}


THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_think(s: str) -> str:
    s = THINK_RE.sub("", s or "")
    if "<think>" in s:  # unterminated thinking block
        s = s.split("<think>")[0]
    if "</think>" in s:  # some thinking builds close the block without ever opening it
        s = s.rsplit("</think>", 1)[1]
    return s.strip()


class TagFilter:
    """Hides <think>...</think> (and similar) blocks from a token stream."""

    def __init__(self, tags=("think",)):
        self.tags, self.buf, self.inside = tags, "", None

    def feed(self, s: str) -> str:
        self.buf += s
        out = []
        while True:
            if self.inside:
                close = f"</{self.inside}>"
                i = self.buf.find(close)
                if i < 0:
                    self.buf = self.buf[-(len(close) - 1):]
                    break
                self.buf, self.inside = self.buf[i + len(close):], None
            else:
                hits = [(self.buf.find(f"<{t}>"), t) for t in self.tags]
                hits = [h for h in hits if h[0] >= 0]
                if not hits:
                    keep = 0
                    for t in self.tags:
                        o = f"<{t}>"
                        for k in range(len(o) - 1, 0, -1):
                            if self.buf.endswith(o[:k]):
                                keep = max(keep, k)
                                break
                    cut = len(self.buf) - keep
                    out.append(self.buf[:cut])
                    self.buf = self.buf[cut:]
                    break
                i, t = min(hits)
                out.append(self.buf[:i])
                self.buf, self.inside = self.buf[i + len(t) + 2:], t
        return "".join(out)

    def flush(self) -> str:
        r = "" if self.inside else self.buf
        self.buf, self.inside = "", None
        return r
