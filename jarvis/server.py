"""Web server: GUI, JSON API and the OpenAI-compatible endpoint."""

from __future__ import annotations

import base64
import datetime as dt
import http.server
import json
import os
import queue
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from .agent import Agent, warm_up
from .common import VERSION, TagFilter, bold, cyan, dim, strip_think, yellow
from .connectors import get_weather
from .presets import PRESETS
from .protocols import all_protocols
from .speech import transcribe_file, whisper_available
from .sysutils import _battery, cpu_percent, lan_ip, memory_info

if TYPE_CHECKING:
    from .app import App


GUI_HTML = Path(__file__).with_name("gui.html").read_text("utf-8")


_HUD_WEATHER: dict = {"t": 0.0, "text": "", "busy": False}


def hud_data(app: App) -> dict:
    d: dict = {"time": dt.datetime.now().isoformat(timespec="seconds"), "cpu": None, "mem": None,
               "battery": None, "charging": None, "disk": None}
    d["cpu"] = cpu_percent()
    mem = memory_info()
    if mem:
        d["mem"] = 100 * (1 - mem[1] / max(1, mem[0]))
    b = _battery()
    if b:
        d["battery"], d["charging"] = b
    du = shutil.disk_usage(str(Path.home()))
    d["disk"] = 100 * du.used / du.total
    d["disk_free_gb"] = du.free / 2**30
    midnight = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
    r = app.db.q("SELECT COUNT(*), SUM(joules), SUM(local), SUM(completion_tokens) FROM telemetry WHERE ts>=?", (midnight,))[0]
    d.update(calls_today=r[0] or 0, joules_today=r[1] or 0.0, local_today=r[2] or 0, tokens_today=r[3] or 0)
    loc = app.router.local
    d["local_up"] = bool(loc and loc.available())
    try:
        d["model"] = app.router.local_model() if d["local_up"] else ""
    except Exception:
        d["model"] = ""
    d["routing"] = app.cfg.get("engine.routing")
    nxt = app.db.q("SELECT name, next_run FROM tasks WHERE enabled=1 AND next_run IS NOT NULL ORDER BY next_run LIMIT 1")
    d["next_task"] = f"{nxt[0]['name']} · {nxt[0]['next_run'][5:16].replace('T', ' ')}" if nxt else ""
    d["alerts"] = [{"id": a["id"], "ts": a["ts"], "message": a["message"]}
                   for a in app.db.q("SELECT id, ts, message FROM alerts ORDER BY id DESC LIMIT 5")]
    d["watchers"] = len(app.watchers.list())
    if time.time() - _HUD_WEATHER["t"] > 1800 and not _HUD_WEATHER["busy"]:
        _HUD_WEATHER["busy"] = True

        def refresh():
            try:
                _HUD_WEATHER["text"] = get_weather(app.cfg.get("digest.location", "")).splitlines()[0]
            except Exception:
                _HUD_WEATHER["text"] = _HUD_WEATHER["text"] or "weather unavailable"
            _HUD_WEATHER["t"], _HUD_WEATHER["busy"] = time.time(), False
        threading.Thread(target=refresh, daemon=True).start()
    d["weather"] = _HUD_WEATHER["text"]
    return d


