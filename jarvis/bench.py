"""Benchmark: accuracy against latency and energy."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from typing import TYPE_CHECKING

from .agent import Agent
from .common import HOME, bold, dim, green, loads_lenient, red, strip_think

if TYPE_CHECKING:
    from .app import App


BENCH = [
    {"q": "What is 17 * 23? Reply with only the number.", "check": ("regex", r"\b391\b")},
    {"q": "What is the capital of Australia? One word.", "check": ("contains", "canberra")},
    {"q": "Which is larger: 9.11 or 9.9? Reply with only the larger number.", "check": ("regex", r"(?<![\d.])9\.9(?![\d])")},
    {"q": "How many times does the letter r appear in the word 'strawberry'? Reply with only the number.",
     "check": ("regex", r"\b3\b|three")},
    {"q": "Reverse the word 'jarvis'. Reply with only the reversed word.", "check": ("contains", "sivraj")},
    {"q": 'Return a JSON object with key "name" set to "Ada" and key "year" set to 1815. Output only the JSON.',
     "check": ("json", {"name": "Ada", "year": 1815})},
    {"q": "What is the chemical symbol for gold? Reply with only the symbol.", "check": ("regex", r"\bAu\b")},
    {"q": "A train leaves at 14:40 and the trip takes 2 hours 35 minutes. When does it arrive? Reply HH:MM.",
     "check": ("contains", "17:15")},
    {"q": "Translate 'thank you' into Spanish. Reply with only the translation.", "check": ("contains", "gracias")},
    {"q": "A shirt costs $40 after a 20% discount. What was the original price in dollars? Reply with only the number.",
     "check": ("regex", r"\b50(\.0+)?\b")},
    {"q": "Use the calculator tool to compute (1234*5678)-91011. Reply with only the result.",
     "check": ("regex", r"6,?915,?641"), "preset": "lite"},
    {"q": "Use your tools to find out what weekday it is today. Reply with only the weekday name.",
     "check": ("weekday", None), "preset": "lite"},
]


def bench_check(check, answer: str) -> bool:
    kind, val = check
    a = strip_think(answer)
    if kind == "contains":
        return val.lower() in a.lower()
    if kind == "regex":
        return re.search(val, a, re.I) is not None
    if kind == "json":
        d = loads_lenient(a)
        return all(d.get(k) == v for k, v in val.items())
    if kind == "weekday":
        return dt.datetime.now().strftime("%A").lower() in a.lower()
    return False


def run_bench(app: App, models: list[str] | None, routing: str | None = None, out=print) -> list[dict]:
    targets = models or [None]
    summary = []
    for m in targets:
        label = m or "default"
        out(bold(f"\n▶ Benchmarking {label}"))
        rows = []
        for i, item in enumerate(BENCH, 1):
            sid = f"bench-{uuid.uuid4().hex[:8]}"
            ag = Agent(app, preset=item.get("preset", "chat"), session_id=sid, routing=routing, model=m,
                       approver=lambda n, a: False)
            try:
                ans = ag.run(item["q"])
            except Exception as e:
                ans = f"ERROR: {e}"
            ok = bench_check(item["check"], ans)
            st = ag.last_stats or {}
            rows.append({"q": item["q"], "answer": ans[:200], "ok": ok, **{k: st.get(k) for k in
                         ("model", "latency", "completion_tokens", "tps", "joules", "energy_method", "local")}})
            out(f"  {green('✓') if ok else red('✗')} {i:>2}. {item['q'][:60]:<60} {dim(ans.strip()[:40].replace(chr(10), ' '))}")
            app.delete_session(sid)
        n = len(rows)
        acc = sum(r["ok"] for r in rows) / n
        joules = [r["joules"] for r in rows if r["joules"] is not None]
        summary.append({
            "model": next((r["model"] for r in rows if r["model"]), label), "accuracy": acc,
            "avg_latency": sum(r["latency"] or 0 for r in rows) / n,
            "avg_tps": sum(r["tps"] or 0 for r in rows) / n,
            "joules_per_query": (sum(joules) / len(joules)) if joules else None,
            "energy_method": next((r["energy_method"] for r in rows if r["energy_method"]), ""),
            "acc_per_kJ": (acc / (sum(joules) / len(joules) / 1000)) if joules and sum(joules) > 0 else None,
            "items": rows})
    out(bold("\nResults"))
    out(f"{'model':<28}{'acc':>7}{'lat(s)':>9}{'tok/s':>8}{'J/query':>10}{'acc/kJ':>9}  energy")
    for s in summary:
        jq = f"{s['joules_per_query']:.1f}" if s["joules_per_query"] is not None else "-"
        ak = f"{s['acc_per_kJ']:.1f}" if s["acc_per_kJ"] is not None else "-"
        out(f"{s['model'][:27]:<28}{s['accuracy']:>7.0%}{s['avg_latency']:>9.2f}{s['avg_tps']:>8.0f}{jq:>10}{ak:>9}  {s['energy_method']}")
    d = HOME / "bench"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"bench-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    f.write_text(json.dumps(summary, indent=2, default=str), "utf-8")
    out(dim(f"\nSaved {f}"))
    return summary
