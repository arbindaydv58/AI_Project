"""Setup checks and the first-run wizard."""

from __future__ import annotations

import platform
import sqlite3
import sys
from typing import TYPE_CHECKING

from .common import HOME, VERSION, bold, dim, green, red, strip_think, yellow
from .energy import EnergyMeter
from .errors import EngineError
from .protocols import all_protocols
from .speech import tts_command, whisper_available

if TYPE_CHECKING:
    from .app import App


def doctor(app: App):
    ok, warn, bad = green("✓"), yellow("!"), red("✗")
    print(bold(f"Jarvis doctor  (v{VERSION}, data in {HOME})\n"))
    v = sys.version_info
    print(f" {ok if v >= (3, 9) else bad} Python {platform.python_version()} on {platform.system()} {platform.machine()}")
    print(f" {ok if app.db.fts else warn} SQLite {sqlite3.sqlite_version} "
          f"{'with FTS5 full-text search' if app.db.fts else '(no FTS5: keyword search falls back to a slower scan)'}")
    print(f" {ok if app.cfg.path.exists() else warn} Config: {app.cfg.path}" +
          ("" if app.cfg.path.exists() else "  (not created yet: run `jarvis init`)"))
    loc = app.router.local
    up = loc is not None and loc.available()
    print(f" {ok if up else bad} Local engine ({app.cfg.get('engine.local.provider')}) at {getattr(loc, 'url', '?')}: "
          f"{'online' if up else 'NOT reachable - install/start Ollama: https://ollama.com'}")
    if up:
        try:
            models = loc.list_models()
            print(f"    models installed: {', '.join(models) or 'none'}")
            try:
                print(f" {ok} Chat model: {app.router.local_model()}")
            except EngineError as e:
                print(f" {bad} {e}")
            em = app.cfg.get("engine.embed_model")
            has_em = any(n.split(":")[0] == em.split(":")[0] for n in models)
            print(f" {ok if has_em else warn} Embedding model '{em}': " +
                  ("installed (semantic memory on)" if has_em else f"missing - optional: ollama pull {em}"))
        except Exception as e:
            print(f" {bad} could not list models: {e}")
    cloud = app.router.cloud
    print(f" {ok if cloud else warn} Cloud engine: " + (f"{cloud.name} / {app.router.cloud_model()}" if cloud else
          f"not configured (optional: set {app.cfg.get('engine.cloud.api_key_env')})"))
    print(f" {ok} Routing mode: {app.cfg.get('engine.routing')}  · PII redaction for cloud: "
          f"{app.cfg.get('security.redact_pii_for_cloud')}")
    print(f" {ok} Energy measurement: {EnergyMeter.methods(app.cfg)}")
    print(f" {ok} Allowed folders: {', '.join(map(str, app.security.allowed))}")
    tts = tts_command()
    print(f" {ok if tts else warn} Text-to-speech: {tts[0] if tts else 'none found (Linux: install espeak-ng)'}")
    print(f" {ok} Voice in web app: built in (🎙 button) · local Whisper speech recognition: " +
          ("installed" if whisper_available() else dim("not installed (optional: pip install faster-whisper)")))
    print(f" {ok} Persona: {app.cfg.get('agent.preset')} · addresses you as “{app.cfg.get('agent.address')}” · "
          f"{len(all_protocols(app))} protocols · {len(app.watchers.list())} watchers")
    nf, nc = app.memory.doc_stats()
    print(f" {ok} Memory: {app.db.q('SELECT COUNT(*) FROM facts')[0][0]} facts · {nf} documents ({nc} chunks) · "
          f"{len(app.scheduler.list())} scheduled tasks")
    if up:
        print(dim("\nQuick model test..."))
        try:
            r = app.router.chat([{"role": "user", "content": "Reply with exactly: OK"}], session_id="doctor")
            print(f" {ok} Model replied: {strip_think(r.content)[:40]!r} in {r.latency:.1f}s")
        except Exception as e:
            print(f" {bad} Model test failed: {e}")


def init_wizard(app: App):
    cfg = app.cfg
    print(bold("Jarvis setup\n"))

    def ask(q, default=""):
        try:
            a = input(f"{q}{dim(f' [{default}]') if default else ''}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        return a or default
    cfg.set("agent.user_name", ask("Your name", cfg.get("agent.user_name") or ""))
    cfg.set("agent.address", ask("How should Jarvis address you (sir / ma'am / boss / your name)",
                                 cfg.get("agent.address") or "sir"))
    loc = app.router.local
    if loc and loc.available():
        models = [m for m in loc.list_models() if "embed" not in m]
        if models:
            print("Installed models:")
            for i, m in enumerate(models, 1):
                print(f"  {i}. {m}")
            pick = ask("Choose default model number (or 'auto')", "auto")
            cfg.set("engine.local.model", models[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(models) else "auto")
        else:
            print(yellow("Ollama is running but no models are installed. Run: ollama pull qwen3:4b"))
    else:
        print(yellow("Ollama not detected at " + getattr(loc, "url", "?") + ". Install it from https://ollama.com"))
    cfg.set("engine.routing", ask("Routing (local / auto / cloud)", cfg.get("engine.routing")))
    cfg.set("digest.location", ask("Your city for weather (blank = auto)", cfg.get("digest.location") or ""))
    cfg.set("agent.preset", ask("Default preset (lite / jarvis / chat / code / research)", cfg.get("agent.preset")))
    cfg.save()
    print(green(f"\nSaved {cfg.path}. Run `python main.py doctor` to verify, then `python main.py`."))
