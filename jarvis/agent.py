"""The agent loop: prompts, tool calls, approvals and tracing."""

from __future__ import annotations

import datetime as dt
import json
import platform
import re
import threading
import time
import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any, Callable

from . import tools as _builtin_tools  # noqa: F401  (importing it registers every built-in tool)
from .common import TagFilter, loads_lenient, strip_think, truncate
from .engines import _new_call_id
from .errors import EngineError, ToolsUnsupported
from .presets import BASE_RULES, PRESETS, TEXT_TOOL_PROTOCOL
from .protocols import PROTOCOL_RE, all_protocols, match_protocol, run_protocol
from .registry import TOOLS, Tool

if TYPE_CHECKING:
    from .app import App


def parse_text_tool_calls(text: str) -> tuple[list[dict], str]:
    calls = []
    for m in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.S):
        d = loads_lenient(m.group(1))
        if d.get("name"):
            calls.append({"id": _new_call_id(), "name": d["name"], "arguments": loads_lenient(d.get("arguments", {}))})
    if not calls:
        m = re.search(r"```(?:json)?\s*(\{[^`]*\"name\"[^`]*\})\s*```", text, re.S)
        if m:
            d = loads_lenient(m.group(1))
            if d.get("name") in TOOLS:
                calls.append({"id": _new_call_id(), "name": d["name"], "arguments": loads_lenient(d.get("arguments", {}))})
    clean = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S).strip()
    return calls, clean


Approver = Callable[[str, dict], Any]  # returns True / False / "always"


