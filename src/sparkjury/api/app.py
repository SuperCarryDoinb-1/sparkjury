"""FastAPI backend (M8): runs, SSE timeline, card, traces, confirm, regress, DGX panel, static cockpit."""

from __future__ import annotations

import json
import os
import queue
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from sparkjury import __version__
from sparkjury.api import dgx as dgx_mod
from sparkjury.api.runs import RunManager
from sparkjury.harness import EventKind, RunConfig, Stage
from sparkjury.report import build_card
from sparkjury.store import TraceStore

STATIC_DIR = Path(__file__).parent / "static"


class StartRun(BaseModel):
    demo: bool = False
    config_path: str | None = None
    run_id: str | None = None
    db: str | None = None
    stages: list[str] | None = None
    evalset_limit: int | None = None


class Confirm(BaseModel):
    cluster_id: int
    note: str = ""
    decided_by: str = "pm"


def create_app(runs_dir: str | Path = "runs", *, dgx_endpoints: list[dict[str, str]] | None = None, probe_endpoints: bool = True,
               token: str | None = None) -> FastAPI:
    """`token` (or env SPARKJURY_API_TOKEN) protects every route except /health: pass it as
    `Authorization: Bearer <token>` or `?token=<token>` (EventSource cannot set headers)."""
    app = FastAPI(title="SparkJury API", version=__version__)
    mgr = RunManager(runs_dir)
    app.state.manager = mgr
    api_token = token if token is not None else os.environ.get("SPARKJURY_API_TOKEN") or None
    app.state.token = api_token

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if api_token and request.url.path not in ("/health",):
            auth = request.headers.get("authorization", "")
            given = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
            if not given or not secrets.compare_digest(given, api_token):
                return JSONResponse({"detail": "unauthorized: pass Authorization: Bearer <token> or ?token="}, status_code=401)
        return await call_next(request)

    # ---- helpers ----------------------------------------------------------

    def _manifest_or_404(run_id: str) -> dict[str, Any]:
        m = mgr.manifest(run_id)
        if not m:
            raise HTTPException(404, f"run {run_id} not found")
        return m

    def _store(run_id: str) -> TraceStore:
        _manifest_or_404(run_id)
        db = mgr.db_path(run_id)
        if not db or not db.exists():
            raise HTTPException(409, f"run {run_id} has no database yet")
        return TraceStore(db)

    # ---- runs -------------------------------------------------------------

    @app.post("/runs", status_code=202)
    def start_run(body: StartRun) -> dict[str, Any]:
        if body.demo:
            cfg = RunConfig.demo(run_id=body.run_id, db=body.db)
        elif body.config_path:
            if not Path(body.config_path).exists():
                raise HTTPException(400, f"config not found: {body.config_path}")
            cfg = RunConfig.from_toml(body.config_path)
            if body.run_id:
                cfg.run_id = body.run_id
            if body.db:
                cfg.db = body.db
        else:
            raise HTTPException(400, "give demo=true or config_path")
        if body.stages:
            try:
                cfg.stages = [Stage(s.upper()) for s in body.stages]
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        if body.evalset_limit:
            cfg.evalset.limit = body.evalset_limit
        if body.demo and not body.db:
            cfg.db = str(Path(runs_dir) / cfg.run_id / "sparkjury.db")
        try:
            run_id = mgr.start(cfg)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"run_id": run_id, "status": "running", "events": f"/runs/{run_id}/events"}

    @app.get("/runs")
    def list_runs() -> list[dict[str, Any]]:
        return mgr.list_runs()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        m = dict(_manifest_or_404(run_id))
        m.pop("traceback", None)
        m["running"] = mgr.is_running(run_id)
        return m

    @app.get("/runs/{run_id}/events")
    def stream_events(run_id: str, since: int = Query(0, ge=0), follow: bool = True):
        _manifest_or_404(run_id)

        def gen():
            last = since
            for ev in mgr.events(run_id, since):
                last = ev.seq
                yield _sse(ev.kind.value, ev.model_dump_json())
                if ev.kind == EventKind.RUN_END:
                    return
            q = mgr.subscribe(run_id) if follow else None
            if q is None:
                yield _sse("end", json.dumps({"run_id": run_id, "reason": "not running"}))
                return
            try:
                idle = 0.0
                while True:
                    try:
                        ev = q.get(timeout=1.0)
                    except queue.Empty:
                        idle += 1.0
                        if idle >= 15.0:
                            idle = 0.0
                            yield ": keepalive\n\n"
                        continue
                    if ev is None:
                        yield _sse("end", json.dumps({"run_id": run_id, "reason": "finished"}))
                        return
                    if ev.seq <= last:
                        continue
                    last = ev.seq
                    yield _sse(ev.kind.value, ev.model_dump_json())
                    if ev.kind == EventKind.RUN_END:
                        return
            finally:
                mgr.unsubscribe(run_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/runs/{run_id}/events.json")
    def events_json(run_id: str, since: int = 0) -> list[dict[str, Any]]:
        _manifest_or_404(run_id)
        return [e.model_dump(mode="json") for e in mgr.events(run_id, since)]

    # ---- results --------------------------------------------------------------

    @app.get("/runs/{run_id}/card")
    def get_card(run_id: str) -> dict[str, Any]:
        p = mgr.run_dir(run_id) / "card" / "card.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        with _store(run_id) as store:
            return build_card(store, run_id=run_id).model_dump(mode="json")

    @app.get("/runs/{run_id}/card.html", response_class=HTMLResponse)
    def get_card_html(run_id: str) -> str:
        p = mgr.run_dir(run_id) / "card" / "card.html"
        if p.exists():
            return p.read_text(encoding="utf-8")
        from sparkjury.report import render_html

        with _store(run_id) as store:
            return render_html(build_card(store, run_id=run_id))

    @app.get("/runs/{run_id}/clusters")
    def get_clusters(run_id: str) -> dict[str, Any]:
        with _store(run_id) as store:
            run = store.get_cluster_run()
        if not run:
            return {"n_badcases": 0, "n_clusters": 0, "n_noise": 0, "clusters": []}
        d = run.model_dump(mode="json")
        d.pop("badcases", None)
        return d

    @app.get("/runs/{run_id}/traces")
    def list_traces(run_id: str, cluster: int | None = None, failed: bool | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with _store(run_id) as store:
            if cluster is not None:
                return [b.model_dump(mode="json") for b in store.list_badcases(cluster)]
            rows = []
            for t in store.list(success=(False if failed else None), limit=limit):
                pre = store.get_precheck(t.trace_id)
                dec = store.get_decision(t.trace_id)
                rows.append({
                    "trace_id": t.trace_id, "task_id": t.task_id, "trial": t.trial, "success": t.outcome.success,
                    "termination": t.outcome.termination_reason, "n_steps": t.metrics.n_steps, "n_tool_calls": t.metrics.n_tool_calls,
                    "env_failure": bool(pre and pre.is_env_failure), "final_scores": dec.scores if dec else None,
                    "degraded": dec.any_degraded if dec else None,
                })
            return rows

    @app.get("/runs/{run_id}/traces/{trace_id}")
    def get_trace(run_id: str, trace_id: str) -> dict[str, Any]:
        with _store(run_id) as store:
            t = store.get(trace_id)
            if not t:
                raise HTTPException(404, f"trace {trace_id} not found")
            pre, panel, dec = store.get_precheck(trace_id), store.get_panel_result(trace_id), store.get_decision(trace_id)
        return {
            "trace": t.model_dump(mode="json"), "transcript": t.transcript(),
            "precheck": pre.model_dump(mode="json") if pre else None,
            "panel": panel.model_dump(mode="json") if panel else None,
            "decision": dec.model_dump(mode="json") if dec else None,
        }

    @app.post("/runs/{run_id}/confirm")
    def confirm(run_id: str, body: Confirm) -> dict[str, Any]:
        m = _manifest_or_404(run_id)
        with _store(run_id) as store:
            run = store.get_cluster_run()
        cl = next((c for c in (run.clusters if run else []) if c.cluster_id == body.cluster_id), None)
        if cl is None:
            raise HTTPException(404, f"cluster {body.cluster_id} not found")
        rec = {"run_id": run_id, "cluster_id": cl.cluster_id, "label": cl.label.value, "rank": cl.rank, "size": cl.size,
               "suggestion": cl.suggestion, "note": body.note, "decided_by": body.decided_by, "ts": time.time(),
               "next": f"apply the fix, re-run the same tasks, then: sparkjury regress --before {m.get('config', {}).get('db')} --after <new db>"}
        p = mgr.run_dir(run_id) / "confirm.json"
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec

    @app.get("/runs/{run_id}/confirm")
    def get_confirm(run_id: str) -> dict[str, Any] | None:
        _manifest_or_404(run_id)
        p = mgr.run_dir(run_id) / "confirm.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @app.get("/runs/{run_id}/regress")
    def regress(run_id: str, before: str = Query(..., description="run_id of the earlier run")) -> dict[str, Any]:
        from sparkjury.regress import compare

        _manifest_or_404(before)
        db_b, db_a = mgr.db_path(before), mgr.db_path(run_id)
        if not db_b or not db_b.exists() or not db_a or not db_a.exists():
            raise HTTPException(409, "both runs need a database")
        r = compare(db_b, db_a, before_label=before, after_label=run_id)
        d = r.model_dump(mode="json")
        d["verdict"] = r.verdict
        return d

    # ---- DGX & misc --------------------------------------------------------------

    @app.get("/dgx")
    def dgx() -> dict[str, Any]:
        return dgx_mod.snapshot(dgx_endpoints, probe=probe_endpoints)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "runs_dir": str(runs_dir), "auth": bool(api_token)}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        p = STATIC_DIR / "index.html"
        if not p.exists():
            return "<h1>SparkJury</h1><p>cockpit page missing</p>"
        return p.read_text(encoding="utf-8")

    return app


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


app = create_app()
