"""Tools for listing, running and creating protocols."""

from __future__ import annotations

from ..protocols import all_protocols, match_protocol, run_protocol
from ..registry import S, tool


@tool("list_protocols", "List the named protocols (routines) the user can trigger.")
def t_list_protocols(ag):
    return "\n".join(f"- {k}: {v.get('description', '')}" + (" (built-in)" if v.get("builtin") else "")
                     for k, v in all_protocols(ag.app).items())


@tool("run_protocol", "Run a named protocol (routine), e.g. 'night', 'status', 'morning', 'focus'.",
      {"name": S("Protocol name")}, ["name"])
def t_run_protocol(ag, name: str):
    k = match_protocol(ag.app, name)
    return run_protocol(ag, k) if k else f"No protocol '{name}'. Available: {', '.join(all_protocols(ag.app))}"


@tool("create_protocol", "Create or replace a protocol (a named routine). Steps run in order. Step types: "
      '{"tool": NAME, "args": {...}} | {"say": TEXT} | '
      '{"ask": INSTRUCTION to summarise collected results} | {"remind_in_minutes": N, "message": TEXT}',
      {"name": S("Short name, e.g. 'movie'"), "description": S("What it does"),
       "steps": S("List of step objects", "array", items={"type": "object"})}, ["name", "steps"], risky=True)
def t_create_protocol(ag, name: str, steps: list, description: str = ""):
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return "Error: steps must be a list of objects."
    protos = dict(ag.app.cfg.get("protocols") or {})
    protos[name.lower().strip()] = {"description": description, "steps": steps}
    ag.app.cfg.set("protocols", protos)
    ag.app.cfg.save()
    return f"Protocol '{name}' saved with {len(steps)} steps. Trigger it with: initiate {name} protocol."
