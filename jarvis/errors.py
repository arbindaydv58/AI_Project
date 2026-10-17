"""Exceptions raised by the model engines and the HTTP helpers."""

from __future__ import annotations


class EngineError(Exception):
    pass


class EngineUnavailable(EngineError):
    pass


class ToolsUnsupported(EngineError):
    pass
