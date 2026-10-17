"""Energy measurement for local model calls from RAPL or nvidia-smi. Machines with neither report nothing."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


class EnergyMeter:
    _rapl: list[Path] | None = None
    _nvsmi: str | None | bool = False

    def __init__(self, cfg: Config):
        self.enabled = bool(cfg.get("telemetry.measure_energy", True))
        if EnergyMeter._rapl is None:
            zones = []
            base = Path("/sys/class/powercap")
            if base.exists():
                for z in sorted(base.glob("intel-rapl:*")):
                    if z.name.count(":") == 1 and (z / "energy_uj").exists():
                        try:
                            int((z / "energy_uj").read_text())
                            zones.append(z)
                        except Exception:
                            pass
            EnergyMeter._rapl = zones
        if EnergyMeter._nvsmi is False:
            EnergyMeter._nvsmi = shutil.which("nvidia-smi")
        self._stop = threading.Event()
        self._gpu_j = 0.0
        self._thread = None

    @classmethod
    def methods(cls, cfg) -> str:
        m = cls(cfg)
        parts = []
        if m._rapl:
            parts.append("RAPL (CPU package counters)")
        if m._nvsmi:
            parts.append("nvidia-smi (GPU power)")
        return " + ".join(parts) or "none (no readable power counters)"

    def _read_rapl(self):
        vals = []
        for z in self._rapl or []:
            try:
                vals.append((int((z / "energy_uj").read_text()), int((z / "max_energy_range_uj").read_text())))
            except Exception:
                vals.append((0, 0))
        return vals

    def _gpu_loop(self):
        last = time.time()
        while not self._stop.is_set():
            try:
                out = subprocess.run([self._nvsmi, "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=2).stdout
                watts = sum(float(x) for x in out.split() if re.match(r"^[\d.]+$", x))
            except Exception:
                watts = 0.0
            now = time.time()
            self._gpu_j += watts * (now - last)
            last = now
            self._stop.wait(0.25)

    def start(self):
        self.t0 = time.time()
        if not self.enabled:
            return self
        self.r0 = self._read_rapl()
        if self._nvsmi:
            self._thread = threading.Thread(target=self._gpu_loop, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> tuple[float | None, str]:
        if not self.enabled:
            return None, "off"
        joules, methods = 0.0, []
        if self._rapl:
            r1 = self._read_rapl()
            for (a, mx), (b, _) in zip(self.r0, r1):
                d = b - a
                if d < 0 and mx:
                    d += mx
                joules += max(d, 0) / 1e6
            methods.append("rapl")
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=3)
            joules += self._gpu_j
            methods.append("gpu")
        if not methods:
            return None, ""
        return joules, "+".join(methods)
