"""Run configuration for the harness (TOML file or built-in demo)."""

from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from sparkjury.judges.panel import PanelConfig
from sparkjury.models.trace import TraceSource

PKG_ROOT = Path(__file__).resolve().parents[3]   # repo root (…/sparkjury)


class Stage(StrEnum):
    INGEST = "INGEST"
    PRECHECK = "PRECHECK"
    EVALSET = "EVALSET"
    SCORE = "SCORE"
    ARBITRATE = "ARBITRATE"
    CLUSTER = "CLUSTER"
    REPORT = "REPORT"


ALL_STAGES: list[Stage] = list(Stage)


class InputSpec(BaseModel):
    path: str
    source: TraceSource = TraceSource.TAU2


class PrecheckSettings(BaseModel):
    step_latency_ms: float | None = 120_000.0
    max_duration_s: float | None = None


class EvalsetSettings(BaseModel):
    limit: int | None = None          # cap the number of scorable traces (demo speed)
    task_ids: list[str] | None = None  # restrict to these tasks


class ArbiterSettings(BaseModel):
    jev: str = "auto"                 # auto | off
    jev_timeout_s: float = 5.0
    audit_rate: float = 0.05


class ClusterSettings(BaseModel):
    embedder: str = "hash"            # hash | openai
    embed_base_url: str = "http://127.0.0.1:8003/v1"
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    method: str = "auto"
    min_cluster_size: int = 3
    jev: str = "auto"


class ReportSettings(BaseModel):
    out_dir: str | None = None        # default runs/<run_id>/card
    title: str = "SparkJury evidence card"
    formats: list[str] = Field(default_factory=lambda: ["json", "md", "html"])


class RunConfig(BaseModel):
    run_id: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S"))
    db: str = "runs/sparkjury.db"
    runs_dir: str = "runs"
    inputs: list[InputSpec] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=lambda: list(ALL_STAGES))
    reset_db: bool = False            # delete the db before ingest (fresh run)
    precheck: PrecheckSettings = Field(default_factory=PrecheckSettings)
    evalset: EvalsetSettings = Field(default_factory=EvalsetSettings)
    panel: PanelConfig | None = None  # None -> mock panel
    judge_healthcheck: bool = True    # probe LLM judges before scoring; swap unreachable ones for mock
    arbiter: ArbiterSettings = Field(default_factory=ArbiterSettings)
    cluster: ClusterSettings = Field(default_factory=ClusterSettings)
    report: ReportSettings = Field(default_factory=ReportSettings)

    @property
    def run_dir(self) -> Path:
        return Path(self.runs_dir) / self.run_id

    @classmethod
    def from_toml(cls, path: str | Path) -> "RunConfig":
        with Path(path).open("rb") as f:
            data = tomllib.load(f)
        if "panel" in data and data["panel"] is not None:
            data["panel"] = PanelConfig.model_validate(data["panel"])
        return cls.model_validate(data)

    @classmethod
    def demo(cls, run_id: str | None = None, db: str | None = None) -> "RunConfig":
        """Offline demo: bundled samples, mock judges, hashing embedder, Jev only if a key is present."""
        samples = PKG_ROOT / "data" / "samples"
        cfg = cls(
            inputs=[InputSpec(path=str(samples / "tau2_retail_sample.json"), source=TraceSource.TAU2),
                    InputSpec(path=str(samples / "otel_sample.json"), source=TraceSource.OTEL)],
            reset_db=True,
            cluster=ClusterSettings(min_cluster_size=2),
            report=ReportSettings(title="SparkJury demo: retail agent, 4 tasks x 3 trials"),
        )
        if run_id:
            cfg.run_id = run_id
        cfg.db = db or f"runs/{cfg.run_id}/sparkjury.db"
        return cfg
