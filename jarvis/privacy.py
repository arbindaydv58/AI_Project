"""PII redaction applied before text is sent to a cloud model."""

from __future__ import annotations

import re


PII_PATTERNS = [
    ("API_KEY", re.compile(r"\b(?:sk-(?:ant-)?[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
                           r"|xox[abposr]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{35})\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IP", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("PHONE", re.compile(r"(?<![\w.])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,3}(?![\w.])"
                         r"|(?<![\w.])\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?![\w.])")),
]


def _luhn(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, int]:
    n = 0
    for label, rx in PII_PATTERNS:
        def rep(m, label=label):
            nonlocal n
            if label == "CARD" and not _luhn(m.group(0)):
                return m.group(0)
            n += 1
            return f"[{label}]"
        text = rx.sub(rep, text)
    return text, n


def has_pii(text: str) -> bool:
    return redact(text)[1] > 0


def redact_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        m2 = dict(m)
        if m.get("role") != "system" and isinstance(m.get("content"), str):
            m2["content"] = redact(m["content"])[0]
        out.append(m2)
    return out
