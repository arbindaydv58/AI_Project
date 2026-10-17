"""Model engines: Ollama, OpenAI-compatible servers and Anthropic."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .common import loads_lenient
from .errors import EngineError, ToolsUnsupported
from .http_utils import http_json, http_open

if TYPE_CHECKING:
    from .config import Config


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list = field(default_factory=list)  # [{"id","name","arguments"}]
    thinking: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft: float = 0.0
    latency: float = 0.0
    engine: str = ""
    model: str = ""
    local: bool = True
    joules: float | None = None
    energy_method: str = ""
    cost: float = 0.0
    route_reason: str = ""


def _new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


def _image_mime(b64: str) -> str:
    head = b64[:12]
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("/9j/"):
        return "image/jpeg"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/png"


class Engine:
    name = "base"
    is_local = True

    def available(self) -> bool:
        return False

    def list_models(self) -> list[str]:
        return []

    def chat(self, messages, model, tools=None, on_token=None, on_think=None, temperature=0.4) -> ChatResult:
        raise NotImplementedError

    def embed(self, texts: list[str], model: str):
        return None


class OllamaEngine(Engine):
    name = "ollama"

    def __init__(self, url: str, num_ctx: int = 16384, timeout: float = 300, think=None, is_local=True,
                 num_thread: int = 0, keep_alive: str = ""):
        self.url = (url or "http://127.0.0.1:11434").rstrip("/")
        self.num_ctx, self.timeout, self.think, self.is_local = num_ctx, timeout, think, is_local
        self.num_thread, self.keep_alive = num_thread, keep_alive
        self._avail = (0.0, False)

    def available(self) -> bool:
        if time.time() - self._avail[0] < (10 if self._avail[1] else 3):
            return self._avail[1]
        try:
            http_json(self.url + "/api/version", timeout=2.5)
            ok = True
        except Exception:
            ok = False
        self._avail = (time.time(), ok)
        return ok

    def list_models(self) -> list[str]:
        d = http_json(self.url + "/api/tags", timeout=5) or {}
        return [m.get("name", "") for m in d.get("models", [])]

    @staticmethod
    def _convert(messages):
        out = []
        for m in messages:
            o = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("images"):
                o["images"] = m["images"]
            if m.get("tool_calls"):
                o["tool_calls"] = [{"function": {"name": t["name"], "arguments": t.get("arguments") or {}}}
                                   for t in m["tool_calls"]]
            if m["role"] == "tool":
                o["tool_name"] = m.get("name", "")
            out.append(o)
        return out

    def chat(self, messages, model, tools=None, on_token=None, on_think=None, temperature=0.4):
        payload: dict = {"model": model, "messages": self._convert(messages), "stream": True,
                         "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if self.num_thread:
            payload["options"]["num_thread"] = int(self.num_thread)
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if tools:
            payload["tools"] = tools
        if self.think is not None:
            payload["think"] = bool(self.think)
        res = ChatResult(engine=self.name, model=model, local=self.is_local)
        t0 = time.time()
        try:
            resp = http_open(self.url + "/api/chat", payload, timeout=self.timeout)
        except EngineError as e:
            msg = str(e)
            if tools and "does not support tools" in msg:
                raise ToolsUnsupported(msg) from None
            if "not found" in msg and "model" in msg:
                raise EngineError(f"Model '{model}' is not installed. Run: ollama pull {model}") from None
            raise
        with resp:
            for line in resp:
                if not line.strip():
                    continue
                try:
                    ch = json.loads(line)
                except Exception:
                    continue
                if ch.get("error"):
                    if tools and "does not support tools" in ch["error"]:
                        raise ToolsUnsupported(ch["error"])
                    raise EngineError(ch["error"])
                msg = ch.get("message") or {}
                if msg.get("thinking"):
                    res.thinking += msg["thinking"]
                    if on_think:
                        on_think(msg["thinking"])
                tok = msg.get("content") or ""
                if tok:
                    if not res.ttft:
                        res.ttft = time.time() - t0
                    res.content += tok
                    if on_token:
                        on_token(tok)
                for tc in msg.get("tool_calls") or []:
                    f = tc.get("function") or {}
                    res.tool_calls.append({"id": tc.get("id") or _new_call_id(), "name": f.get("name", ""),
                                           "arguments": loads_lenient(f.get("arguments"))})
                if ch.get("done"):
                    res.prompt_tokens = ch.get("prompt_eval_count", 0) or 0
                    res.completion_tokens = ch.get("eval_count", 0) or 0
        res.latency = time.time() - t0
        if not res.ttft:
            res.ttft = res.latency
        return res

    def embed(self, texts, model):
        try:
            d = http_json(self.url + "/api/embed", {"model": model, "input": texts}, timeout=180)
            return (d or {}).get("embeddings")
        except Exception:
            return None


class OpenAIEngine(Engine):
    """Any OpenAI-compatible /v1 server: LM Studio, llama.cpp, vLLM, OpenAI, OpenRouter, Groq..."""
    name = "openai"

    def __init__(self, url: str, api_key: str = "", timeout: float = 300, is_local: bool = True, name="openai"):
        self.url = (url or "https://api.openai.com/v1").rstrip("/")
        self.key, self.timeout, self.is_local, self.name = api_key, timeout, is_local, name
        self._avail = (0.0, False)
        self._no_usage = False

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def available(self) -> bool:
        if not self.is_local:
            return bool(self.key)
        if time.time() - self._avail[0] < (10 if self._avail[1] else 3):
            return self._avail[1]
        try:
            http_json(self.url + "/models", headers=self._h(), timeout=3)
            ok = True
        except Exception:
            ok = False
        self._avail = (time.time(), ok)
        return ok

    def list_models(self):
        d = http_json(self.url + "/models", headers=self._h(), timeout=8) or {}
        return [m.get("id", "") for m in d.get("data", [])]

    @staticmethod
    def _convert(messages):
        out = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m.get("content") or ""})
            elif role == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": [{"id": t["id"], "type": "function",
                                            "function": {"name": t["name"],
                                                         "arguments": json.dumps(t.get("arguments") or {})}}
                                           for t in m["tool_calls"]]})
            elif role == "user" and m.get("images"):
                parts = [{"type": "text", "text": m.get("content") or ""}]
                parts += [{"type": "image_url", "image_url": {"url": f"data:{_image_mime(b)};base64,{b}"}}
                          for b in m["images"]]
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": role, "content": m.get("content") or ""})
        return out

    def chat(self, messages, model, tools=None, on_token=None, on_think=None, temperature=0.4):
        payload: dict = {"model": model, "messages": self._convert(messages), "stream": True,
                         "temperature": temperature}
        if not self._no_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
        res = ChatResult(engine=self.name, model=model, local=self.is_local)
        t0 = time.time()
        try:
            resp = http_open(self.url + "/chat/completions", payload, headers=self._h(), timeout=self.timeout)
        except EngineError as e:
            if "stream_options" in str(e) and not self._no_usage:
                self._no_usage = True
                return self.chat(messages, model, tools, on_token, on_think, temperature)
            if tools and re.search(r"tool|function", str(e), re.I) and "400" in str(e):
                raise ToolsUnsupported(str(e)) from None
            raise
        acc: dict[int, dict] = {}
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ch = json.loads(data)
                except Exception:
                    continue
                if ch.get("error"):
                    raise EngineError(str(ch["error"]))
                u = ch.get("usage")
                if u:
                    res.prompt_tokens = u.get("prompt_tokens", 0) or 0
                    res.completion_tokens = u.get("completion_tokens", 0) or 0
                for choice in ch.get("choices") or []:
                    d = choice.get("delta") or {}
                    think = d.get("reasoning_content") or d.get("reasoning")
                    if think:
                        res.thinking += think
                        if on_think:
                            on_think(think)
                    tok = d.get("content") or ""
                    if tok:
                        if not res.ttft:
                            res.ttft = time.time() - t0
                        res.content += tok
                        if on_token:
                            on_token(tok)
                    for tc in d.get("tool_calls") or []:
                        a = acc.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            a["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            a["name"] += fn["name"]
                        if fn.get("arguments"):
                            a["args"] += fn["arguments"] if isinstance(fn["arguments"], str) else json.dumps(fn["arguments"])
        for i in sorted(acc):
            a = acc[i]
            res.tool_calls.append({"id": a["id"] or _new_call_id(), "name": a["name"],
                                   "arguments": loads_lenient(a["args"])})
        res.latency = time.time() - t0
        if not res.ttft:
            res.ttft = res.latency
        if not res.completion_tokens:
            res.completion_tokens = max(1, len(res.content) // 4)
        return res

    def embed(self, texts, model):
        try:
            d = http_json(self.url + "/embeddings", {"model": model, "input": texts}, headers=self._h(), timeout=180)
            return [x["embedding"] for x in (d or {}).get("data", [])]
        except Exception:
            return None


class AnthropicEngine(Engine):
    name = "anthropic"
    is_local = False

    def __init__(self, api_key: str, url: str = "", timeout: float = 300):
        self.key, self.timeout = api_key, timeout
        self.url = (url or "https://api.anthropic.com").rstrip("/")

    def _h(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    def available(self):
        return bool(self.key)

    def list_models(self):
        try:
            d = http_json(self.url + "/v1/models", headers=self._h(), timeout=8) or {}
            return [m.get("id", "") for m in d.get("data", [])]
        except Exception:
            return []

    @staticmethod
    def _convert(messages):
        system, out = [], []

        def push(role, blocks):
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": list(blocks)})

        for m in messages:
            role = m["role"]
            if role == "system":
                system.append(m.get("content") or "")
            elif role == "user":
                blocks = [{"type": "image", "source": {"type": "base64", "media_type": _image_mime(b), "data": b}}
                          for b in (m.get("images") or [])]
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                push("user", blocks or [{"type": "text", "text": "."}])
            elif role == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for t in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": t["id"], "name": t["name"], "input": t.get("arguments") or {}})
                push("assistant", blocks or [{"type": "text", "text": "."}])
            elif role == "tool":
                push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                               "content": m.get("content") or ""}])
        return "\n\n".join(system), out

    def chat(self, messages, model, tools=None, on_token=None, on_think=None, temperature=0.4):
        system, msgs = self._convert(messages)
        payload: dict = {"model": model, "max_tokens": 8192, "messages": msgs, "stream": True,
                         "temperature": temperature}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                                 "input_schema": t["function"]["parameters"]} for t in tools]
        res = ChatResult(engine=self.name, model=model, local=False)
        t0 = time.time()
        blocks: dict[int, dict] = {}
        with http_open(self.url + "/v1/messages", payload, headers=self._h(), timeout=self.timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                t = ev.get("type")
                if t == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    res.prompt_tokens = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                elif t == "content_block_start":
                    cb = ev.get("content_block") or {}
                    if cb.get("type") == "tool_use":
                        blocks[ev.get("index", 0)] = {"id": cb.get("id"), "name": cb.get("name"), "json": ""}
                elif t == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        tok = d.get("text", "")
                        if not res.ttft:
                            res.ttft = time.time() - t0
                        res.content += tok
                        if on_token:
                            on_token(tok)
                    elif d.get("type") == "input_json_delta":
                        blocks.setdefault(ev.get("index", 0), {"id": None, "name": "", "json": ""})["json"] += d.get("partial_json", "")
                    elif d.get("type") == "thinking_delta":
                        res.thinking += d.get("thinking", "")
                elif t == "message_delta":
                    u = ev.get("usage") or {}
                    res.completion_tokens = u.get("output_tokens", res.completion_tokens) or 0
                elif t == "error":
                    raise EngineError(str(ev.get("error")))
        for i in sorted(blocks):
            b = blocks[i]
            res.tool_calls.append({"id": b["id"] or _new_call_id(), "name": b["name"],
                                   "arguments": loads_lenient(b["json"]) if b["json"] else {}})
        res.latency = time.time() - t0
        if not res.ttft:
            res.ttft = res.latency
        return res


def build_engine(sec: dict, cfg: Config, is_local: bool) -> Engine | None:
    prov = (sec.get("provider") or "ollama").lower()
    key = os.environ.get(sec.get("api_key_env") or "", "") or sec.get("api_key", "")
    timeout = cfg.get("engine.timeout", 300)
    if prov == "ollama":
        return OllamaEngine(sec.get("url") or "http://127.0.0.1:11434", cfg.get("engine.num_ctx", 16384),
                            timeout, cfg.get("engine.think"), is_local=is_local,
                            num_thread=int(cfg.get("engine.num_thread", 0) or 0),
                            keep_alive=str(cfg.get("engine.keep_alive", "") or ""))
    if prov in ("openai", "openai-compatible", "lmstudio", "llamacpp", "vllm", "openrouter", "groq"):
        default = {"lmstudio": "http://127.0.0.1:1234/v1", "llamacpp": "http://127.0.0.1:8080/v1",
                   "vllm": "http://127.0.0.1:8000/v1", "openrouter": "https://openrouter.ai/api/v1",
                   "groq": "https://api.groq.com/openai/v1"}.get(prov, "https://api.openai.com/v1")
        if not is_local and not key:
            return None
        return OpenAIEngine(sec.get("url") or default, key, timeout, is_local=is_local, name=prov)
    if prov == "anthropic":
        return AnthropicEngine(key, sec.get("url", ""), timeout) if key else None
    raise ValueError(f"Unknown provider '{prov}'")
