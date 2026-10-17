"""Chooses between the local and the cloud engine for each request."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from .energy import EnergyMeter
from .engines import build_engine
from .errors import EngineUnavailable
from .privacy import has_pii, redact_messages

if TYPE_CHECKING:
    from .app import App


MODEL_PREFS = ["qwen3", "qwen2.5", "llama3.1", "llama3.2", "gemma3", "mistral", "phi4", "granite", "llama3"]
HARD_WORDS = re.compile(r"\b(prove|derive|architect|architecture|refactor|optimi[sz]e|comprehensive|in[- ]depth|"
                        r"rigorous|legal|contract|diagnos|step[- ]by[- ]step|research|analy[sz]e|compare)\w*", re.I)


def complexity_score(text: str) -> int:
    s = 0
    if len(text) > 1500:
        s += 2
    elif len(text) > 600:
        s += 1
    s += min(3, len(HARD_WORDS.findall(text)))
    if "```" in text:
        s += 1
    return s


class Router:
    def __init__(self, app: "App"):
        self.app, self.cfg = app, app.cfg
        self.local = build_engine(self.cfg.get("engine.local"), self.cfg, True)
        try:
            self.cloud = build_engine(self.cfg.get("engine.cloud"), self.cfg, False)
        except ValueError:
            self.cloud = None
        self._resolved: dict[str, str] = {}

    def local_model(self) -> str:
        want = self.cfg.get("engine.local.model") or "auto"
        if want != "auto":
            return want
        if "auto" in self._resolved:
            return self._resolved["auto"]
        try:
            names = [n for n in self.local.list_models() if "embed" not in n.lower()]
        except Exception:
            names = []
        if not names:
            raise EngineUnavailable("No local models installed. Run e.g.:  ollama pull qwen3:4b")
        pick = names[0]
        for pref in MODEL_PREFS:
            hit = [n for n in names if n.lower().startswith(pref)]
            if hit:
                pick = hit[0]
                break
        self._resolved["auto"] = pick
        return pick

    def cloud_model(self) -> str:
        return self.cfg.get("engine.cloud.model") or "claude-sonnet-5-5"

    def pick(self, text: str, force: str | None = None, model: str | None = None):
        mode = (force or self.cfg.get("engine.routing") or "local").lower()
        cloud_model_override = model[6:] if model and model.startswith("cloud:") else None
        local_model_override = model if model and not model.startswith("cloud:") else None
        if cloud_model_override:
            mode = "cloud"
        local_up = self.local is not None and self.local.available()
        if mode == "cloud":
            if self.cloud:
                return self.cloud, cloud_model_override or self.cloud_model(), "cloud requested"
            if local_up:
                return self.local, local_model_override or self.local_model(), "no cloud key; stayed local"
            raise EngineUnavailable("Cloud routing requested but no cloud API key is set "
                                    "(set ANTHROPIC_API_KEY or configure engine.cloud).")
        if not local_up:
            if self.cloud and self.cfg.get("engine.cloud_fallback", True):
                return self.cloud, self.cloud_model(), "local engine unavailable -> cloud fallback"
            url = getattr(self.local, "url", "")
            raise EngineUnavailable(f"Local engine is not reachable at {url}. Start Ollama (`ollama serve` or open "
                                    "the Ollama app), or set a cloud API key for fallback.")
        if mode == "auto" and self.cloud:
            score = complexity_score(text)
            if score >= int(self.cfg.get("engine.auto_threshold", 3)):
                if has_pii(text):
                    return self.local, local_model_override or self.local_model(), f"complex (score {score}) but contains PII -> local"
                return self.cloud, self.cloud_model(), f"complex (score {score}) -> cloud"
            return self.local, local_model_override or self.local_model(), f"simple (score {score}) -> local"
        return self.local, local_model_override or self.local_model(), "local-first"

    def chat(self, messages, tools=None, on_token=None, on_think=None, force=None, model=None,
             session_id="", route_text: str | None = None) -> ChatResult:
        if route_text is None:
            route_text = next((m.get("content") or "" for m in reversed(messages) if m["role"] == "user"), "")
        engine, mdl, reason = self.pick(route_text, force, model)
        msgs = messages
        if not engine.is_local and self.cfg.get("security.redact_pii_for_cloud", True):
            msgs = redact_messages(messages)
        meter = EnergyMeter(self.cfg).start() if engine.is_local else None
        try:
            res = engine.chat(msgs, mdl, tools=tools, on_token=on_token, on_think=on_think,
                              temperature=float(self.cfg.get("engine.temperature", 0.4)))
        except EngineUnavailable:
            if meter:
                meter.stop()
            if engine.is_local and self.cloud and self.cfg.get("engine.cloud_fallback", True):
                self.local._avail = (time.time(), False)  # type: ignore[attr-defined]
                return self.chat(messages, tools, on_token, on_think, "cloud", None, session_id, route_text)
            raise
        if meter:
            res.joules, res.energy_method = meter.stop()
        else:
            res.joules, res.energy_method = None, "remote"
        price = (self.cfg.get("pricing") or {}).get(mdl)
        if price and not engine.is_local:
            res.cost = res.prompt_tokens / 1e6 * price[0] + res.completion_tokens / 1e6 * price[1]
        res.route_reason = reason
        try:
            self.app.db.x("INSERT INTO telemetry(ts,session_id,engine,model,local,prompt_tokens,completion_tokens,"
                          "ttft,latency,joules,energy_method,cost,route_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (time.time(), session_id, res.engine, mdl, int(engine.is_local), res.prompt_tokens,
                           res.completion_tokens, res.ttft, res.latency, res.joules, res.energy_method, res.cost, reason))
        except Exception:
            pass
        return res
