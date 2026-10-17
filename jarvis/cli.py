"""Terminal chat interface."""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .agent import Agent, fmt_stats, warm_up
from .common import VERSION, bold, cyan, dim, green, magenta, red, short_args, yellow
from .errors import EngineError
from .presets import PRESETS
from .protocols import all_protocols
from .speech import speak

if TYPE_CHECKING:
    from .app import App


try:  # optional: nicer line editing / history in the terminal
    import readline  # noqa: F401
except Exception:
    pass


def cli_approver(name: str, args: dict):
    print()
    print(yellow(f"  ⚠  Jarvis wants to use {bold(name)}"))
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v)
        lines = str(s).splitlines() or [""]
        print(f"     {cyan(k)}: {lines[0][:200]}")
        for ln in lines[1:15]:
            print(f"       {ln[:200]}")
        if len(lines) > 15:
            print(dim(f"       ... ({len(lines) - 15} more lines)"))
    try:
        a = input(yellow("     Allow? [y]es / [n]o / [a]lways this session: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return "always" if a in ("a", "always") else a in ("y", "yes")


def cli_events(ev: dict):
    t = ev["type"]
    if t == "tool_start":
        print("\n" + dim(f"  ⚙ {ev['name']}({short_args(ev['args'])})"), flush=True)
    elif t == "tool_end":
        mark = green("✓") if ev["ok"] else red("✗")
        prev = ev["preview"].replace("\n", " ")[:100]
        print(dim("    ") + mark + dim(f" {prev}"), flush=True)
    elif t == "step":
        print(magenta("jarvis ›") + " ", end="", flush=True)
    elif t == "thinking":
        print(dim(ev["text"]), end="", flush=True)


REPL_HELP = """Commands:
  /new                 start a new conversation        /sessions          list conversations
  /resume <id>         continue a conversation         /preset <name>     lite|jarvis|chat|code|research
  /model <name>        use a specific model (cloud:<name> for cloud)
  /route <mode>        local | auto | cloud            /tools             list available tools
  /remember <fact>     save a fact                     /memory            show remembered facts
  /forget <id>         delete a fact                   /good  /bad        rate the last answer
  /image <path>        attach an image to next message /speak on|off      read answers aloud
  /stats               usage & energy                  /clear             clear the screen
  /alerts              recent proactive alerts         /protocols         list protocols
  /exit                quit
Protocols: type "initiate <name> protocol" (e.g. status, morning, night, focus).
Multi-line input: type \"\"\" on its own line, paste, then \"\"\" again."""


def repl(app: App, preset=None, session=None, routing=None, model=None):
    agent = Agent(app, preset=preset, session_id=session, approver=cli_approver, on_event=cli_events,
                  routing=routing, model=model)
    speak_on = bool(app.cfg.get("voice.speak"))
    images: list[str] = []
    warm_up(app, preset)
    try:
        where = app.router.local_model() if app.router.local and app.router.local.available() else "offline"
    except Exception:
        where = "no model"
    print(bold(cyan("\n  J A R V I S")) + dim(f"  v{VERSION} · preset {agent.preset_name} · local model: {where} · "
                                              f"routing: {routing or app.cfg.get('engine.routing')}"))
    print(dim("  Type /help for commands, /exit to quit.\n"))
    if session:
        for r in app.db.q("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 6", (session,))[::-1]:
            print((cyan("you ›") if r["role"] == "user" else magenta("jarvis ›")) + " " + r["content"][:400])
        print()
    while True:
        try:
            line = input(cyan("you › "))
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() == '"""':
            buf = []
            while True:
                try:
                    ln = input()
                except EOFError:
                    break
                if ln.strip() == '"""':
                    break
                buf.append(ln)
            line = "\n".join(buf)
        text = line.strip()
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, arg = text[1:].partition(" ")
            arg = arg.strip()
            if cmd in ("exit", "quit", "q"):
                break
            elif cmd == "help":
                print(REPL_HELP)
            elif cmd == "new":
                agent = Agent(app, preset=agent.preset_name, approver=cli_approver, on_event=cli_events,
                              routing=agent.routing, model=agent.model)
                print(dim("  New conversation."))
            elif cmd == "sessions":
                for r in app.sessions(20):
                    print(f"  {cyan(r['id'])}  {dt.datetime.fromtimestamp(r['updated']):%m-%d %H:%M}  {r['title']}")
            elif cmd == "resume" and arg:
                agent = Agent(app, preset=agent.preset_name, session_id=arg, approver=cli_approver,
                              on_event=cli_events, routing=agent.routing, model=agent.model)
                print(dim(f"  Resumed {arg}."))
            elif cmd == "preset":
                if arg in PRESETS and arg != "digest":
                    agent.preset_name, agent.preset = arg, PRESETS[arg]
                    print(dim(f"  Preset: {arg} - {PRESETS[arg]['desc']}"))
                else:
                    print("  Presets: " + ", ".join(f"{k} ({v['desc']})" for k, v in PRESETS.items() if k != "digest"))
            elif cmd == "model":
                agent.model = arg or None
                print(dim(f"  Model: {arg or 'default'}"))
            elif cmd == "route":
                if arg in ("local", "auto", "cloud"):
                    agent.routing = arg
                    print(dim(f"  Routing: {arg}"))
                else:
                    print("  Usage: /route local|auto|cloud")
            elif cmd == "tools":
                for t in agent.tool_list():
                    print(f"  {cyan(t.name):<28} {'⚠ ' if t.risky is True else '~ ' if t.risky else '  '}{t.description[:90]}")
            elif cmd == "remember" and arg:
                print(dim(f"  Saved (id {app.memory.add_fact(arg)})."))
            elif cmd == "memory":
                for r in app.memory.facts():
                    print(f"  [{r['id']}] {r['text']}")
            elif cmd == "forget" and arg.isdigit():
                print(dim("  Forgotten." if app.memory.forget(int(arg)) else "  Not found."))
            elif cmd in ("good", "bad"):
                if agent.last_trace_id:
                    app.feedback(agent.last_trace_id, 1 if cmd == "good" else -1)
                    print(dim("  Thanks - recorded for learning."))
            elif cmd == "image" and arg:
                p = Path(arg).expanduser()
                if p.is_file():
                    images.append(base64.b64encode(p.read_bytes()).decode())
                    print(dim(f"  Attached {p.name} to your next message."))
                else:
                    print(red("  File not found."))
            elif cmd == "speak":
                speak_on = arg != "off"
                print(dim(f"  Speech {'on' if speak_on else 'off'}."))
            elif cmd == "stats":
                print_stats(app, 30)
            elif cmd == "alerts":
                for r in app.db.q("SELECT ts, message FROM alerts ORDER BY id DESC LIMIT 10"):
                    print(f"  {dt.datetime.fromtimestamp(r['ts']):%m-%d %H:%M}  {r['message']}")
            elif cmd == "protocols":
                for k, v in all_protocols(app).items():
                    print(f"  {cyan(k):<20} {v.get('description', '')}")
            elif cmd == "clear":
                print("\033[2J\033[H", end="")
            else:
                print("  Unknown command. /help for help.")
            continue
        print(magenta("jarvis ›") + " ", end="", flush=True)
        try:
            out = agent.run(text, images=images or None, on_token=lambda t: print(t, end="", flush=True))
            images = []
            print()
            print(dim("  " + fmt_stats(agent.last_stats)))
            if speak_on:
                speak(out)
        except KeyboardInterrupt:
            print(red("\n  [interrupted]"))
        except EngineError as e:
            print(red(f"\n  {e}"))
        print()


def print_stats(app: App, days: int):
    s = app.stats(days)
    print(bold(f"Usage - last {days} days"))
    print(f"  model calls: {s['calls']}   ran locally: {s['local_share']:.0%}   local energy: {s['joules']:.0f} J   "
          f"cloud cost: ${s['cost']:.4f}")
    print(f"  rated answers: 👍 {s['thumbs_up']}  👎 {s['thumbs_down']}   facts: {s['facts']}   "
          f"documents: {s['doc_files']} files / {s['doc_chunks']} chunks")
    if s["models"]:
        print(f"\n  {'model':<30}{'calls':>6}{'avg lat':>9}{'ttft':>7}{'tok/s':>7}{'joules':>9}  method")
        for m in s["models"]:
            j = f"{m['joules']:.0f}" if m["joules"] is not None else "-"
            print(f"  {(('🖥 ' if m['local'] else '☁ ') + m['model'])[:29]:<30}{m['calls']:>6}{m['avg_latency']:>8.2f}s"
                  f"{m['avg_ttft']:>6.2f}s{m['tok_per_s']:>7.0f}{j:>9}  {m['energy_method']}")
