"""Learning from rated conversations: fine-tune export and a personalised Ollama model."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .common import HOME, green, red
from .presets import PRESETS

if TYPE_CHECKING:
    from .app import App


def learn_export(app: App, out_path: Path) -> tuple[int, int]:
    pos = app.db.q("SELECT system, prompt, response FROM traces WHERE feedback>0 ORDER BY ts")
    neg = app.db.q("SELECT system, prompt, response FROM traces WHERE feedback<0 ORDER BY ts")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in pos:
            f.write(json.dumps({"messages": [{"role": "system", "content": r["system"]},
                                             {"role": "user", "content": r["prompt"]},
                                             {"role": "assistant", "content": r["response"]}]}, ensure_ascii=False) + "\n")
    rej = out_path.with_name(out_path.stem + "-rejected.jsonl")
    with open(rej, "w", encoding="utf-8") as f:
        for r in neg:
            f.write(json.dumps({"prompt": r["prompt"], "rejected": r["response"]}, ensure_ascii=False) + "\n")
    return len(pos), len(neg)


def learn_modelfile(app: App, base: str, name: str, create: bool, out=print) -> Path:
    q = lambda s: s.replace('"""', "'''")  # noqa: E731
    pos = app.db.q("SELECT prompt, response FROM traces WHERE feedback>0 AND length(response)<1500 "
                   "AND length(prompt)<800 ORDER BY ts DESC LIMIT 12")
    neg = app.db.q("SELECT prompt FROM traces WHERE feedback<0 ORDER BY ts DESC LIMIT 5")
    facts = [r["text"] for r in app.memory.facts(30)]
    system = PRESETS["jarvis"]["prompt"].replace("{user}", app.cfg.get("agent.user_name") or "the user")\
        .replace("{address}", app.cfg.get("agent.address") or "sir")
    if app.cfg.get("agent.persona"):
        system += "\n" + app.cfg.get("agent.persona")
    if facts:
        system += "\n\nKnown facts about the user:\n" + "\n".join(f"- {f}" for f in facts)
    if neg:
        system += ("\n\nThe user disliked earlier answers to prompts like: " +
                   "; ".join(r["prompt"][:80] for r in neg) + ". Be extra careful and precise on such topics.")
    lines = [f"FROM {base}", f'SYSTEM """{q(system)}"""', "PARAMETER temperature 0.4"]
    for r in reversed(pos):
        lines.append(f'MESSAGE user """{q(r["prompt"])}"""')
        lines.append(f'MESSAGE assistant """{q(r["response"])}"""')
    path = HOME / "learn" / f"{name}.Modelfile"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", "utf-8")
    out(f"Wrote {path} ({len(pos)} example conversations, {len(facts)} facts).")
    if create:
        if not shutil.which("ollama"):
            out(red("ollama CLI not found on PATH."))
        else:
            r = subprocess.run(["ollama", "create", name, "-f", str(path)])
            if r.returncode == 0:
                out(green(f"Created model '{name}'. Use it with: jarvis config set engine.local.model {name}"))
    else:
        out(f"Create it with:  ollama create {name} -f \"{path}\"")
    return path
