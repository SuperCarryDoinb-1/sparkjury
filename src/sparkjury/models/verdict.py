"""Judge verdicts (M3) and panel agreement."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Dimension(StrEnum):
    OUTCOME = "outcome"        # did the agent achieve the task goal (vs gold final state)
    TOOL_USE = "tool_use"      # right tool, right arguments, nothing missing
    EFFICIENCY = "efficiency"  # redundant steps vs a reasonable shortest path
    SAFETY = "safety"          # destructive actions without explicit user confirmation


ALL_DIMENSIONS: list[Dimension] = [Dimension.OUTCOME, Dimension.TOOL_USE, Dimension.EFFICIENCY, Dimension.SAFETY]

SCORE_MIN, SCORE_MAX = 0, 4


class Verdict(BaseModel):
    trace_id: str
    judge: str                       # panel slot name, e.g. "judge_a"
    model: str                       # model id actually used
    dimension: Dimension
    score: int | None = None         # 0..4, None when the judge failed
    label: str | None = None         # "pass" | "fail" for outcome, else None
    confidence: float | None = None  # 0..1
    evidence_steps: list[int] = Field(default_factory=list)
    rationale: str = ""
    latency_ms: float | None = None
    error: str | None = None         # set when the backend failed or returned garbage
    raw: str | None = None           # raw model text, kept for audit

    @property
    def ok(self) -> bool:
        return self.error is None and self.score is not None


class DimensionAgreement(BaseModel):
    dimension: Dimension
    scores: dict[str, int | None]        # judge -> score
    labels: dict[str, str | None]        # judge -> label
    agreed: bool
    reason: str = ""


class PanelResult(BaseModel):
    trace_id: str
    verdicts: list[Verdict] = Field(default_factory=list)
    agreement: list[DimensionAgreement] = Field(default_factory=list)

    @property
    def disagreements(self) -> list[Dimension]:
        return [a.dimension for a in self.agreement if not a.agreed]

    @property
    def needs_arbitration(self) -> bool:
        return bool(self.disagreements)

    def verdicts_for(self, dimension: Dimension) -> list[Verdict]:
        return [v for v in self.verdicts if v.dimension == dimension]