class Agent:
    def __init__(self, app: "App", preset: str | None = None, session_id: str | None = None,
                 approver: Approver | None = None, on_event: Callable[[dict], None] | None = None,
                 routing: str | None = None, model: str | None = None):
        self.app, self.cfg = app, app.cfg
        name = preset or self.cfg.get("agent.preset", "jarvis")
        self.preset_name = name if name in PRESETS else "jarvis"
        self.preset = PRESETS[self.preset_name]
        self.session_id = app.ensure_session(session_id, self.preset_name)
        self.approver = approver or (lambda n, a: False)
        self.on_event = on_event or (lambda ev: None)
        self.routing, self.model = routing, model
        self.always_allow: set[str] = set()
        self.last_stats: dict | None = None
        self.last_trace_id: str | None = None

    # -- context ---------------------------------------------------------------
    def tool_list(self) -> list[Tool]:
        names = self.preset["tools"]
        if "*" in names:
            return list(TOOLS.values())
        return [TOOLS[n] for n in names if n in TOOLS]

    @staticmethod
    def clock() -> str:
        # Sent with the user's message, not in the system prompt: a changing system prompt makes Ollama re-read it.
        return dt.datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")

    def system_prompt(self, user_text: str, text_tools: list[Tool] | None = None) -> str:
        c = self.cfg
        parts = [self.preset["prompt"].replace("{user}", c.get("agent.user_name") or "the user")
                 .replace("{address}", c.get("agent.address") or "sir")]
        if c.get("agent.persona"):
            parts.append(c.get("agent.persona"))
        info = f"OS: {platform.system()}."
        if c.get("agent.user_name"):
            info += f" The user's name is {c.get('agent.user_name')}."
        if self.preset["tools"]:
            info += f" Working folder: {self.app.security.workdir}."
        parts.append(info)
        if self.preset["tools"]:
            parts.append(BASE_RULES)
        try:
            mem = self.app.memory.context_for(user_text)
        except Exception:
            mem = []
        if mem:
            parts.append("What you remember about the user:\n" + "\n".join(f"- {m}" for m in mem))
        if text_tools:
            spec = []
            for t in text_tools:
                props = t.parameters.get("properties", {})
                ps = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in props.items())
                spec.append(f"- {t.name}({ps}): {t.description}")
            parts.append(TEXT_TOOL_PROTOCOL + "\n".join(spec))
        return "\n\n".join(parts)

    def history(self) -> list[dict]:
        rows = self.app.db.q("SELECT role, content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
                             "ORDER BY id DESC LIMIT 80", (self.session_id,))
        budget = int(self.cfg.get("agent.history_chars", 24000))
        out, used = [], 0
        for r in rows:
            used += len(r["content"] or "")
            if used > budget and out:
                break
            out.append({"role": r["role"], "content": r["content"] or ""})
        out.reverse()
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    # -- tool execution ---------------------------------------------------------
    def _needs_approval(self, t: Tool, args: dict) -> bool:
        if callable(t.risky):
            try:
                return bool(t.risky(self, args))
            except Exception:
                return True
        return bool(t.risky)

    def _execute(self, call: dict, allowed: dict[str, Tool], seen: Counter) -> str:
        name, args = call.get("name", ""), call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {"_raw": args}
        self.on_event({"type": "tool_start", "name": name, "args": args})
        out, decision = None, "auto"
        t = allowed.get(name)
        if not t:
            out = f"Error: unknown tool '{name}'. Available tools: {', '.join(allowed) or 'none'}."
        else:
            props = t.parameters.get("properties", {})
            if "_raw" in args and len(t.parameters.get("required", [])) == 1:
                args = {t.parameters["required"][0]: args["_raw"]}
            args = {k: v for k, v in args.items() if k in props}
            missing = [r for r in t.parameters.get("required", []) if r not in args]
            sig = name + json.dumps(args, sort_keys=True, default=str)
            seen[sig] += 1
            if missing:
                out = f"Error: missing required argument(s): {', '.join(missing)}."
            elif seen[sig] > 2:
                out = "Blocked: you already made this exact call twice. Use the earlier result or try something different."
            elif self._needs_approval(t, args) and name not in (self.cfg.get("security.auto_approve") or []) \
                    and name not in self.always_allow:
                d = self.approver(name, args)
                if d == "always":
                    self.always_allow.add(name)
                    d = True
                decision = "approved" if d else "denied"
                if not d:
                    out = "The user denied permission for this action. Do not retry it; continue without it or ask the user."
            if out is None:
                try:
                    out = str(t.fn(self, **args))
                except PermissionError as e:
                    out = f"Error: permission denied: {e}"
                except EngineError as e:
                    out = f"Error: {e}"
                except Exception as e:
                    out = f"Error: {type(e).__name__}: {e}"
        out = truncate(out, 14000)
        try:
            self.app.db.x("INSERT INTO audit(ts,session_id,tool,args,decision,result) VALUES(?,?,?,?,?,?)",
                          (time.time(), self.session_id, name, json.dumps(args, default=str)[:4000], decision, out[:2000]))
        except Exception:
            pass
        self.on_event({"type": "tool_end", "name": name, "ok": not out.startswith(("Error", "Blocked", "The user denied")),
                       "preview": out[:400]})
        return out

    # -- main loop ------------------------------------------------------------------
    def _protocol_turn(self, text: str, name: str, on_token) -> str:
        t0 = time.time()
        self.app.save_message(self.session_id, "user", text, {})
        self.on_event({"type": "tool_start", "name": f"{name} protocol", "args": {}})
        result = run_protocol(self, name)
        self.on_event({"type": "tool_end", "name": f"{name} protocol", "ok": True, "preview": "protocol complete"})
        if on_token:
            on_token(result)
        stats = {"engine": "protocol", "model": f"{name} protocol", "local": True, "latency": time.time() - t0,
                 "completion_tokens": 0, "tps": 0, "joules": None, "energy_method": "", "cost": 0.0,
                 "tools": len(all_protocols(self.app)[name].get("steps", [])), "route": "protocol"}
        self.last_stats = stats
        trace_id = uuid.uuid4().hex[:16]
        self.last_trace_id = trace_id
        self.app.save_message(self.session_id, "assistant", result, {"stats": stats, "trace_id": trace_id,
                                                                     "tools": [f"{name} protocol"]})
        self.app.db.x("INSERT INTO traces(id,ts,session_id,preset,model,system,prompt,response,tools,feedback) "
                      "VALUES(?,?,?,?,?,?,?,?,?,NULL)", (trace_id, time.time(), self.session_id, self.preset_name,
                                                        "protocol", "", text, result, "[]"))
        self.on_event({"type": "done", "text": result, "stats": stats, "trace_id": trace_id})
        return result

    def run(self, text: str, images: list[str] | None = None, on_token: Callable[[str], None] | None = None) -> str:
        pm = PROTOCOL_RE.search(text or "")
        if pm and not images:
            name = match_protocol(self.app, pm.group(1))
            if name:
                return self._protocol_turn(text, name, on_token)
        tools = self.tool_list()
        allowed = {t.name: t for t in tools}
        schemas = [t.schema() for t in tools]
        max_steps = int(self.preset.get("max_steps") or self.cfg.get("agent.max_steps", 8))
        hist = self.history()
        key_model = self.model or self.cfg.get("engine.local.model")
        text_mode = bool(tools) and key_model in self.app.text_tool_models
        system = self.system_prompt(text, tools if text_mode else None)
        messages = [{"role": "system", "content": system}] + hist + \
                   [{"role": "user", "content": f"[Current date and time: {self.clock()}]\n{text}",
                     **({"images": images} if images else {})}]
        self.app.save_message(self.session_id, "user", text, {"images": len(images or [])})

        stats = {"engine": "", "model": "", "local": True, "prompt_tokens": 0, "completion_tokens": 0,
                 "latency": 0.0, "gen_time": 0.0, "ttft": None, "joules": None, "methods": set(), "cost": 0.0,
                 "tools": 0, "route": ""}
        t0 = time.time()
        seen: Counter = Counter()
        tool_log: list[dict] = []
        final = ""
        step = 0
        retried_text_mode = False
        while step <= max_steps:
            last = step == max_steps
            if step > 0:
                self.on_event({"type": "step", "n": step})
            if last and tools:
                messages.append({"role": "user", "content": "You have used all your tool steps. Do not call any more "
                                                            "tools; give your best final answer now."})
            filt = TagFilter(("think", "tool_call") if text_mode else ("think",))
            show_think = self.cfg.get("agent.show_thinking", False)

            def tok(s, filt=filt):
                v = filt.feed(s)
                if v and on_token:
                    on_token(v)

            def think(s):
                if show_think:
                    self.on_event({"type": "thinking", "text": s})
            try:
                res = self.app.router.chat(messages, tools=schemas if (tools and not text_mode) else None,
                                           on_token=tok, on_think=think, force=self.routing, model=self.model,
                                           session_id=self.session_id, route_text=text)
            except ToolsUnsupported:
                if retried_text_mode:
                    raise
                retried_text_mode = text_mode = True
                self.app.text_tool_models.add(key_model)
                messages[0]["content"] = self.system_prompt(text, tools)
                continue
            rest = filt.flush()
            if rest and on_token:
                on_token(rest)
            stats.update(engine=res.engine, model=res.model, local=res.local, route=res.route_reason)
            stats["prompt_tokens"] += res.prompt_tokens
            stats["completion_tokens"] += res.completion_tokens
            stats["gen_time"] += max(res.latency - res.ttft, 1e-3)
            if stats["ttft"] is None:
                stats["ttft"] = res.ttft
            if res.joules is not None:
                stats["joules"] = (stats["joules"] or 0) + res.joules
            stats["methods"].add(res.energy_method)
            stats["cost"] += res.cost

            content = strip_think(res.content)
            calls = res.tool_calls
            if text_mode and not calls:
                calls, content = parse_text_tool_calls(res.content)
                content = strip_think(content)
            if not calls or not tools or last:
                final = content
                break
            if text_mode:
                messages.append({"role": "assistant", "content": res.content})
            else:
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                out = self._execute(call, allowed, seen)
                stats["tools"] += 1
                tool_log.append({"name": call["name"], "args": call.get("arguments"), "result": out[:500]})
                if text_mode:
                    messages.append({"role": "user", "content": f'<tool_result name="{call["name"]}">\n{out}\n</tool_result>'})
                else:
                    messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": out})
            step += 1

        if not final:
            final = "(I couldn't produce an answer. Try rephrasing, or switch model/preset.)"
        stats["latency"] = time.time() - t0
        stats["tps"] = stats["completion_tokens"] / stats["gen_time"] if stats["gen_time"] else 0
        methods = {m for m in stats.pop("methods") if m and m != "remote"}
        stats["energy_method"] = "+".join(sorted(methods)) if methods else ("remote" if not stats["local"] else "")
        self.last_stats = stats
        trace_id = uuid.uuid4().hex[:16]
        self.last_trace_id = trace_id
        self.app.save_message(self.session_id, "assistant", final, {"stats": stats, "trace_id": trace_id,
                                                                    "tools": [t["name"] for t in tool_log]})
        try:
            self.app.db.x("INSERT INTO traces(id,ts,session_id,preset,model,system,prompt,response,tools,feedback) "
                          "VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                          (trace_id, time.time(), self.session_id, self.preset_name, stats["model"], system, text, final,
                           json.dumps(tool_log, default=str)[:20000]))
        except Exception:
            pass
        self.on_event({"type": "done", "text": final, "stats": stats, "trace_id": trace_id})
        return final


