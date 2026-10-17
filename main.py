#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JARVIS - a small AI assistant that runs on your own computer.

It talks to a local model through Ollama and uses only the Python standard library.
The code is in the jarvis/ package; this file is the command-line entry point.

WHAT IT DOES
  Chat          in the terminal or in a web page (the page also has voice input and output)
  Tools         calculator, clock, web search, page fetch, memory, document search; the larger
                presets add files, shell and Python (these ask before they act)
  Memory        facts about you, plus an index of your documents (keyword and meaning search)
  Routines      named protocols ("initiate night protocol"), scheduled tasks, a morning brief
  Alerts        battery, disk, memory, websites that go down, news keywords
  Presets       lite (default, fast) | chat | jarvis (every tool, slow) | code | research
  Cloud         optional; set ANTHROPIC_API_KEY or OPENAI_API_KEY and use --auto or --cloud
  Safety        file access limited to chosen folders, secret files blocked, dangerous commands
                blocked, approval before risky actions, audit log of every tool call

QUICK START
  1. Install Python 3.9+ and Ollama (https://ollama.com), then:
       ollama pull qwen3:4b-instruct
       ollama pull nomic-embed-text      # optional: search by meaning
  2. python main.py doctor               # checks your setup
  3. python main.py gui                  # web app at http://127.0.0.1:8765
     python main.py                      # chat in the terminal

COMMANDS (python main.py <command> --help for details)
  chat | ask | gui | serve | doctor | init | models | config | sessions
  memory | ingest | docs | schedule | daemon | digest | protocol | watch
  alerts | stats | bench | learn

Data lives in ~/.jarvis (override with the JARVIS_HOME environment variable).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sys
import threading
from pathlib import Path

from jarvis.agent import Agent, fmt_stats
from jarvis.app import App
from jarvis.bench import run_bench
from jarvis.cli import cli_approver, cli_events, print_stats, repl
from jarvis.common import HOME, VERSION, bold, cyan, dim, green, magenta, red, yellow
from jarvis.digest import run_digest
from jarvis.doctor import doctor, init_wizard
from jarvis.errors import EngineError
from jarvis.learn import learn_export, learn_modelfile
from jarvis.presets import PRESETS
from jarvis.protocols import all_protocols, match_protocol, run_protocol
from jarvis.server import serve
from jarvis.speech import speak


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis", description="JARVIS - local-first personal AI assistant",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--version", action="version", version=f"jarvis {VERSION}")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("-p", "--preset", choices=[k for k in PRESETS if k != "digest"])
        p.add_argument("-m", "--model", help="model name (prefix cloud: for a cloud model)")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--local", dest="routing", action="store_const", const="local")
        g.add_argument("--cloud", dest="routing", action="store_const", const="cloud")
        g.add_argument("--auto", dest="routing", action="store_const", const="auto")

    p = sub.add_parser("chat", help="interactive terminal chat (default)")
    common(p)
    p.add_argument("-s", "--session", help="resume a session id")
    p = sub.add_parser("ask", help="one-shot question  (pipe input with: cat f.txt | jarvis ask \"summarise\" -)")
    common(p)
    p.add_argument("text", nargs="*")
    p.add_argument("-i", "--image", action="append", default=[], help="attach image (repeatable)")
    p.add_argument("--speak", action="store_true")
    p.add_argument("--json", action="store_true", help="print JSON with answer and stats")
    p.add_argument("-y", "--yes", action="store_true", help="auto-approve risky tools")
    for name, h in (("gui", "start the web app and open your browser"), ("serve", "run web app + API + scheduler")):
        p = sub.add_parser(name, help=h)
        p.add_argument("--host")
        p.add_argument("--port", type=int)
    sub.add_parser("doctor", help="check your setup")
    sub.add_parser("init", help="interactive setup")
    sub.add_parser("models", help="list available models")
    p = sub.add_parser("config", help="show or change settings")
    p.add_argument("action", choices=["show", "set", "get", "path"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p = sub.add_parser("sessions", help="list / show / delete conversations")
    p.add_argument("action", nargs="?", default="list", choices=["list", "show", "rm"])
    p.add_argument("id", nargs="?")
    p = sub.add_parser("memory", help="manage long-term memory")
    p.add_argument("action", nargs="?", default="list", choices=["list", "add", "forget", "search"])
    p.add_argument("text", nargs="*")
    p = sub.add_parser("ingest", help="index files/folders for document search")
    p.add_argument("paths", nargs="+")
    p = sub.add_parser("docs", help="search indexed documents")
    p.add_argument("query", nargs="+")
    p = sub.add_parser("schedule", help="scheduled background tasks")
    p.add_argument("action", nargs="?", default="list", choices=["list", "add", "rm", "run"])
    p.add_argument("args", nargs="*", help="add: NAME SCHEDULE PROMPT | rm/run: ID")
    p.add_argument("-p", "--preset", default="lite", choices=list(PRESETS))
    sub.add_parser("daemon", help="run scheduled tasks")
    p = sub.add_parser("digest", help="morning brief: weather, calendar, email, news")
    p.add_argument("--speak", action="store_true")
    p = sub.add_parser("protocol", help="named routines: list | run NAME | add NAME 'JSON steps' | rm NAME",
                       description="Step types: {\"tool\": NAME, \"args\": {...}}, "
                                   "{\"say\": TEXT}, {\"ask\": INSTRUCTION}, {\"remind_in_minutes\": N, \"message\": TEXT}, "
                                   "{\"digest\": true}. Example: jarvis protocol add movie "
                                   "'[{\"tool\":\"get_time\"},{\"say\":\"Enjoy the film, {address}.\"}]'")
    p.add_argument("action", nargs="?", default="list", choices=["list", "run", "add", "rm"])
    p.add_argument("name", nargs="?")
    p.add_argument("steps", nargs="?", help="JSON list of steps (for add)")
    p.add_argument("-d", "--description", default="")
    p = sub.add_parser("watch", help="proactive watchers: list | add 'JSON' | rm N | check",
                       description='Examples:  jarvis watch add \'{"type":"url","url":"https://mysite.com","every":"5m"}\'   '
                                   'jarvis watch add \'{"type":"rss","keywords":["Nepal","OpenAI"],"every":"30m"}\'')
    p.add_argument("action", nargs="?", default="list", choices=["list", "add", "rm", "check"])
    p.add_argument("arg", nargs="?")
    sub.add_parser("alerts", help="show recent proactive alerts")
    p = sub.add_parser("stats", help="usage, latency and energy")
    p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("bench", help="benchmark accuracy / latency / energy")
    p.add_argument("--models", help="comma-separated models to compare")
    p.add_argument("--cloud", dest="routing", action="store_const", const="cloud")
    p = sub.add_parser("learn", help="learn from your rated conversations")
    p.add_argument("action", choices=["stats", "export", "modelfile"])
    p.add_argument("--out", default=str(HOME / "learn" / "sft.jsonl"))
    p.add_argument("--base", help="base model for modelfile")
    p.add_argument("--name", default="jarvis-personal")
    p.add_argument("--create", action="store_true", help="run `ollama create` too")

    a = ap.parse_args(argv)
    app = App(a.config)
    cmd = a.cmd or "chat"

    if cmd == "chat":
        return repl(app, getattr(a, "preset", None), getattr(a, "session", None), getattr(a, "routing", None),
                    getattr(a, "model", None))
    if cmd == "ask":
        words = [w for w in a.text if w != "-"]
        text = " ".join(words).strip()
        if ("-" in a.text or not text) and not sys.stdin.isatty():
            piped = sys.stdin.read()
            if piped.strip():
                text = f"{text}\n\n<<<INPUT>>>\n{piped}\n<<<END INPUT>>>" if text else piped
        if not text:
            ap.error("ask needs a question")
        imgs = [base64.b64encode(Path(i).expanduser().read_bytes()).decode() for i in a.image]
        approver = (lambda n, x: True) if a.yes else (cli_approver if sys.stdin.isatty() else (lambda n, x: False))
        agent = Agent(app, preset=a.preset, approver=approver, on_event=(lambda e: None) if a.json else cli_events,
                      routing=a.routing, model=a.model)
        try:
            out = agent.run(text, images=imgs or None, on_token=None if a.json else (lambda t: print(t, end="", flush=True)))
        except EngineError as e:
            print(red(str(e)), file=sys.stderr)
            return 1
        if a.json:
            print(json.dumps({"answer": out, "stats": agent.last_stats, "session_id": agent.session_id}, default=str))
        else:
            print()
            print(dim(fmt_stats(agent.last_stats)), file=sys.stderr)
        if a.speak:
            speak(out)
        return 0
    if cmd in ("gui", "serve"):
        return serve(app, a.host, a.port, open_browser=(cmd == "gui"))
    if cmd == "doctor":
        return doctor(app)
    if cmd == "init":
        return init_wizard(app)
    if cmd == "models":
        loc = app.router.local
        if loc and loc.available():
            try:
                cur = app.router.local_model()
            except Exception:
                cur = ""
            print(bold("Local models:"))
            for m in loc.list_models():
                print(f"  {'*' if m == cur else ' '} {m}")
        else:
            print(yellow("Local engine offline."))
        if app.router.cloud:
            print(bold("Cloud:") + f" {app.router.cloud.name} / {app.router.cloud_model()}")
        return 0
    if cmd == "config":
        if a.action == "path":
            print(app.cfg.path)
        elif a.action == "show":
            print(json.dumps(app.cfg.data, indent=2))
        elif a.action == "get":
            print(json.dumps(app.cfg.get(a.key or ""), indent=2))
        elif a.action == "set":
            if not a.key or a.value is None:
                ap.error("config set KEY VALUE")
            app.cfg.set(a.key, a.value)
            app.cfg.save()
            print(f"{a.key} = {json.dumps(app.cfg.get(a.key))}")
        return 0
    if cmd == "sessions":
        if a.action == "list":
            for r in app.sessions(50):
                print(f"{cyan(r['id'])}  {dt.datetime.fromtimestamp(r['updated']):%Y-%m-%d %H:%M}  "
                      f"{r['n']:>3} msgs  {r['title']}")
        elif a.action == "show" and a.id:
            for r in app.db.q("SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (a.id,)):
                print((cyan("you ›") if r["role"] == "user" else magenta("jarvis ›")) + " " + r["content"] + "\n")
        elif a.action == "rm" and a.id:
            app.delete_session(a.id)
            print("Deleted.")
        return 0
    if cmd == "memory":
        t = " ".join(a.text)
        if a.action == "list":
            for r in app.memory.facts():
                print(f"[{r['id']}] {r['text']}")
        elif a.action == "add" and t:
            print(f"Saved (id {app.memory.add_fact(t)}).")
        elif a.action == "forget" and t.isdigit():
            print("Forgotten." if app.memory.forget(int(t)) else "Not found.")
        elif a.action == "search" and t:
            for r, s in app.memory.search_facts(t, 10):
                print(f"[{r['id']}] {r['text']}  {dim(f'{s:.3f}')}")
        return 0
    if cmd == "ingest":
        for pth in a.paths:
            st = app.memory.ingest(pth, on_file=lambda f, n: print(dim(f"  + {f} ({n} chunks)")))
            print(green(f"Indexed {st['files']} files ({st['chunks']} chunks); {st['unchanged']} unchanged, "
                        f"{st['skipped']} skipped." + ("" if not st["files"] else
                        f" Semantic embeddings: {'yes' if st['embedded'] else 'no (keyword search only)'}")))
        return 0
    if cmd == "docs":
        for i, (r, s) in enumerate(app.memory.search_docs(" ".join(a.query), 8), 1):
            print(bold(f"{i}. {r['path']}") + dim(f"  (part {r['idx'] + 1})"))
            print("   " + r["text"][:400].replace("\n", "\n   ") + "\n")
        return 0
    if cmd == "schedule":
        if a.action == "list":
            rows = app.scheduler.list()
            for r in rows:
                print(f"#{r['id']:<3} {r['name']:<24} [{r['schedule']}] preset={r['preset']} next={r['next_run']} "
                      f"{'' if r['enabled'] else red('(disabled)')}")
                if r["last_result"]:
                    print(dim(f"      last: {r['last_run']} - {r['last_result'][:100].replace(chr(10), ' ')}"))
            if not rows:
                print("No tasks. Example: jarvis schedule add \"Morning brief\" \"weekdays 07:30\" \"brief\" -p digest")
        elif a.action == "add":
            if len(a.args) < 3:
                ap.error('schedule add NAME "SCHEDULE" "PROMPT"')
            try:
                tid = app.scheduler.add(a.args[0], a.args[1], " ".join(a.args[2:]), a.preset)
            except ValueError as e:
                print(red(str(e)))
                return 1
            print(green(f"Added task #{tid}. Keep `jarvis daemon` or `jarvis serve` running for it to fire."))
        elif a.action == "rm" and a.args:
            app.scheduler.remove(int(a.args[0]))
            print("Removed.")
        elif a.action == "run" and a.args:
            row = app.db.q("SELECT * FROM tasks WHERE id=?", (int(a.args[0]),))
            if row:
                print(app.scheduler.run_task(row[0], notify_user=False))
        return 0
    if cmd == "daemon":
        stop = threading.Event()
        try:
            app.scheduler.loop(stop)
        except KeyboardInterrupt:
            stop.set()
        return 0
    if cmd == "digest":
        print(dim("Gathering weather, calendar, email and news...\n"))
        text = run_digest(app, on_token=lambda t: print(t, end="", flush=True))
        print()
        if a.speak or app.cfg.get("voice.speak"):
            speak(text)
        return 0
    if cmd == "protocol":
        protos = all_protocols(app)
        if a.action == "list":
            for k, v in protos.items():
                print(f"  {cyan(k):<20} {v.get('description', '')} {dim('(built-in)' if v.get('builtin') else '')}")
            print(dim('\n  Say or type: "initiate <name> protocol"'))
        elif a.action == "run" and a.name:
            k = match_protocol(app, a.name)
            if not k:
                print(red(f"No protocol '{a.name}'."))
                return 1
            ag = Agent(app, approver=cli_approver if sys.stdin.isatty() else (lambda n, x: False), on_event=cli_events)
            print(run_protocol(ag, k))
        elif a.action == "add" and a.name and a.steps:
            try:
                steps = json.loads(a.steps)
                assert isinstance(steps, list) and all(isinstance(s, dict) for s in steps)
            except Exception:
                print(red("Steps must be a JSON list of objects. See: jarvis protocol --help"))
                return 1
            user = dict(app.cfg.get("protocols") or {})
            user[a.name.lower()] = {"description": a.description, "steps": steps}
            app.cfg.set("protocols", user)
            app.cfg.save()
            print(green(f"Saved. Trigger with: initiate {a.name} protocol"))
        elif a.action == "rm" and a.name:
            user = dict(app.cfg.get("protocols") or {})
            print("Removed." if user.pop(a.name.lower(), None) else "Not a user protocol (built-ins can be overridden, not removed).")
            app.cfg.set("protocols", user)
            app.cfg.save()
        return 0
    if cmd == "watch":
        ws = app.watchers.list()
        if a.action == "list":
            for i, w in enumerate(ws, 1):
                print(f"  {i}. {app.watchers.describe(w)}")
            if not ws:
                print("  No watchers.")
        elif a.action == "add" and a.arg:
            try:
                w = json.loads(a.arg)
                assert isinstance(w, dict) and w.get("type")
            except Exception:
                print(red("Give a JSON object with a 'type'. See: jarvis watch --help"))
                return 1
            app.cfg.set("watchers", ws + [w])
            app.cfg.save()
            print(green(f"Watching: {app.watchers.describe(w)}"))
        elif a.action == "rm" and a.arg and a.arg.isdigit():
            i = int(a.arg) - 1
            if 0 <= i < len(ws):
                gone = ws.pop(i)
                app.cfg.set("watchers", ws)
                app.cfg.save()
                print(f"Removed: {app.watchers.describe(gone)}")
        elif a.action == "check":
            for w in ws:
                try:
                    alert, msg = app.watchers.check(w)
                    print(f"  {red('ALERT') if alert else green('ok   ')}  {app.watchers.describe(w)}" + (f"\n         {msg}" if alert else ""))
                except Exception as e:
                    print(f"  {yellow('error')}  {app.watchers.describe(w)}: {e}")
        return 0
    if cmd == "alerts":
        for r in app.db.q("SELECT ts, source, message FROM alerts ORDER BY id DESC LIMIT 30"):
            print(f"  {dt.datetime.fromtimestamp(r['ts']):%m-%d %H:%M}  {dim(r['source']):<8} {r['message']}")
        return 0
    if cmd == "stats":
        print_stats(app, a.days)
        return 0
    if cmd == "bench":
        models = [m.strip() for m in a.models.split(",")] if a.models else None
        run_bench(app, models, a.routing)
        return 0
    if cmd == "learn":
        if a.action == "stats":
            r = app.db.q("SELECT COUNT(*), SUM(feedback>0), SUM(feedback<0) FROM traces")[0]
            print(f"{r[0]} conversation turns recorded · 👍 {r[1] or 0} · 👎 {r[2] or 0}")
            print(dim("Rate answers with /good and /bad in the terminal, or 👍/👎 in the web app."))
        elif a.action == "export":
            n, m = learn_export(app, Path(a.out))
            print(green(f"Wrote {n} positive examples to {a.out} and {m} rejected examples alongside it."))
            print(dim("Use them to fine-tune (e.g. with Unsloth, Axolotl or MLX-LM), then import the model into Ollama."))
        elif a.action == "modelfile":
            base = a.base or app.router.local_model()
            learn_modelfile(app, base, a.name, a.create)
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
