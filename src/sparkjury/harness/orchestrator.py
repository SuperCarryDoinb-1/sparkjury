"""Orchestrator (M7): runs the evaluation pipeline as a state machine.

INGEST -> PRECHECK -> EVALSET -> SCORE -> ARBITRATE -> CLUSTER -> REPORT

Every stage emits start/end events with timings and counts, writes into the run manifest, and
degrades instead of failing when a model backend is unreachable:
  - an LLM judge that fails its health check is replaced by a mock judge (event: degraded)
  - Jev unconfigured/unreachable -> local arbitration (recorded per decision, summarised here)
  - embedding server unreachable -> hashing embedder
A stage that raises stops the run; the manifest records status=failed and the error.
"""

from __future__ import annotations

import json
import os
import platform
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from sparkjury import __version__
from sparkjury.adapters import load as load_traces
from sparkjury.arbiter import Arbiter, JevClient
from sparkjury.cluster import HashingEmbedder, OpenAIEmbedder, build_badcase, cluster_badcases, label_clusters
from sparkjury.harness.config import ALL_STAGES, RunConfig, Stage
from sparkjury.harness.events import EventBus, EventKind
from sparkjury.judges import MockJudge, OpenAICompatJudge, Panel, PanelConfig
from sparkjury.judges.panel import build_judges
from sparkjury.models.cluster import ClusterRun
from sparkjury.precheck import PrecheckConfig, run_many
from sparkjury.report import build_card, write_card
from sparkjury.store import TraceStore


