"""Time and calculator tools."""

from __future__ import annotations

import ast
import datetime as dt
import math
import operator
import re

from ..registry import S, tool


# -- safe calculator ---------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_MATH = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_MATH.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "ln": math.log})


def safe_eval(expr: str):
    e = expr.strip().replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-")
    e = re.sub(r"(?<=\d),(?=\d{3}\b)", "", e)
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(])", r"(\1/100)", e)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, complex)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 10000:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _MATH and not callable(_MATH[n.id]):
            return _MATH[n.id]
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(x) for x in n.elts]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_MATH.get(n.func.id)) and not n.keywords:
            args = [ev(a) for a in n.args]
            if n.func.id == "factorial" and args and args[0] > 5000:
                raise ValueError("factorial argument too large")
            return _MATH[n.func.id](*args)
        raise ValueError(f"unsupported syntax: {type(n).__name__}")

    return ev(ast.parse(e, mode="eval"))


def _fmt_num(v) -> str:
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.12g}"
    return str(v)


@tool("get_time", "Get the current local date, time, weekday and timezone.")
def t_time(ag):
    n = dt.datetime.now().astimezone()
    return n.strftime("%A, %d %B %Y, %H:%M:%S %Z (UTC%z)")


@tool("calculator", "Evaluate a math expression exactly. Use for ANY arithmetic instead of mental math. "
      "Supports + - * / // % ** and functions like sqrt, log, sin, cos, factorial, pi, e.",
      {"expression": S("The expression, e.g. (1234*5678)-91011 or sqrt(2)*pi")}, ["expression"])
def t_calc(ag, expression: str):
    return f"{expression} = {_fmt_num(safe_eval(expression))}"
