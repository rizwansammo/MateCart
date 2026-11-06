"""Host facts, resource metrics and TLS certificate observation.

Unknown values stay ``None`` ("Not reported"); nothing is estimated (D-113).
"""

import os
import platform
import shutil
import socket
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import VERSION, validation


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def os_release() -> dict[str, str]:
    text = _read("/etc/os-release") or ""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def memory_mb() -> tuple[int | None, int | None]:
    text = _read("/proc/meminfo")
    if text is None:
        return None, None
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            values[parts[0].rstrip(":")] = int(parts[1])
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    return (
        total // 1024 if total else None,
        available // 1024 if available is not None else None,
    )


def _cpu_times() -> tuple[int, int] | None:
    text = _read("/proc/stat")
    if not text:
        return None
    fields = [int(item) for item in text.splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return idle, sum(fields)


def cpu_percent(sample_seconds: float = 0.5) -> float | None:
    first = _cpu_times()
    if first is None:
        return None
    time.sleep(sample_seconds)
    second = _cpu_times()
    if second is None:
        return None
    idle = second[0] - first[0]
    total = second[1] - first[1]
    return round(100.0 * (1 - idle / total), 2) if total > 0 else None


def disk(path: str) -> tuple[float | None, int | None]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None, None
    return round(100.0 * usage.used / usage.total, 2), usage.free // (1024**3)


def facts(root: str) -> dict[str, Any]:
    release = os_release()
    total_mb, _ = memory_mb()
    _, free_gb = disk(root if Path(root).exists() else "/")
    try:
        total_gb = shutil.disk_usage(root if Path(root).exists() else "/").total // (1024**3)
    except OSError:
        total_gb = None
    result: dict[str, Any] = {
        "hostname": socket.gethostname()[:253],
        "os": release.get("PRETTY_NAME", platform.system())[:120],
        "kernel": platform.release()[:120],
        "architecture": platform.machine()[:40],
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
        "agent_version": VERSION,
    }
    if total_mb is not None:
        result["ram_mb"] = total_mb
    if total_gb is not None:
        result["disk_gb"] = total_gb
    if free_gb is not None:
        result["disk_free_gb"] = free_gb
    return result


def metrics(root: str) -> dict[str, float | None]:
    total, available = memory_mb()
    ram = round(100.0 * (1 - available / total), 2) if total and available is not None else None
    used_percent, _ = disk(root if Path(root).exists() else "/")
    return {"cpu_percent": cpu_percent(), "ram_percent": ram, "disk_percent": used_percent}


def certificate_state(
    domain: str, *, port: int = 443, cafile: str | None = None, host: str = "127.0.0.1"
) -> dict[str, Any]:
    """Handshake with the local proxy using SNI ``domain`` and the expected trust anchor (public
    CAs for ACME, the proxy's local CA for internal test certificates)."""
    name = validation.domain(domain)
    context = ssl.create_default_context(cafile=cafile)
    try:
        with (
            socket.create_connection((host, port), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname=name) as tls,
        ):
            cert = tls.getpeercert()
    except (OSError, ssl.SSLError):
        return {"domain": name, "tls_state": "invalid", "renewal_state": "unknown"}
    if not cert:
        return {"domain": name, "tls_state": "missing", "renewal_state": "unknown"}
    not_after = ssl.cert_time_to_seconds(str(cert["notAfter"]))
    not_before = ssl.cert_time_to_seconds(str(cert["notBefore"]))
    return {
        "domain": name,
        "tls_state": "valid",
        "renewal_state": renewal_state(not_before, not_after, time.time()),
        "certificate_expires_at": datetime.fromtimestamp(not_after, UTC).isoformat(),
    }


def renewal_state(not_before: float, not_after: float, now: float) -> str:
    """Caddy renews with about a third of the lifetime left. Below a tenth, renewal is failing."""
    lifetime = not_after - not_before
    if lifetime <= 0:
        return "unknown"
    remaining = (not_after - now) / lifetime
    if remaining > 1 / 3:
        return "ok"
    if remaining > 1 / 10:
        return "renewal_due"
    return "renewal_failed"
