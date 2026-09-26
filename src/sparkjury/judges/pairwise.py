"""Pairwise before/after comparison with position-swap.

Position bias is real (GPT-4 agreed with itself only 65% of the time when A/B were swapped),
so every pair is judged twice with the order swapped; a result only counts as consistent when
both passes name the same winner. Used by `sparkjury regress --pairwise`.
"""

from __future__ import annotations

import re
from typing import Protocol

from sparkjury.judges import heuristics
from sparkjury.models.trace import Trace
from sparkjury.models.verdict import ALL_DIMENSIONS

PAIRWISE_SYSTEM = """You compare two transcripts (A and B) of a customer-service agent handling the SAME task.
Decide which transcript handled the task better overall, weighing: goal achieved, correct tool use,
no destructive action without confirmation, no fabricated facts, fewer wasted steps.
Answer with exactly one word on the first line: A, B or TIE. Then one sentence of rationale."""


class PairwiseJudge(Protocol):
    name: str

    def compare(self, a: Trace, b: Trace) -> tuple[str, str]:
        """Return ("A" | "B" | "TIE", rationale)."""
        ...


class MockPairwiseJudge:
    """Sums the heuristic scores over the four dimensions; deterministic, order-invariant."""

    name = "mock-pairwise"

    def compare(self, a: Trace, b: Trace) -> tuple[str, str]:
        sa = sum(heuristics.evaluate(a, d).score for d in ALL_DIMENSIONS)
        sb = sum(heuristics.evaluate(b, d).score for d in ALL_DIMENSIONS)
        if sa > sb:
            return "A", f"A scores {sa} vs B {sb} on the four rubric dimensions"
        if sb > sa:
            return "B", f"B scores {sb} vs A {sa} on the four rubric dimensions"
        return "TIE", f"both score {sa}"


class OpenAIPairwiseJudge:
    def __init__(self, name: str, model: str, base_url: str, api_key: str | None = None, *, timeout_s: float = 60.0, transcript_width: int = 300):
        from openai import OpenAI

        self.name, self.model, self.transcript_width = name, model, transcript_width
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout_s, max_retries=2)

    def compare(self, a: Trace, b: Trace) -> tuple[str, str]:
        user = (f"Task: {a.task_id}\n\n=== Transcript A ===\n{a.transcript(self.transcript_width)}\n\n"
                f"=== Transcript B ===\n{b.transcript(self.transcript_width)}\n")
        resp = self._client.chat.completions.create(
            model=self.model, temperature=0.0, max_tokens=200,
            messages=[{"role": "system", "content": PAIRWISE_SYSTEM}, {"role": "user", "content": user}],
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.match(r"\s*(A|B|TIE)\b", text, re.I)
        winner = m.group(1).upper() if m else "TIE"
        return winner, text[:300]


def compare_with_swap(judge: PairwiseJudge, before: Trace, after: Trace) -> tuple[str, bool, str]:
    """Judge (before=A, after=B) and (after=A, before=B); map both to before/after; check consistency."""
    w1, r1 = judge.compare(before, after)      # A=before, B=after
    w2, r2 = judge.compare(after, before)      # A=after,  B=before
    m1 = {"A": "before", "B": "after", "TIE": "tie"}[w1]
    m2 = {"A": "after", "B": "before", "TIE": "tie"}[w2]
    consistent = m1 == m2
    winner = m1 if consistent else "tie"
    return winner, consistent, (r1 if consistent else f"inconsistent across order swap: [{r1}] vs [{r2}]")
