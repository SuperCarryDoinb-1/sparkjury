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

    def transcript(self, width: int = 160, include_system: bool = False) -> str:
        lines = [
            s.short(width)
            for s in self.steps
            if include_system or s.role != Role.SYSTEM
        ]
        return "\n".join(lines)

    @property
    def is_success(self) -> bool | None:
        return self.outcome.success
