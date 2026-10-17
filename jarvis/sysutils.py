"""Small OS helpers: memory, CPU and battery readings, system info and desktop notifications."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .common import IS_WIN

_cpu_last: tuple[int, int] | None = None


def lan_ip() -> str | None:
    """This PC's address on the local network (what a phone on the same Wi-Fi would use), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # nothing is sent; this only makes the OS pick the outgoing interface
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def memory_info() -> tuple[int, int] | None:
    """(total, available) physical memory in bytes, or None if it cannot be read."""
    try:
        if IS_WIN:
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                            ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                            ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]
            st = Status()
            st.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.total, st.avail
        elif Path("/proc/meminfo").exists():
            mi = dict(re.findall(r"(\w+):\s+(\d+)", Path("/proc/meminfo").read_text()))
            return int(mi["MemTotal"]) * 1024, int(mi.get("MemAvailable", 0)) * 1024
    except Exception:
        pass
    return None


def _win_cpu_times() -> tuple[int, int]:
    idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return idle.value, kernel.value + user.value  # kernel time already includes idle time


def cpu_percent() -> float | None:
    """CPU use in percent: since the previous call on Windows, the 1-minute load average elsewhere."""
    global _cpu_last
    try:
        if IS_WIN:
            prev = _cpu_last
            if prev is None:
                prev = _win_cpu_times()
                time.sleep(0.25)
            now = _cpu_last = _win_cpu_times()
            span = now[1] - prev[1]
            if span <= 0:
                return None
            return max(0.0, min(100.0, 100.0 * (span - (now[0] - prev[0])) / span))
        if hasattr(os, "getloadavg"):
            return min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100)
    except Exception:
        pass
    return None


def _battery() -> tuple[int, bool] | None:
    """(percent, on AC power or charging) or None if there is no battery or it cannot be read."""
    try:
        if IS_WIN:
            class Power(ctypes.Structure):
                _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                            ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full_life", ctypes.c_ulong)]
            st = Power()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)) and st.percent <= 100 and not st.flag & 128:
                return st.percent, st.ac == 1
            return None
        bats = list(Path("/sys/class/power_supply").glob("BAT*"))
        if bats:
            pct = int((bats[0] / "capacity").read_text().strip())
            st = (bats[0] / "status").read_text().strip().lower()
            return pct, st in ("charging", "full")
        if platform.system() == "Darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"(\d+)%;\s*(\w+)", out)
            if m:
                return int(m.group(1)), m.group(2).lower() in ("charging", "charged", "finishing")
    except Exception:
        pass
    return None


def _sysinfo() -> str:
    lines = [f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
             f"Python: {platform.python_version()}", f"CPUs: {os.cpu_count()}"]
    if hasattr(os, "getloadavg"):
        try:
            lines.append("Load avg: " + ", ".join(f"{x:.2f}" for x in os.getloadavg()))
        except OSError:
            pass
    mem = memory_info()
    if mem:
        lines.append(f"Memory: {mem[1] / 2**30:.1f} GB free of {mem[0] / 2**30:.1f} GB")
    elif platform.system() == "Darwin":
        try:
            tot = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
            lines.append(f"Memory: {tot:.1f} GB total")
        except Exception:
            pass
    du = shutil.disk_usage(str(Path.home()))
    lines.append(f"Disk (home): {du.free / 2**30:.1f} GB free of {du.total / 2**30:.1f} GB")
    bat = _battery()
    if bat:
        lines.append(f"Battery: {bat[0]}% ({'on AC power' if bat[1] else 'on battery'})")
    return "\n".join(lines)


def notify(title: str, msg: str):
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(msg[:240], ensure_ascii=False)} "
                                               f"with title {json.dumps(title, ensure_ascii=False)}"], timeout=5)
        elif sysname == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, msg[:400]], timeout=5)
        elif sysname == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  f"$n.ShowBalloonTip(8000,{json.dumps(title)},{json.dumps(msg[:240])},'Info');Start-Sleep 9;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
    except Exception:
        pass
