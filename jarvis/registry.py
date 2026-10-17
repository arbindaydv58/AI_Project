"""Tool registry: the Tool class, the TOOLS table and the @tool decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable
    risky: Any = False  # bool, or callable(agent, args) -> bool for argument-dependent approval

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


TOOLS: dict[str, Tool] = {}


def S(desc: str, typ: str = "string", **extra) -> dict:
    d = {"type": typ, "description": desc}
    d.update(extra)
    return d


def tool(name: str, description: str, params: dict | None = None, required: list | None = None, risky=False):
    def deco(fn):
        TOOLS[name] = Tool(name, description, {"type": "object", "properties": params or {},
                                               "required": required or []}, fn, risky)
        return fn
    return deco
