"""Canonical trace model.

Every downstream module (precheck, judges, cluster, report) consumes this model
and never touches raw benchmark or OTel JSON. The shape follows the OTel GenAI
semantic conventions: an agent invocation is a sequence of steps, each step is a
chat turn or a tool execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterator

from pydantic import BaseModel, Field


class TraceSource(StrEnum):
    TAU2 = "tau2"
    OTEL = "otel"
    CUSTOM = "custom"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    call_id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requestor: str = "assistant"  # tau2 lets the simulated user call tools too


class ToolResult(BaseModel):
    call_id: str = ""
    content: str | None = None
    is_error: bool = False


class Step(BaseModel):
    idx: int
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_result: ToolResult | None = None
    timestamp: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def short(self, width: int = 160) -> str:
        """One-line rendering used by transcripts and evidence cards."""
        if self.role == Role.TOOL and self.tool_result is not None:
            tag = "TOOL_ERR" if self.tool_result.is_error else "TOOL"
            body = (self.tool_result.content or "").replace("\n", " ")
            return f"[{self.idx}] {tag}({self.tool_result.call_id}): {body[:width]}"
        parts: list[str] = []
        if self.content:
            parts.append(self.content.replace("\n", " ")[:width])
        for tc in self.tool_calls:
            parts.append(f"-> {tc.name}({_fmt_args(tc.arguments, width)})")
        return f"[{self.idx}] {self.role.value.upper()}: " + " ".join(parts)


def _fmt_args(args: dict[str, Any], width: int) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= width else s[: width - 1] + "…"


def _fit_with_gaps(steps: list[Step], width: int, max_chars: int) -> str:
    """Last resort for a huge trace: keep both ends, replace the middle with a marker.

    The marker names how many steps were dropped so a reader never mistakes a gappy
    transcript for a complete one.
    """
    lines = [s.short(width) for s in steps]
    n = len(lines)
    for drop in range(1, n):
        head = (n - drop) // 2
        tail = n - drop - head
        marker = f"[... {drop} step(s) omitted to fit the prompt budget ...]"
        kept = lines[:head] + [marker] + (lines[n - tail:] if tail else [])
        if len("\n".join(kept)) <= max_chars:
            return "\n".join(kept)
    return f"[transcript omitted: {n} steps exceed the prompt budget]"


class Outcome(BaseModel):
    """Gold-standard outcome when the source provides one (tau2 db_check)."""

    success: bool | None = None
    reward: float | None = None
    termination_reason: str | None = None
    gold: dict[str, Any] = Field(default_factory=dict)


class TraceMetrics(BaseModel):
    n_steps: int = 0
    n_assistant_turns: int = 0
    n_user_turns: int = 0
    n_tool_calls: int = 0
    n_tool_errors: int = 0
    duration_s: float | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class Trace(BaseModel):
    trace_id: str
    source: TraceSource
    domain: str = "unknown"
    task_id: str
    trial: int = 0
    agent_model: str | None = None
    steps: list[Step] = Field(default_factory=list)
    outcome: Outcome = Field(default_factory=Outcome)
    metrics: TraceMetrics = Field(default_factory=TraceMetrics)
    raw_ref: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    # ---- derived helpers -------------------------------------------------

    def compute_metrics(self) -> TraceMetrics:
        m = TraceMetrics(
            n_steps=len(self.steps),
            duration_s=self.metrics.duration_s,
            cost_usd=self.metrics.cost_usd,
        )
        tin = tout = 0
        seen_tokens = False
        for s in self.steps:
            if s.role == Role.ASSISTANT:
                m.n_assistant_turns += 1
            elif s.role == Role.USER:
                m.n_user_turns += 1
            m.n_tool_calls += len(s.tool_calls)
            if s.tool_result is not None and s.tool_result.is_error:
                m.n_tool_errors += 1
            if s.tokens_in is not None or s.tokens_out is not None:
                seen_tokens = True
                tin += s.tokens_in or 0
                tout += s.tokens_out or 0
        if seen_tokens:
            m.tokens_in, m.tokens_out = tin, tout
        self.metrics = m
        return m

    def iter_tool_calls(self) -> Iterator[tuple[Step, ToolCall]]:
        for s in self.steps:
            for tc in s.tool_calls:
                yield s, tc

    def tool_result_for(self, call_id: str) -> ToolResult | None:
        for s in self.steps:
            if s.tool_result is not None and s.tool_result.call_id == call_id:
                return s.tool_result
        return None

    def transcript(self, width: int = 160, include_system: bool = False, *,
                   max_chars: int | None = None, min_width: int = 200) -> str:
        """Render the steps one line each, optionally inside a character budget.

        `width` caps every step body. When `max_chars` is set the whole render must fit
        it: the per-step width shrinks (binary search) until it does, so every step keeps
        its `[n]` line and the `evidence_steps` a judge cites stay valid. Real tau2 tool
        results run to ~1000 characters a step, so a fixed 400-character cut throws away
        about two thirds of the evidence; the budget keeps the prompt inside a 16k-context
        judge instead. Only if even `min_width` overflows are middle steps dropped.
        """
        steps = [s for s in self.steps if include_system or s.role != Role.SYSTEM]
        if max_chars is None or max_chars <= 0 or not steps:
            return "\n".join(s.short(width) for s in steps)
        lo = max(1, min(min_width, width))
        hi = max(width, lo)

        def render(w: int) -> str:
            return "\n".join(s.short(w) for s in steps)

        text = render(hi)
        if len(text) <= max_chars:
            return text
        if len(render(lo)) > max_chars:
            return _fit_with_gaps(steps, lo, max_chars)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(render(mid)) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        return render(lo)

    @property
    def is_success(self) -> bool | None:
        return self.outcome.success
