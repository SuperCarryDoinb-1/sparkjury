"""Register the SparkJury evaluator with NeMo Agent Toolkit.

YAML usage:

    eval:
      evaluators:
        sparkjury:
          _type: sparkjury
          judges: deploy/judges.toml     # or "mock"
          dimensions: [outcome, tool_use, efficiency, safety]
          jev: auto                      # auto | off
          audit_rate: 0.05

Output per item: score in [0, 1] (mean of final 0-4 scores / 4) and a reasoning dict with per-dimension
scores, outcome label, which decisions were arbitrated by Jev / locally, and the judges' rationales.
"""

from __future__ import annotations

from pydantic import Field

from nat.builder.builder import EvalBuilder
from nat.builder.evaluator import EvaluatorInfo
from nat.cli.register_workflow import register_evaluator
from nat.data_models.evaluator import EvaluatorBaseConfig


class SparkJuryEvaluatorConfig(EvaluatorBaseConfig, name="sparkjury"):
    """Three-judge panel (three model families) + arbitration, from the SparkJury harness."""

    judges: str = Field(default="mock", description="'mock' or path to a SparkJury panel TOML")
    dimensions: list[str] = Field(default_factory=lambda: ["outcome", "tool_use", "efficiency", "safety"])
    jev: str = Field(default="auto", description="auto: use TYPESAFE_API_KEY if set; off: local arbitration only")
    audit_rate: float = Field(default=0.0, description="fraction of items re-scored by the audit judge")
    domain: str = Field(default="nat")


@register_evaluator(config_type=SparkJuryEvaluatorConfig)
async def register_sparkjury_evaluator(config: SparkJuryEvaluatorConfig, builder: EvalBuilder):
    from nat.eval.evaluator.evaluator_model import EvalInput, EvalOutput, EvalOutputItem

    from sparkjury.integrations.nat_eval import SparkJuryEvaluatorCore

    core = SparkJuryEvaluatorCore(judges=config.judges, dimensions=config.dimensions, jev=config.jev,
                                  audit_rate=config.audit_rate, domain=config.domain)

    async def evaluate_fn(eval_input: EvalInput) -> EvalOutput:
        items = []
        for item in eval_input.eval_input_items:
            score, reasoning = core.evaluate_item(item)
            items.append(EvalOutputItem(id=item.id, score=score, reasoning=reasoning))
        avg = (sum(i.score for i in items) / len(items)) if items else 0.0
        return EvalOutput(average_score=avg, eval_output_items=items)

    yield EvaluatorInfo(config=config, evaluate_fn=evaluate_fn,
                        description="SparkJury: three-judge panel across model families with Jev/local arbitration")
