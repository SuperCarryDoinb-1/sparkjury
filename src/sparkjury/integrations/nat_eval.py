"""Framework-independent core of the NeMo Agent Toolkit evaluator.

`nat_sparkjury.register` (the NAT plugin under nat/) is a thin wrapper around `SparkJuryEvaluatorCore`,
so the logic here is tested without nvidia-nat installed.
"""

from __future__ import annotations

from typing import Any

from sparkjury.adapters.nat import nat_item_to_trace
from sparkjury.arbiter import Arbiter, JevClient
from sparkjury.judges import Panel, PanelConfig
from sparkjury.models.verdict import ALL_DIMENSIONS, Dimension
from sparkjury.precheck import run as precheck_run


class SparkJuryEvaluatorCore:
    """Scores one NAT eval item with the three-judge panel + arbitration.

    score = mean(final_score / 4) over the evaluated dimensions (0..1, higher is better);
    an environment failure (precheck) scores 0 with the flag kinds in `reasoning`.
    """

    def __init__(self, *, judges: str = "mock", dimensions: list[str] | None = None, jev: str = "auto",
                 audit_rate: float = 0.0, domain: str = "nat"):
        cfg = PanelConfig.mock() if judges == "mock" else PanelConfig.from_toml(judges)
        if dimensions:
            cfg.dimensions = [Dimension(d) for d in dimensions]
        self.panel = Panel.from_config(cfg)
        jev_client = None if jev == "off" else JevClient()
        self.arbiter = Arbiter(jev=jev_client, local_judge=self.panel.judges[0],
                               audit_judge=self.panel.judges[-1] if len(self.panel.judges) > 1 else None, audit_rate=audit_rate)
        self.dimensions = cfg.dimensions or list(ALL_DIMENSIONS)
        self.domain = domain

    def evaluate_item(self, item: Any) -> tuple[float, dict[str, Any]]:
        trace = nat_item_to_trace(item, domain=self.domain)
        pre = precheck_run(trace)
        if pre.is_env_failure:
            return 0.0, {"trace_id": trace.trace_id, "env_failure": True, "kinds": [k.value for k in pre.kinds],
                         "flags": [f.model_dump(mode="json") for f in pre.flags]}
        panel = self.panel.score(trace)
        decision = self.arbiter.decide(trace, panel)
        scores = {a.dimension.value: a.final_score for a in decision.arbitrations}
        valid = [s for s in scores.values() if s is not None]
        score = (sum(valid) / (4.0 * len(valid))) if valid else 0.0
        reasoning = {
            "trace_id": trace.trace_id, "env_failure": False, "scores": scores,
            "outcome_label": decision.outcome_label, "degraded": decision.any_degraded,
            "disagreements": [d.value for d in panel.disagreements],
            "sources": {a.dimension.value: a.source.value for a in decision.arbitrations},
            "judges": {v.judge: v.model for v in panel.verdicts},
            "rationales": {a.dimension.value: a.rationale for a in decision.arbitrations},
            "n_steps": trace.metrics.n_steps, "n_tool_calls": trace.metrics.n_tool_calls,
        }
        return round(score, 4), reasoning

    def evaluate_items(self, items: list[Any]) -> tuple[float, list[dict[str, Any]]]:
        outs = []
        for it in items:
            s, r = self.evaluate_item(it)
            outs.append({"id": _item_id(it), "score": s, "reasoning": r})
        avg = (sum(o["score"] for o in outs) / len(outs)) if outs else 0.0
        return round(avg, 4), outs


def _item_id(it: Any) -> Any:
    if isinstance(it, dict):
        return it.get("id")
    return getattr(it, "id", None)
