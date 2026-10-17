"""Folder sandbox, secret-file denylist, command blocklist and SSRF guard."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from .common import HOME, LAUNCH_CWD

if TYPE_CHECKING:
    from .config import Config


SENSITIVE_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".password-store", ".netrc", ".git-credentials",
                   ".pgpass", "id_rsa", "id_ed25519", "shadow"}

DANGEROUS_CMDS = [re.compile(p, re.I) for p in [
    r"\brm\s+-[a-z]*(rf|fr)[a-z]*\s+(/|~|\*|\$HOME|/\*)(\s|$)",
    r"\bmkfs(\.\w+)?\b", r"\bdd\s+[^|;]*of=/dev/", r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\b(shutdown|reboot|halt|poweroff)\b", r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^|;]*\|\s*(sudo\s+)?(ba|z|da)?sh\b",
    r">\s*/dev/(sd[a-z]|nvme\d)", r"\bformat\s+[a-z]:", r"\bchmod\s+-R\s+777\s+/(\s|$)",
    r"\bdiskpart\b", r"Remove-Item\b.*-Recurse.*\b[A-Z]:\\(\s|$)", r"\bdel\s+/[sq]\b.*\b[A-Z]:\\(\s|$)",
]]


class Security:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        dirs = []
        for d in cfg.get("security.allowed_dirs") or []:
            p = LAUNCH_CWD if d in (".", "./") else Path(d).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True) if p == HOME / "workspace" else None
                dirs.append(p.resolve())
            except Exception:
                pass
        self.allowed = dirs or [(HOME / "workspace").resolve()]
        self.protected = {(HOME / "config.json").resolve(), (HOME / "jarvis.db").resolve()}

    @property
    def workdir(self) -> Path:
        for d in self.allowed:
            if d == LAUNCH_CWD.resolve():
                return d
        return self.allowed[0]

    def path(self, p: str, base: Path | None = None) -> Path:
        raw = Path(os.path.expandvars(str(p or "."))).expanduser()
        if not raw.is_absolute():
            raw = (base or self.workdir) / raw
        rp = raw.resolve()
        if rp in self.protected or any(part.lower() in SENSITIVE_PARTS for part in rp.parts):
            raise PermissionError(f"Access to {rp} is blocked (sensitive file).")
        for d in self.allowed:
            if rp == d or d in rp.parents:
                return rp
        raise PermissionError(f"{rp} is outside the allowed folders: {', '.join(map(str, self.allowed))}. "
                              "Add folders with: jarvis config set security.allowed_dirs '[\"...\"]'")

    def command(self, cmd: str):
        if not self.cfg.get("security.allow_shell", True):
            raise PermissionError("Shell access is disabled (security.allow_shell=false).")
        for rx in DANGEROUS_CMDS:
            if rx.search(cmd):
                raise PermissionError("Command blocked by the safety filter as potentially destructive.")

    def url(self, url: str):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https"):
            raise PermissionError("Only http(s) URLs are allowed.")
        if self.cfg.get("security.allow_private_fetch", False):
            return
        try:
            infos = socket.getaddrinfo(u.hostname or "", None)
        except Exception:
            return  # resolution failure -> request will fail anyway
        for info in infos:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise PermissionError("Fetching private/local network addresses is blocked "
                                      "(set security.allow_private_fetch=true to allow).")
