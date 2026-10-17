"""Shell and Python execution tools."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile

from ..common import truncate
from ..registry import S, tool


@tool("run_shell", f"Run a shell command on the user's computer ({platform.system()}) in the working folder. "
      "Returns exit code, stdout and stderr.", {"command": S("The command line to run")}, ["command"], risky=True)
def t_run_shell(ag, command: str):
    ag.app.security.command(command)
    t = int(ag.app.cfg.get("security.tool_timeout", 60))
    try:
        r = subprocess.run(command, shell=True, cwd=str(ag.app.security.workdir), capture_output=True,
                           text=True, timeout=t, errors="replace")
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {t}s"
    out = f"exit code {r.returncode}\n"
    if r.stdout.strip():
        out += f"--- stdout ---\n{truncate(r.stdout, 10000)}\n"
    if r.stderr.strip():
        out += f"--- stderr ---\n{truncate(r.stderr, 4000)}\n"
    if not r.stdout.strip() and not r.stderr.strip():
        out += "(no output)"
    return out


@tool("run_python", "Run a Python 3 script in a separate process and return what it prints. Use print() "
      "to show results. Good for data processing, plotting to files, precise computation.",
      {"code": S("Complete Python source code")}, ["code"], risky=True)
def t_run_python(ag, code: str):
    t = int(ag.app.cfg.get("security.tool_timeout", 60))
    wd = ag.app.security.workdir
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script = f.name
    try:
        r = subprocess.run([sys.executable, "-I", script], cwd=str(wd), capture_output=True, text=True,
                           timeout=t, errors="replace")
    except subprocess.TimeoutExpired:
        return f"Error: script timed out after {t}s"
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
    out = ""
    if r.stdout.strip():
        out += truncate(r.stdout, 10000)
    if r.stderr.strip():
        out += f"\n--- stderr ---\n{truncate(r.stderr, 5000)}"
    if not out.strip():
        out = "(the script ran successfully but printed nothing - use print() to see values)"
    return f"exit code {r.returncode}\n{out}"