def make_handler(app: App, token: str, loopback: bool):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"Jarvis/{VERSION}"
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            if os.environ.get("JARVIS_DEBUG"):
                sys.stderr.write(dim("[http] " + fmt % args) + "\n")

        # -- helpers -------------------------------------------------------
        def _path(self):
            return urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

        def _host_ok(self) -> bool:
            if not loopback:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("127.0.0.1", "localhost", "::1", "")

        def _is_local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _authed(self) -> bool:
            if not token:
                return True
            h = self.headers.get("Authorization", "")
            qtok = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [""])[0]
            return secrets.compare_digest(h, f"Bearer {token}") or (bool(qtok) and secrets.compare_digest(qtok, token))

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 40_000_000:
                raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")

        def _guard(self, write: bool) -> bool:
            if not self._host_ok():
                self._err(403, "bad host header")
                return False
            if not self._authed():
                self._err(401, "unauthorized: add ?token=... to the URL or an Authorization: Bearer header")
                return False
            if write and "application/json" not in (self.headers.get("Content-Type") or ""):
                self._err(415, "Content-Type must be application/json")  # blocks cross-site form posts (CSRF)
                return False
            return True

        # -- routes ---------------------------------------------------------
        def do_OPTIONS(self):
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            p = self._path()
            if p == "/":
                if not self._host_ok():
                    return self._err(403, "bad host header")
                return self._send(200, GUI_HTML.encode(), "text/html; charset=utf-8")
            if p == "/api/health":
                return self._json({"ok": True, "version": VERSION})
            if not self._guard(False):
                return
            try:
                if p == "/api/info":
                    local_up = app.router.local is not None and app.router.local.available()
                    lm = ""
                    if local_up:
                        try:
                            lm = app.router.local_model()
                        except Exception:
                            pass
                    phone_url = None
                    if not loopback and token and self._is_local() and lan_ip():
                        phone_url = f"http://{lan_ip()}:{self.server.server_address[1]}/?token={token}"
                    return self._json({"version": VERSION, "phone_url": phone_url,
                                       "presets": {k: v["desc"] for k, v in PRESETS.items() if k != "digest"},
                                       "default_preset": app.cfg.get("agent.preset"), "routing": app.cfg.get("engine.routing"),
                                       "local_up": local_up, "local_model": lm, "user_name": app.cfg.get("agent.user_name"),
                                       "cloud": (app.router.cloud_model() if app.router.cloud else ""),
                                       "stt": app.cfg.get("voice.stt", "auto"), "local_stt": whisper_available(),
                                       "wake_word": app.cfg.get("voice.wake_word", "jarvis"),
                                       "address": app.cfg.get("agent.address") or "sir"})
                if p == "/api/sessions":
                    return self._json([dict(r) for r in app.sessions()])
                if p.startswith("/api/sessions/"):
                    sid = p.split("/")[-1]
                    s = app.db.q("SELECT * FROM sessions WHERE id=?", (sid,))
                    rows = app.db.q("SELECT role, content, meta, ts FROM messages WHERE session_id=? ORDER BY id", (sid,))
                    msgs = []
                    for r in rows:
                        meta = json.loads(r["meta"] or "{}")
                        fb = None
                        if meta.get("trace_id"):
                            t = app.db.q("SELECT feedback FROM traces WHERE id=?", (meta["trace_id"],))
                            fb = t[0][0] if t else None
                        msgs.append({"role": r["role"], "content": r["content"], "meta": meta, "feedback": fb})
                    return self._json({"id": sid, "preset": s[0]["preset"] if s else None, "messages": msgs})
                if p == "/api/stats":
                    return self._json(app.stats())
                if p == "/api/hud":
                    return self._json(hud_data(app))
                if p == "/api/alerts":
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    after = int((qs.get("after") or ["-1"])[0])
                    last = app.db.q("SELECT COALESCE(MAX(id),0) FROM alerts")[0][0]
                    rows = app.db.q("SELECT id, ts, source, message FROM alerts WHERE id>? ORDER BY id LIMIT 20",
                                    (after,)) if after >= 0 else []
                    return self._json({"last_id": last, "alerts": [dict(r) for r in rows]})
                if p == "/api/protocols":
                    return self._json({k: v.get("description", "") for k, v in all_protocols(app).items()})
                if p == "/api/memory":
                    return self._json([dict(r) for r in app.memory.facts()])
                if p == "/v1/models":
                    names = ["jarvis", "jarvis-local", "jarvis-cloud"]
                    try:
                        names += app.router.local.list_models()
                    except Exception:
                        pass
                    return self._json({"object": "list", "data": [{"id": n, "object": "model", "owned_by": "jarvis"} for n in names]})
                return self._err(404, "not found")
            except Exception as e:
                return self._err(500, str(e))

        def do_DELETE(self):
            if not self._guard(False):
                return
            p = self._path()
            if p.startswith("/api/sessions/"):
                app.delete_session(p.split("/")[-1])
                return self._json({"ok": True})
            if p.startswith("/api/memory/"):
                return self._json({"ok": app.memory.forget(int(p.split("/")[-1]))})
            return self._err(404, "not found")

        def do_POST(self):
            if not self._guard(True):
                return
            p = self._path()
            try:
                body = self._body()
            except Exception as e:
                return self._err(400, f"bad request: {e}")
            try:
                if p == "/api/chat":
                    return self._chat(body)
                if p == "/api/approve":
                    if not self._is_local():
                        return self._err(403, "risky actions can only be approved on the PC itself")
                    ent = app.pending.get(body.get("id", ""))
                    if not ent:
                        return self._err(404, "no such approval")
                    ent[1] = {"allow": True, "always": "always"}.get(body.get("decision"), False)
                    ent[0].set()
                    return self._json({"ok": True})
                if p == "/api/feedback":
                    return self._json({"ok": app.feedback(body.get("trace_id", ""), int(body.get("value", 0)))})
                if p == "/api/memory":
                    return self._json({"id": app.memory.add_fact(str(body.get("text", "")))})
                if p == "/v1/chat/completions":
                    return self._openai(body)
                if p == "/api/transcribe":
                    if not whisper_available():
                        return self._err(501, "Local speech recognition needs: pip install faster-whisper")
                    mime = str(body.get("mime") or "")
                    ext = ".ogg" if "ogg" in mime else ".mp4" if ("mp4" in mime or "aac" in mime) else ".wav" if "wav" in mime else ".webm"
                    fd, path = tempfile.mkstemp(suffix=ext)
                    try:
                        with os.fdopen(fd, "wb") as f:
                            f.write(base64.b64decode(body.get("audio") or ""))
                        return self._json({"text": transcribe_file(path, app.cfg)})
                    finally:
                        os.unlink(path)
                return self._err(404, "not found")
            except Exception as e:
                traceback.print_exc()
                return self._err(500, str(e))

        # -- SSE chat -------------------------------------------------------------
        def _sse_start(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _sse(self, obj) -> bool:
            try:
                self.wfile.write(f"data: {json.dumps(obj, default=str)}\n\n".encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        def _chat(self, body):
            msg = str(body.get("message") or "").strip()
            if not msg:
                return self._err(400, "empty message")
            images = [b for b in (body.get("images") or []) if isinstance(b, str)][:6]
            q: queue.Queue = queue.Queue()

            def approver(name, args):
                if not self._is_local():
                    return False  # a request from another device, such as the phone, never gets a risky action approved
                aid = uuid.uuid4().hex[:10]
                ev = threading.Event()
                app.pending[aid] = [ev, False]
                q.put({"type": "approval", "id": aid, "tool": name, "args": args})
                ev.wait(300)
                return app.pending.pop(aid, [None, False])[1]

            agent = Agent(app, preset=body.get("preset"), session_id=body.get("session_id"), approver=approver,
                          on_event=q.put, routing=body.get("routing") or None)
            self._sse_start()
            self._sse({"type": "session", "session_id": agent.session_id})

            def work():
                try:
                    agent.run(msg, images=images or None, on_token=lambda t: q.put({"type": "token", "text": t}))
                except Exception as e:
                    q.put({"type": "error", "message": str(e)})
                finally:
                    q.put(None)
            threading.Thread(target=work, daemon=True).start()
            alive = True
            while True:
                ev = q.get()
                if ev is None:
                    break
                if alive:
                    alive = self._sse(ev)

        # -- OpenAI-compatible endpoint ---------------------------------------------
        def _openai(self, body):
            def text_of(c):
                if isinstance(c, list):
                    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                return str(c or "")
            msgs = [{"role": m["role"], "content": text_of(m.get("content"))} for m in body.get("messages", [])
                    if m.get("role") in ("system", "user", "assistant")]
            if not msgs:
                return self._err(400, "messages required")
            want = body.get("model") or "jarvis"
            force, model = {"jarvis": (None, None), "jarvis-local": ("local", None),
                            "jarvis-cloud": ("cloud", None)}.get(want, ("local", want))
            cid = "chatcmpl-" + uuid.uuid4().hex[:20]
            created = int(time.time())
            if body.get("stream"):
                self._sse_start()
                filt = TagFilter()

                def on_tok(t):
                    v = filt.feed(t)
                    if v:
                        self._sse({"id": cid, "object": "chat.completion.chunk", "created": created, "model": want,
                                   "choices": [{"index": 0, "delta": {"content": v}, "finish_reason": None}]})
                app.router.chat(msgs, on_token=on_tok, force=force, model=model, session_id="api")
                rest = filt.flush()
                if rest:
                    on_tok(rest)
                self._sse({"id": cid, "object": "chat.completion.chunk", "created": created, "model": want,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass
                return
            res = app.router.chat(msgs, force=force, model=model, session_id="api")
            return self._json({"id": cid, "object": "chat.completion", "created": created, "model": res.model,
                               "choices": [{"index": 0, "message": {"role": "assistant", "content": strip_think(res.content)},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                                         "total_tokens": res.prompt_tokens + res.completion_tokens}})
    return Handler


def access_token(app: App, loopback: bool) -> str:
    """The token clients must present: none for a PC-only server, a random saved one when listening on the network."""
    if loopback:
        return ""
    token = str(app.cfg.get("server.token") or "")  # str(): `config set` turns a value like 12345 into a number
    if not token:
        token = secrets.token_urlsafe(18)
        app.cfg.set("server.token", token)
        app.cfg.save()
    return token


def serve(app: App, host: str | None, port: int | None, open_browser=False, background=True):
    host = host or app.cfg.get("server.host", "127.0.0.1")
    port = int(port or app.cfg.get("server.port", 8765))
    loopback = host in ("127.0.0.1", "localhost", "::1")
    token = access_token(app, loopback)
    httpd = http.server.ThreadingHTTPServer((host, port), make_handler(app, token, loopback))
    httpd.daemon_threads = True
    here = "127.0.0.1" if loopback or host in ("0.0.0.0", "::") else host
    url = f"http://{here}:{port}/" + (f"?token={token}" if token else "")
    stop = threading.Event()
    warm_up(app)
    if background:
        threading.Thread(target=app.scheduler.loop, args=(stop,), daemon=True).start()
    print(bold(cyan("JARVIS")) + f" web app running at {bold(url)}")
    print(dim("  OpenAI-compatible API at /v1/chat/completions · Ctrl+C to stop"))
    if not loopback:
        print("  On your phone (same Wi-Fi): " + bold(f"http://{lan_ip() or host}:{port}/?token={token}"))
        print(yellow("  Anyone with that link can chat with Jarvis, so keep it private. "
                     "Actions that change files or run commands can only be approved on this PC."))
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop.set()
        httpd.server_close()
