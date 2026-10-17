"""File tools: list, read, write and search inside the allowed folders."""

from __future__ import annotations

import re

from ..common import truncate
from ..documents import SKIP_DIRS, extract_text
from ..registry import S, tool


@tool("list_dir", "List files and folders in a directory (inside the allowed folders).",
      {"path": S("Directory path; default is the working folder")})
def t_list_dir(ag, path: str = "."):
    p = ag.app.security.path(path)
    if not p.is_dir():
        return f"Error: {p} is not a directory"
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    lines = [f"{p}:"]
    for it in items[:300]:
        try:
            if it.is_dir():
                lines.append(f"  [dir]  {it.name}/")
            else:
                lines.append(f"  {it.stat().st_size:>10,}  {it.name}")
        except OSError:
            continue
    if len(items) > 300:
        lines.append(f"  ... and {len(items) - 300} more")
    return "\n".join(lines)


@tool("read_file", "Read a text file (also .docx, .ipynb, .pdf if pypdf is installed). Returns numbered lines.",
      {"path": S("File path"), "start_line": S("First line to read (1-based)", "integer"),
       "max_lines": S("Maximum number of lines (default 400)", "integer")}, ["path"])
def t_read_file(ag, path: str, start_line: int = 1, max_lines: int = 400):
    p = ag.app.security.path(path)
    if not p.is_file():
        return f"Error: file not found: {p}"
    text = extract_text(p, max_bytes=20_000_000)
    if not text:
        return f"Error: {p.name} is binary, empty, or an unsupported format."
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), start - 1 + max(1, int(max_lines or 400)))
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n[showing lines {start}-{end} of {len(lines)}]" if end < len(lines) or start > 1 else ""
    return truncate(body + more, 20000)


@tool("write_file", "Create or overwrite a text file (or append to it). Creates parent folders.",
      {"path": S("File path"), "content": S("Full file content"),
       "append": S("Append instead of overwrite", "boolean")}, ["path", "content"], risky=True)
def t_write_file(ag, path: str, content: str, append: bool = False):
    p = ag.app.security.path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return f"{'Appended' if append else 'Wrote'} {len(content):,} chars to {p}"


@tool("search_files", "Search file contents with a regex (like grep) under a folder.",
      {"pattern": S("Regular expression or plain text"), "path": S("Folder to search (default working folder)"),
       "file_glob": S("Filename filter like *.py (default all text files)")}, ["pattern"])
def t_search_files(ag, pattern: str, path: str = ".", file_glob: str = "*"):
    root = ag.app.security.path(path)
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits, scanned = [], 0
    files = [root] if root.is_file() else root.rglob(file_glob or "*")
    for f in files:
        if len(hits) >= 120 or scanned > 20000:
            break
        if not f.is_file() or any(part in SKIP_DIRS for part in f.parts):
            continue
        try:
            if f.stat().st_size > 2_000_000:
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:2048]:
            continue
        scanned += 1
        for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f.relative_to(root) if root.is_dir() else f.name}:{i}: {line.strip()[:200]}")
                if len(hits) >= 120:
                    break
    return "\n".join(hits) if hits else f"No matches for /{pattern}/ in {scanned} files."
