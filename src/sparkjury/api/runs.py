"""Run manager: starts pipeline runs in background threads and fans events out to SSE subscribers."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from sparkjury.harness import Event, EventBus, EventKind, Orchestrator, RunConfig


class RunManager:
    def __init__(self, runs_dir: str | Path = "runs"):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._live: dict[str, dict[str, Any]] = {}     # run_id -> {"events": [...], "subs": set[Queue], "done": bool, "thread": Thread}

    # ---- lifecycle ------------------------------------------------------------

    def start(self, cfg: RunConfig) -> str:
        run_id = cfg.run_id
        with self._lock:
            if run_id in self._live and not self._live[run_id]["done"]:
                raise ValueError(f"run {run_id} is already running")
            state: dict[str, Any] = {"events": [], "subs": set(), "done": False, "thread": None, "manifest": None}
            self._live[run_id] = state
        cfg.runs_dir = str(self.runs_dir)
        orch = Orchestrator(cfg)
        orch.bus.subscribe(lambda ev: self._on_event(run_id, ev))

        def _target() -> None:
            try:
                state["manifest"] = orch.run()
            finally:
                state["done"] = True
                for q in list(state["subs"]):
                    q.put(None)

        t = threading.Thread(target=_target, name=f"sparkjury-run-{run_id}", daemon=True)
        state["thread"] = t
        t.start()
        return run_id

    def _on_event(self, run_id: str, ev: Event) -> None:
        state = self._live.get(run_id)
        if not state:
            return
        state["events"].append(ev)
        for q in list(state["subs"]):
            q.put(ev)

    def is_running(self, run_id: str) -> bool:
        s = self._live.get(run_id)
        return bool(s) and not s["done"]

    def wait(self, run_id: str, timeout: float | None = None) -> dict[str, Any] | None:
        s = self._live.get(run_id)
        if not s or not s["thread"]:
            return None
        s["thread"].join(timeout)
        return s["manifest"]

    # ---- reads ----------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def manifest(self, run_id: str) -> dict[str, Any] | None:
        s = self._live.get(run_id)
        if s and s["manifest"]:
            return s["manifest"]
        p = self.run_dir(run_id) / "manifest.json"
        if not p.exists():
            if s:  # running, no manifest yet
                return {"run_id": run_id, "status": "running", "stages": {}, "degradations": []}
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        seen = set()
        for p in sorted(self.runs_dir.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            seen.add(m.get("run_id", p.parent.name))
            out.append(_summary(m))
        for run_id, s in self._live.items():
            if run_id not in seen:
                out.insert(0, {"run_id": run_id, "status": "running", "duration_s": None, "degradations": 0, "pass_rate": None, "n_badcases": None, "recommendation": None})
        return out

    def events(self, run_id: str, since: int = 0) -> list[Event]:
        s = self._live.get(run_id)
        evs = list(s["events"]) if s else EventBus.read_log(self.run_dir(run_id) / "events.jsonl")
        return [e for e in evs if e.seq > since]

    def subscribe(self, run_id: str) -> "queue.Queue[Event | None] | None":
        s = self._live.get(run_id)
        if not s or s["done"]:
            return None
        q: queue.Queue = queue.Queue()
        s["subs"].add(q)
        return q

    def unsubscribe(self, run_id: str, q: queue.Queue) -> None:
        s = self._live.get(run_id)
        if s:
            s["subs"].discard(q)

    def db_path(self, run_id: str) -> Path | None:
        m = self.manifest(run_id)
        if not m:
            return None
        db = (m.get("config") or {}).get("db")
        return Path(db) if db else None


def _summary(m: dict[str, Any]) -> dict[str, Any]:
    rep = m.get("stages", {}).get("REPORT", {})
    return {
        "run_id": m.get("run_id"), "status": m.get("status"), "duration_s": m.get("duration_s"),
        "degradations": len(m.get("degradations", [])), "pass_rate": rep.get("pass_rate"), "pass_k": rep.get("pass_k"),
        "n_badcases": rep.get("n_badcases"), "recommendation": rep.get("recommendation"),
        "models": m.get("models", {}), "title": ((m.get("config") or {}).get("report") or {}).get("title"),
    }