def fmt_stats(s: dict | None) -> str:
    if not s:
        return ""
    parts = ["local" if s.get("local") else "cloud", s.get("model", "?"), f"{s.get('latency', 0):.1f}s"]
    if s.get("completion_tokens"):
        parts.append(f"{s['completion_tokens']} tok")
    if s.get("tps"):
        parts.append(f"{s['tps']:.0f} tok/s")
    if s.get("tools"):
        parts.append(f"{s['tools']} tool call{'s' if s['tools'] != 1 else ''}")
    if s.get("joules") is not None:
        parts.append(f"{s['joules']:.1f} J ({s.get('energy_method')})")
    if s.get("cost"):
        parts.append(f"${s['cost']:.4f}")
    return " · ".join(parts)


def warm_up(app: App, preset: str | None = None):
    """Load the local model and cache the default prompt in the background, so the first real question waits for neither."""
    def work():
        try:
            if not (app.router.local and app.router.local.available()):
                return
            ag = Agent(app, preset=preset, session_id="warmup")
            msgs = [{"role": "system", "content": ag.system_prompt("")},
                    {"role": "user", "content": f"[Current date and time: {ag.clock()}]\nReply with one word: ready."}]
            app.router.chat(msgs, tools=[t.schema() for t in ag.tool_list()] or None, session_id="warmup")
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
