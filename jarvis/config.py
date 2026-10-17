"""Default settings and the Config class."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .common import HOME, yellow


DEFAULT_CONFIG: dict = {
    "engine": {
        "routing": "local",            # local | cloud | auto
        "cloud_fallback": True,        # use cloud if the local engine is down
        "local": {"provider": "ollama", "url": "http://127.0.0.1:11434", "model": "qwen3:4b-instruct",
                  "api_key_env": ""},
        # provider: ollama | openai (any OpenAI-compatible server) | anthropic
        "cloud": {"provider": "anthropic", "url": "", "model": "claude-sonnet-5-5",
                  "api_key_env": "ANTHROPIC_API_KEY"},
        "embed_model": "nomic-embed-text",
        "embed_min_similarity": 0.47,  # matches below this cosine similarity are ignored (measured for nomic-embed-text)
        "temperature": 0.4,
        "num_ctx": 8192,
        "num_thread": 0,               # CPU threads for Ollama; 0 = let Ollama decide
        "keep_alive": "30m",           # how long Ollama keeps the model in memory after the last question
        "think": None,                # None = model default; True/False for reasoning models
        "timeout": 300,
        "auto_threshold": 3,           # complexity score that escalates to cloud in auto mode
    },
    "agent": {"preset": "lite", "max_steps": 8, "history_chars": 24000,
              "user_name": "", "address": "sir",   # how Jarvis addresses you: sir, ma'am, boss, your name...
              "persona": "", "show_thinking": False},
    "protocols": {},                   # your own named routines (see `jarvis protocol --help`)
    "watchers": [                      # proactive alerts; checked while `serve`/`gui`/`daemon` runs
        {"type": "battery", "below": 20, "every": "5m"},
        {"type": "disk", "below_gb": 5, "every": "30m"},
    ],
    "security": {
        "allowed_dirs": [str(HOME / "workspace"), "."],   # "." = folder you launch Jarvis from
        "auto_approve": [],            # risky tools you trust to run without asking
        "redact_pii_for_cloud": True,
        "allow_shell": True,
        "allow_private_fetch": False,  # let fetch_url reach localhost / LAN
        "tool_timeout": 60,
    },
    "search": {"searxng_url": ""},     # optional self-hosted SearxNG instance
    "telemetry": {"measure_energy": True},
    "server": {"host": "127.0.0.1", "port": 8765, "token": ""},
    "digest": {
        "location": "",                # blank = auto-detect by IP
        "feeds": ["https://feeds.bbci.co.uk/news/world/rss.xml", "https://hnrss.org/frontpage"],
        "ics_url": "",                 # e.g. Google Calendar "secret address in iCal format"
        "imap": {"host": "", "user": "", "password_env": "JARVIS_IMAP_PASSWORD",
                 "folder": "INBOX", "max": 10},
    },
    "voice": {
        "speak": False,                # read answers aloud in the terminal
        "wake_word": "jarvis",
        "stt": "auto",                 # auto | browser | local  (local = faster-whisper on this machine)
        "whisper_model": "base",       # tiny | base | small | medium | large-v3
        "language": "",                # e.g. "en", "ne", "hi"; blank = auto-detect
    },
    "pricing": {},                     # {"model-name": [usd_per_M_input, usd_per_M_output]}
}


def deep_merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else HOME / "config.json"
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except Exception as e:
                print(yellow(f"Warning: could not read {self.path}: {e} (using defaults)"))
        self.data = deep_merge(DEFAULT_CONFIG, data)

    def get(self, dotted: str, default=None):
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass
        parts = dotted.split(".")
        cur = self.data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def __getitem__(self, k):
        return self.data[k]
