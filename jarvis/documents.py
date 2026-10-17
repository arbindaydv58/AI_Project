"""Text extraction and chunking for indexed documents."""

from __future__ import annotations

import html
import json
import re
import zipfile
from pathlib import Path

from .http_utils import html_to_text


TEXT_EXT = set(".txt .md .markdown .rst .py .js .ts .tsx .jsx .json .yaml .yml .toml .ini .cfg .conf .csv .tsv "
               ".html .htm .xml .java .c .h .cpp .hpp .cs .go .rs .rb .php .sh .bash .zsh .ps1 .bat .sql .log "
               ".tex .org .swift .kt .lua .r .scala .css .scss .vue .svelte .ipynb .docx .pdf .eml .srt .vtt".split())
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox", "dist", "build", ".idea",
             ".vscode", ".cache", ".mypy_cache", ".pytest_cache", "target", ".next"}


def extract_text(path: Path, max_bytes: int = 8_000_000) -> str:
    ext = path.suffix.lower()
    try:
        if path.stat().st_size > max_bytes:
            return ""
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                x = z.read("word/document.xml").decode("utf-8", "replace")
            x = x.replace("</w:p>", "\n").replace("<w:tab/>", "\t")
            return html.unescape(re.sub(r"<[^>]+>", "", x))
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # optional
            except ImportError:
                return ""
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        if ext == ".ipynb":
            nb = json.loads(path.read_text("utf-8", "replace"))
            return "\n\n".join("".join(c.get("source", [])) for c in nb.get("cells", []))
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            return ""
        text = raw.decode("utf-8", "replace")
        if ext in (".html", ".htm"):
            title, body = html_to_text(text)
            return (title + "\n\n" + body).strip()
        return text
    except Exception:
        return ""


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:
            if cur:
                chunks.append(cur)
                cur = cur[-overlap:]
            chunks.append((cur + "\n" + p[:size]).strip())
            cur, p = "", p[size - overlap:]
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n\n" + p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        chunks.append(cur.strip())
    return chunks