class Orchestrator:
    def __init__(self, config: RunConfig, bus: EventBus | None = None):
        self.cfg = config
        self.run_dir = config.run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.bus = bus or EventBus(config.run_id, self.run_dir / "events.jsonl")
        self.manifest: dict[str, Any] = {
            "run_id": config.run_id, "status": "running", "sparkjury_version": __version__,
            "python": platform.python_version(), "host": platform.node(),
            "config": json.loads(config.model_dump_json()), "stages": {}, "degradations": [], "models": {},
        }
        # a partial re-run (e.g. --stages CLUSTER,REPORT) keeps the earlier stages' records and model info
        prev = self.run_dir / "manifest.json"
        if prev.exists() and set(config.stages) != set(ALL_STAGES):
            try:
                old = json.loads(prev.read_text(encoding="utf-8"))
                self.manifest["stages"] = {k: v for k, v in old.get("stages", {}).items() if Stage(k) not in config.stages}
                self.manifest["models"] = dict(old.get("models", {}))
                self.manifest["degradations"] = [d for d in old.get("degradations", []) if d.get("stage") not in {s.value for s in config.stages}]
                self.manifest["previous_run_at"] = old.get("finished_at")
            except (OSError, ValueError):
                pass
        self._store: TraceStore | None = None
        self._panel: Panel | None = None
        self._evalset_ids: list[str] = []

    # ---- public --------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        self.bus.publish(EventKind.RUN_START, f"run {self.cfg.run_id}", stages=[s.value for s in self.cfg.stages])
        if self.cfg.reset_db and Path(self.cfg.db).exists() and Stage.INGEST in self.cfg.stages:
            Path(self.cfg.db).unlink()
        self._store = TraceStore(self.cfg.db)
        handlers: dict[Stage, Callable[[], dict[str, Any]]] = {
            Stage.INGEST: self._ingest, Stage.PRECHECK: self._precheck, Stage.EVALSET: self._evalset,
            Stage.SCORE: self._score, Stage.ARBITRATE: self._arbitrate, Stage.CLUSTER: self._cluster, Stage.REPORT: self._report,
        }
        try:
            for stage in ALL_STAGES:
                if stage not in self.cfg.stages:
                    continue
                self._run_stage(stage, handlers[stage])
            self.manifest["status"] = "ok"
        except Exception as e:  # noqa: BLE001
            self.manifest["status"] = "failed"
            self.manifest["error"] = f"{type(e).__name__}: {e}"
            self.manifest["traceback"] = traceback.format_exc()
        finally:
            self.manifest["duration_s"] = round(time.perf_counter() - t0, 3)
            self.manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            # keep stage order canonical after a merge
            self.manifest["stages"] = {s.value: self.manifest["stages"][s.value] for s in ALL_STAGES if s.value in self.manifest["stages"]}
            self._store.put_run(self.cfg.run_id, self.manifest["status"], self.manifest)
            self._store.close()
            (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            self.bus.publish(EventKind.RUN_END, f"run {self.cfg.run_id} {self.manifest['status']}",
                             status=self.manifest["status"], duration_s=self.manifest["duration_s"],
                             degradations=len(self.manifest["degradations"]))
        return self.manifest

    # ---- stage wrapper ----------------------------------------------------------

    def _run_stage(self, stage: Stage, fn: Callable[[], dict[str, Any]]) -> None:
        self.bus.publish(EventKind.STAGE_START, stage.value, stage=stage.value)
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"status": "running"}
        self.manifest["stages"][stage.value] = rec
        try:
            out = fn() or {}
            rec.update(out)
            rec["status"] = "ok"
        except Exception as e:
            rec["status"] = "failed"
            rec["error"] = f"{type(e).__name__}: {e}"
            self.bus.publish(EventKind.ERROR, rec["error"], stage=stage.value)
            raise
        finally:
            rec["duration_s"] = round(time.perf_counter() - t0, 3)
            self.bus.publish(EventKind.STAGE_END, f"{stage.value} {rec['status']}", stage=stage.value,
                             duration_s=rec["duration_s"], **{k: v for k, v in rec.items() if k not in ("status", "duration_s", "error")})

    def _degrade(self, stage: Stage, component: str, reason: str, fallback: str) -> None:
        item = {"stage": stage.value, "component": component, "reason": reason, "fallback": fallback}
        self.manifest["degradations"].append(item)
        self.bus.publish(EventKind.DEGRADED, f"{component}: {reason} -> {fallback}", stage=stage.value,
                         component=component, reason=reason, fallback=fallback)

    # ---- stages ---------------------------------------------------------------------

    def _ingest(self) -> dict[str, Any]:
        assert self._store
        n_total = 0
        files = []
        for spec in self.cfg.inputs:
            traces = load_traces(spec.path, spec.source)
            n = self._store.upsert_traces(traces)
            n_total += n
            files.append({"path": spec.path, "source": spec.source.value, "n": n})
            self.bus.publish(EventKind.PROGRESS, f"ingested {n} from {Path(spec.path).name}", stage=Stage.INGEST.value, n=n, path=spec.path)
        st = self._store.stats()
        self.manifest["models"]["agent"] = st.agent_models
        return {"n_ingested": n_total, "n_in_store": st.n_traces, "n_tasks": st.n_tasks, "files": files}

    def _precheck(self) -> dict[str, Any]:
        assert self._store
        cfg = PrecheckConfig(step_latency_ms=self.cfg.precheck.step_latency_ms, max_duration_s=self.cfg.precheck.max_duration_s)
        results = run_many(self._store.list(), cfg)
        self._store.put_precheck(results)
        s = self._store.precheck_summary()
        return {"n_checked": s["n_checked"], "n_env_failures": s["n_env_failures"], "n_scorable": s["n_scorable"], "kinds": s["kinds"]}

    def _evalset(self) -> dict[str, Any]:
        assert self._store
        traces = self._store.scorable_traces()
        if self.cfg.evalset.task_ids:
            keep = set(self.cfg.evalset.task_ids)
            traces = [t for t in traces if t.task_id in keep]
        if self.cfg.evalset.limit:
            traces = traces[: self.cfg.evalset.limit]
        self._evalset_ids = [t.trace_id for t in traces]
        (self.run_dir / "evalset.json").write_text(json.dumps(self._evalset_ids, indent=2), encoding="utf-8")
        return {"n_traces": len(traces), "n_tasks": len({t.task_id for t in traces})}

    def _build_panel(self) -> Panel:
        cfg = self.cfg.panel or PanelConfig.mock()
        judges = build_judges(cfg)
        if self.cfg.judge_healthcheck:
            for i, j in enumerate(judges):
                if isinstance(j, OpenAICompatJudge) and not j.healthcheck():
                    self._degrade(Stage.SCORE, f"judge {j.name} ({j.model} @ {j.base_url})", "health check failed", "mock judge")
                    judges[i] = MockJudge(j.name, f"mock-fallback-for-{j.model}", jitter=0.1)
        self.manifest["models"]["judges"] = {j.name: j.model for j in judges}
        return Panel(judges, cfg.dimensions, score_tolerance=cfg.score_tolerance, workers=cfg.workers)

    def _score(self) -> dict[str, Any]:
        assert self._store
        self._panel = self._build_panel()
        ids = self._evalset_ids or [t.trace_id for t in self._store.scorable_traces()]
        traces = [t for t in (self._store.get(i) for i in ids) if t]
        results = []
        n = len(traces)
        for i, t in enumerate(traces, start=1):
            r = self._panel.score(t)
            results.append(r)
            self.bus.publish(EventKind.PROGRESS, f"scored {t.trace_id} ({i}/{n})" + (" disagreement" if r.needs_arbitration else ""),
                             stage=Stage.SCORE.value, i=i, n=n, trace_id=t.trace_id, needs_arbitration=r.needs_arbitration,
                             disagreements=[d.value for d in r.disagreements])
        self._store.put_panel_results(results)
        n_verdicts = sum(len(r.verdicts) for r in results)
        errs = sum(1 for r in results for v in r.verdicts if not v.ok)
        n_dis = sum(1 for r in results if r.needs_arbitration)
        if errs:
            self.bus.publish(EventKind.WARNING, f"{errs} judge call(s) errored", stage=Stage.SCORE.value, n_errors=errs)
        return {"n_traces": len(results), "n_verdicts": n_verdicts, "n_needing_arbitration": n_dis,
                "agreement_rate": ((len(results) - n_dis) / len(results)) if results else None,
                "judge_errors": errs, "judges": self._store.verdict_summary()["judges"]}

    def _arbitrate(self) -> dict[str, Any]:
        assert self._store
        panel = self._panel or self._build_panel()
        jev = None if self.cfg.arbiter.jev == "off" else JevClient(timeout_s=self.cfg.arbiter.jev_timeout_s)
        if jev is not None and not jev.configured:
            self._degrade(Stage.ARBITRATE, "jev", "TYPESAFE_API_KEY not set", "local judge arbitration")
        arb = Arbiter(jev=jev, local_judge=panel.judges[0], audit_judge=panel.judges[-1] if len(panel.judges) > 1 else None,
                      audit_rate=self.cfg.arbiter.audit_rate)
        panels = [p for p in self._store.list_panel_results() if not self._evalset_ids or p.trace_id in set(self._evalset_ids)]
        pairs = [(self._store.get(p.trace_id), p) for p in panels]
        decisions = arb.decide_many([(t, p) for t, p in pairs if t is not None],
                                    on_result=lambda d: self.bus.publish(EventKind.PROGRESS, f"decided {d.trace_id}" + (" (degraded)" if d.any_degraded else ""),
                                                                         stage=Stage.ARBITRATE.value, trace_id=d.trace_id, degraded=d.any_degraded))
        self._store.put_decisions(decisions)
        arbs = [a for d in decisions for a in d.arbitrations]
        by_source: dict[str, int] = {}
        for a in arbs:
            by_source[a.source.value] = by_source.get(a.source.value, 0) + 1
        n_local = by_source.get("local", 0)
        if jev is not None and jev.configured and n_local:
            self._degrade(Stage.ARBITRATE, "jev", f"{n_local} decision(s) fell back", "local judge arbitration")
        self.manifest["models"]["arbiter"] = {"jev": jev.model if jev and jev.configured else None, "local": panel.judges[0].model}
        return {"n_traces": len(decisions), "n_dimensions": len(arbs), "by_source": by_source,
                "n_degraded": sum(1 for a in arbs if a.degraded),
                "n_audited": sum(1 for a in arbs if a.audit_sampled),
                "n_audit_disagreements": sum(1 for a in arbs if a.audit_disagrees)}

    def _cluster(self) -> dict[str, Any]:
        assert self._store
        c = self.cfg.cluster
        emb = HashingEmbedder() if c.embedder == "hash" else OpenAIEmbedder(c.embed_base_url, c.embed_model)
        badcases = []
        for d in self._store.list_decisions():
            t = self._store.get(d.trace_id)
            if t is None:
                continue
            b = build_badcase(t, d, self._store.get_panel_result(d.trace_id))
            if b:
                badcases.append(b)
        try:
            clusters, used = cluster_badcases(badcases, emb, min_cluster_size=c.min_cluster_size, method=c.method)
            emb_name = emb.name
        except Exception as e:  # noqa: BLE001
            self._degrade(Stage.CLUSTER, f"embedder {emb.name}", f"{type(e).__name__}", "hashing embedder")
            emb = HashingEmbedder()
            clusters, used = cluster_badcases(badcases, emb, min_cluster_size=c.min_cluster_size, method=c.method)
            emb_name = emb.name + " (fallback)"
        jev = None if c.jev == "off" else JevClient()
        label_clusters(clusters, badcases, jev)
        run = ClusterRun(n_badcases=len(badcases), n_clusters=sum(1 for x in clusters if x.cluster_id != -1),
                         n_noise=sum(x.size for x in clusters if x.cluster_id == -1), embedder=emb_name, method=used,
                         clusters=clusters, badcases=badcases)
        self._store.put_cluster_run(run)
        self.manifest["models"]["embedder"] = emb_name
        return {"n_badcases": run.n_badcases, "n_clusters": run.n_clusters, "n_noise": run.n_noise, "method": used,
                "labels": {f"#{x.rank}": f"{x.label.value} ({x.size})" for x in clusters if x.cluster_id != -1}}

    def _report(self) -> dict[str, Any]:
        assert self._store
        card = build_card(self._store, run_id=self.cfg.run_id, title=self.cfg.report.title)
        out_dir = Path(self.cfg.report.out_dir) if self.cfg.report.out_dir else self.run_dir / "card"
        files = write_card(card, out_dir, formats=tuple(self.cfg.report.formats))
        return {"files": [str(f) for f in files], "recommendation": card.recommendation,
                "pass_rate": card.quality.pass_rate, "pass_k": card.quality.pass_k, "n_badcases": card.totals.n_badcases}


def run_config(cfg: RunConfig, on_event=None) -> dict[str, Any]:
    orch = Orchestrator(cfg)
    if on_event:
        orch.bus.subscribe(on_event)
    return orch.run()
