"""DGX resource panel: nvidia-smi sampling plus reachability of the model endpoints."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any

import httpx

DEFAULT_ENDPOINTS = [
    {"name": "judge_a (Qwen)", "url": "http://127.0.0.1:8001/v1"},
    {"name": "judge_b (Nemotron)", "url": "http://127.0.0.1:8002/v1"},
    {"name": "embedding", "url": "http://127.0.0.1:8003/v1"},
    {"name": "agent under test", "url": "http://127.0.0.1:8004/v1"},
]

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def sample_gpus(timeout_s: float = 2.0) -> dict[str, Any]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "reason": "nvidia-smi not found", "gpus": []}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_s, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        return {"available": False, "reason": f"{type(e).__name__}", "gpus": []}
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            used, total = _num(parts[2]), _num(parts[3])
            unified = False
            if used is None or total is None:
                # DGX Spark (GB10): nvidia-smi reports [N/A] for memory because it is unified with system RAM
                used, total = _system_memory_mb()
                unified = True
            gpus.append({
                "index": int(parts[0]), "name": parts[1],
                "mem_used_mb": used, "mem_total_mb": total, "unified_memory": unified,
                "util_pct": _num(parts[4]) or 0.0, "temp_c": _num(parts[5]) if len(parts) > 5 else None,
            })
        except ValueError:
            continue
    return {"available": bool(gpus), "gpus": gpus}


def _num(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _system_memory_mb() -> tuple[float, float]:
    """(used, total) MB from /proc/meminfo; used = total - available (page cache is reclaimable)."""
    try:
        info: dict[str, float] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = float(v.strip().split()[0]) / 1024.0
        total = info["MemTotal"]
        return round(total - info.get("MemAvailable", 0.0), 1), round(total, 1)
    except (OSError, KeyError, ValueError):
        return 0.0, 0.0


def probe_endpoints(endpoints: list[dict[str, str]] | None = None, timeout_s: float = 0.6) -> list[dict[str, Any]]:
    out = []
    for ep in endpoints or DEFAULT_ENDPOINTS:
        url = ep["url"].rstrip("/") + "/models"
        t0 = time.perf_counter()
        ok, models, err = False, [], None
        try:
            r = httpx.get(url, timeout=timeout_s)
            ok = r.status_code == 200
            if ok:
                try:
                    models = [m.get("id") for m in r.json().get("data", [])][:5]
                except ValueError:
                    models = []
            else:
                err = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            err = type(e).__name__
        out.append({"name": ep["name"], "url": ep["url"], "ok": ok, "models": models, "error": err,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
    return out


def snapshot(endpoints: list[dict[str, str]] | None = None, max_age_s: float = 2.0, probe: bool = True) -> dict[str, Any]:
    with _lock:
        now = time.time()
        if _cache["data"] is not None and now - _cache["ts"] < max_age_s:
            return _cache["data"]
        data = {"ts": now, **sample_gpus(), "endpoints": probe_endpoints(endpoints) if probe else []}
        _cache["ts"], _cache["data"] = now, data
        return data
