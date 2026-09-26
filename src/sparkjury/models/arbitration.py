"""Arbitration (M4): the final per-dimension decision for a trace."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from sparkjury.models.verdict import Dimension


class DecisionSource(StrEnum):
    PANEL = "panel"                  # judges agreed; median / majority taken
    JEV = "jev"                      # disagreement resolved by cloud Jev
    LOCAL = "local"                  # Jev unreachable; local judge arbitrated (degraded)
    PANEL_FALLBACK = "panel_fallback"  # everything failed; panel median used (degraded)


class Arbitration(BaseModel):
    trace_id: str
    dimension: Dimension
    final_score: int | None = None
    final_label: str | None = None          # outcome only
    source: DecisionSource
    degraded: bool = False
    panel_scores: dict[str, int | None] = Field(default_factory=dict)
    panel_labels: dict[str, str | None] = Field(default_factory=dict)
    jev_confidence: float | None = None
    jev_raw_score: float | None = None      # Jev's fractional position on the scale
    rationale: str = ""
    latency_ms: float | None = None
    error: str | None = None
    # 5% audit: an independent strong judge re-scores this dimension
    audit_sampled: bool = False
    audit_score: int | None = None
    audit_label: str | None = None
    audit_disagrees: bool | None = None


class TraceDecision(BaseModel):
    trace_id: str
    arbitrations: list[Arbitration] = Field(default_factory=list)

    def get(self, dimension: Dimension) -> Arbitration | None:
        return next((a for a in self.arbitrations if a.dimension == dimension), None)

    @property
    def scores(self) -> dict[str, int | None]:
        return {a.dimension.value: a.final_score for a in self.arbitrations}

    @property
    def outcome_label(self) -> str | None:
        a = self.get(Dimension.OUTCOME)
        return a.final_label if a else None

    @property
    def any_degraded(self) -> bool:
        return any(a.degraded for a in self.arbitrations)
