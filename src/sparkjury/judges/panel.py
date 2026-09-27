"""Three-judge panel: fan out per (trace, dimension), then decide agreement."""

from __future__ import annotations

import os
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

from sparkjury.judges.client import Judge, MockJudge, OpenAICompatJudge
from sparkjury.models.trace import Trace
from sparkjury.models.verdict import ALL_DIMENSIONS, Dimension, DimensionAgreement, PanelResult, Verdict

# ---- configuration -----------------------------------------------------------


class JudgeSpec(BaseModel):
    name: str
    kind: str = "mock"                 # mock | openai
    model: str = "mock-heuristic-v1"
    base_url: str | None = None
    api_key_env: str | None = None     # env var holding the key; never store keys in config
    timeout_s: float = 60.0
    max_retries: int = 2
    jitter: float = 0.0                # mock only
    max_tokens: int = 1200             # room for models that add prose before the JSON
    temperature: float = 0.0
    extra_body: dict = Field(default_factory=dict)  # e.g. {"chat_template_kwargs": {"enable_thinking": false}}


class PanelConfig(BaseModel):
    judges: list[JudgeSpec]
    dimensions: list[Dimension] = Field(default_factory=lambda: list(ALL_DIMENSIONS))
    score_tolerance: int = 1           # max-min <= tolerance counts as agreement on score dimensions
    workers: int = 4
    # Prompt budget. Real tau2 tool results run to ~1000 characters a step, so the old
    # per-step cut of 400 dropped about two thirds of the evidence before a judge saw it;
    # the total budget keeps the biggest trace inside a 16k-context judge (Nemotron).
    transcript_width: int = 4000       # per-step character cap
    transcript_max_chars: int | None = 45000   # whole-transcript budget; None disables it
    include_gold: bool = False         # hand judges the benchmark's own outcome (off: it is the answer)

    @classmethod
    def mock(cls) -> "PanelConfig":
        """Three heuristic judges with different jitter so some disagreements occur."""
        return cls(judges=[
            JudgeSpec(name="judge_a", model="mock-qwen", jitter=0.0),
            JudgeSpec(name="judge_b", model="mock-gemma", jitter=0.25),
            JudgeSpec(name="judge_c", model="mock-step", jitter=0.15),
        ])

    @classmethod
    def from_toml(cls, path: str | Path) -> "PanelConfig":
        with Path(path).open("rb") as f:
            data = tomllib.load(f)
        return cls.model_validate(data.get("panel", data))


def build_judges(cfg: PanelConfig) -> list[Judge]:
    judges: list[Judge] = []
    for spec in cfg.judges:
        if spec.kind == "mock":
            judges.append(MockJudge(spec.name, spec.model, jitter=spec.jitter))
        elif spec.kind == "openai":
            if not spec.base_url:
                raise ValueError(f"judge {spec.name}: base_url required for kind=openai")
            key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
            judges.append(OpenAICompatJudge(spec.name, spec.model, spec.base_url, key,
                                            timeout_s=spec.timeout_s, max_retries=spec.max_retries,
                                            temperature=spec.temperature, max_tokens=spec.max_tokens,
                                            extra_body=spec.extra_body,
                                            transcript_width=cfg.transcript_width,
                                            transcript_max_chars=cfg.transcript_max_chars,
                                            include_gold=cfg.include_gold))
        else:
            raise ValueError(f"unknown judge kind {spec.kind!r}")
    return judges


# ---- agreement ---------------------------------------------------------------


def decide_agreement(verdicts: Sequence[Verdict], dimension: Dimension, tolerance: int = 1) -> DimensionAgreement:
    vs = [v for v in verdicts if v.dimension == dimension]
    scores = {v.judge: v.score for v in vs}
    labels = {v.judge: v.label for v in vs}
    ok = [v for v in vs if v.ok]
    if len(ok) < 2:
        return DimensionAgreement(dimension=dimension, scores=scores, labels=labels, agreed=False,
                                  reason=f"only {len(ok)} usable verdict(s)")
    if len(ok) < len(vs):
        # a judge failed: the remaining votes must be unanimous to count as agreement
        pass
    if dimension == Dimension.OUTCOME:
        ls = {v.label for v in ok}
        agreed = len(ls) == 1 and len(ok) == len(vs)
        return DimensionAgreement(dimension=dimension, scores=scores, labels=labels, agreed=agreed,
                                  reason="labels unanimous" if agreed else f"labels split {sorted(l or 'none' for l in ls)}")
    ss = [v.score for v in ok if v.score is not None]
    spread = max(ss) - min(ss)
    agreed = spread <= tolerance and len(ok) == len(vs)
    return DimensionAgreement(dimension=dimension, scores=scores, labels=labels, agreed=agreed,
                              reason=f"score spread {spread} <= {tolerance}" if agreed else f"score spread {spread} > {tolerance}")


# ---- panel -------------------------------------------------------------------


class Panel:
    def __init__(self, judges: Sequence[Judge], dimensions: Sequence[Dimension] | None = None,
                 *, score_tolerance: int = 1, workers: int = 4):
        if len(judges) < 2:
            raise ValueError("a panel needs at least two judges")
        self.judges = list(judges)
        self.dimensions = list(dimensions or ALL_DIMENSIONS)
        self.score_tolerance = score_tolerance
        self.workers = max(1, workers)

    @classmethod
    def from_config(cls, cfg: PanelConfig) -> "Panel":
        return cls(build_judges(cfg), cfg.dimensions, score_tolerance=cfg.score_tolerance, workers=cfg.workers)

    def arbiter_judges(self) -> tuple[Judge | None, Judge | None]:
        """(local arbiter, audit judge) for this panel — one place, so no caller picks wrong.

        The audit judge must be a real model. A MockJudge is a deterministic heuristic over
        the trace, so a mock "auditing" real judges yields no independent signal while still
        writing `audit_sampled` rows that read like a passing audit; on the 2026-09-26 node
        run the auditor was exactly the judge that had degraded to mock. Fewer than two real
        judges means no independent scrutineer exists, so the audit is skipped (None) and the
        caller can see it in `stages.ARBITRATE.n_audited` and `models.arbiter.audit`.

        The local arbiter prefers a real judge but still falls back to a mock so the offline
        demo keeps arbitrating; that path is always marked degraded at the decision level.
        """
        real = [j for j in self.judges if not isinstance(j, MockJudge)]
        local = real[0] if real else (self.judges[0] if self.judges else None)
        audit = real[-1] if len(real) > 1 else None
        return local, audit

    def score(self, trace: Trace) -> PanelResult:
        jobs = [(j, d) for d in self.dimensions for j in self.judges]
        if self.workers == 1:
            verdicts = [j.score(trace, d) for j, d in jobs]
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                verdicts = list(ex.map(lambda jd: jd[0].score(trace, jd[1]), jobs))
        agreement = [decide_agreement(verdicts, d, self.score_tolerance) for d in self.dimensions]
        return PanelResult(trace_id=trace.trace_id, verdicts=verdicts, agreement=agreement)

    def score_many(self, traces: Iterable[Trace], on_result=None) -> list[PanelResult]:
        out: list[PanelResult] = []
        for t in traces:
            r = self.score(t)
            out.append(r)
            if on_result:
                on_result(r)
        return out
