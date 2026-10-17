"""Text-to-speech and optional local speech recognition (faster-whisper)."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import threading
from typing import TYPE_CHECKING

from .common import dim

if TYPE_CHECKING:
    from .config import Config


def strip_markdown(s: str) -> str:
    s = re.sub(r"```.*?```", " (code omitted) ", s, flags=re.S)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[*_#>|]+", "", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def tts_command() -> list[str] | None:
    sysname = platform.system()
    if sysname == "Darwin" and shutil.which("say"):
        return ["say", "-f", "-"]
    if sysname == "Windows":
        return ["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "$s.Speak([Console]::In.ReadToEnd())"]
    for c in ("espeak-ng", "espeak"):
        if shutil.which(c):
            return [c, "--stdin"]
    if shutil.which("spd-say"):
        return ["spd-say", "-e", "-w"]
    return None


def speak(text: str):
    cmd = tts_command()
    if not cmd:
        print(dim("(no text-to-speech engine found: install espeak-ng on Linux)"))
        return
    p = None
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        p.communicate(strip_markdown(text).encode("utf-8"), timeout=600)
    except KeyboardInterrupt:
        if p:
            p.kill()
    except Exception:
        pass


# -- Speech recognition (local, optional: pip install faster-whisper) ---------------

_WHISPER: dict = {"model": None, "name": None, "lock": threading.Lock()}


def whisper_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def transcribe_file(path: str, cfg: Config) -> str:
    from faster_whisper import WhisperModel  # optional dependency
    name = cfg.get("voice.whisper_model", "base") or "base"
    with _WHISPER["lock"]:
        if _WHISPER["model"] is None or _WHISPER["name"] != name:
            print(dim(f"[voice] loading Whisper '{name}' (first time downloads the model)..."))
            _WHISPER["model"] = WhisperModel(name, device="auto", compute_type="default")
            _WHISPER["name"] = name
        segments, _ = _WHISPER["model"].transcribe(path, beam_size=1, vad_filter=True,
                                                   language=cfg.get("voice.language") or None)
        return " ".join(s.text.strip() for s in segments).strip()
