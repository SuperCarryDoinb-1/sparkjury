"""Rule-based precheck (M2).

Environment-caused failures are labelled here, in the harness, by deterministic
rules. No model is involved: SWE-bench and Anthropic's eval write-ups both do it
this way so that judges never have to guess whether a failure was the agent's.

Each rule is a function `(trace, cfg) -> list[PrecheckFlag]`. A trace with any
flag is excluded from scoring and reported separately as an environment issue.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable

from sparkjury.models.precheck import PrecheckFlag, PrecheckKind, PrecheckResult
from sparkjury.models.trace import Role, Step, Trace

# ---- configuration ---------------------------------------------------------


@dataclass
class PrecheckConfig:
    infra_terminations: set[str] = field(
        default_factory=lambda: {"infrastructure_error", "unexpected_error", "user_error"}
    )
    timeout_terminations: set[str] = field(default_factory=lambda: {"timeout"})
    context_terminations: set[str] = field(default_factory=lambda: {"context_window_exceeded"})
    step_latency_ms: float | None = 120_000.0     # single step slower than this → timeout
    max_duration_s: float | None = None           # whole run longer than this → timeout (off by default)
    tool_unavailable_min_consecutive: int = 2
    tool_unavailable_pattern: str = (
        r"(unavailable|service (is )?down|5\d\d\b|internal server error|bad gateway|gateway time-?out|"
        r"connection (reset|refused|error|aborted)|timed? ?out|rate.?limit|too many requests|econn|network error)"
    )
    permission_pattern: str = r"(permission denied|unauthori[sz]ed|forbidden|\b403\b|access denied|invalid api key|missing credentials)"
    # Agent-side authentication failures are the agent's fault, not the environment's.
    permission_exclude_pattern: str = r"(not authenticated|authenticate (the )?(user|first)|verify (the )?user|login required|please (log|sign) ?in)"
    user_repeat_min: int = 3                      # identical user message repeated this many times → broken simulator
    user_empty_min: int = 1                       # empty user messages (no text, no tool call) → broken simulator


RuleFn = Callable[[Trace, PrecheckConfig], list[PrecheckFlag]]


# ---- rules -----------------------------------------------------------------


def rule_empty_trace(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    if not trace.steps:
        return [PrecheckFlag(kind=PrecheckKind.EMPTY_TRACE, note="trace has no steps")]
    if not any(s.role == Role.ASSISTANT for s in trace.steps):
        return [PrecheckFlag(kind=PrecheckKind.EMPTY_TRACE, note="trace has no assistant step")]
    return []


def rule_infra_error(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    tr = (trace.outcome.termination_reason or "").lower()
    if tr in cfg.infra_terminations:
        return [PrecheckFlag(kind=PrecheckKind.INFRA_ERROR, note=f"termination_reason={tr}")]
    return []


def rule_timeout(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    flags: list[PrecheckFlag] = []
    tr = (trace.outcome.termination_reason or "").lower()
    if tr in cfg.timeout_terminations:
        flags.append(PrecheckFlag(kind=PrecheckKind.TIMEOUT, note=f"termination_reason={tr}"))
    if cfg.step_latency_ms is not None:
        for s in trace.steps:
            if s.latency_ms is not None and s.latency_ms > cfg.step_latency_ms:
                flags.append(
                    PrecheckFlag(
                        kind=PrecheckKind.TIMEOUT,
                        evidence_step_idx=s.idx,
                        note=f"step latency {s.latency_ms:.0f} ms > {cfg.step_latency_ms:.0f} ms",
                    )
                )
                break
    if cfg.max_duration_s is not None and trace.metrics.duration_s is not None:
        if trace.metrics.duration_s > cfg.max_duration_s:
            flags.append(
                PrecheckFlag(kind=PrecheckKind.TIMEOUT, note=f"duration {trace.metrics.duration_s:.0f}s > {cfg.max_duration_s:.0f}s")
            )
    return flags


def rule_context_overflow(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    tr = (trace.outcome.termination_reason or "").lower()
    if tr in cfg.context_terminations:
        return [PrecheckFlag(kind=PrecheckKind.CONTEXT_OVERFLOW, note=f"termination_reason={tr}")]
    return []


def rule_tool_unavailable(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    """Same tool failing >= N times in a row with an infrastructure-looking error."""
    pat = re.compile(cfg.tool_unavailable_pattern, re.I)
    name_by_call = {tc.call_id: tc.name for _, tc in trace.iter_tool_calls() if tc.call_id}
    run_name: str | None = None
    run_len = 0
    run_start: int | None = None
    run_text = ""
    best: tuple[int, int | None, str, str] | None = None  # (len, start, name, first error text)

    def _close() -> None:
        nonlocal best
        if run_name is not None and run_len >= cfg.tool_unavailable_min_consecutive:
            if best is None or run_len > best[0]:
                best = (run_len, run_start, run_name, run_text)

    for s in trace.steps:
        if s.role != Role.TOOL or s.tool_result is None:
            continue
        name = name_by_call.get(s.tool_result.call_id) or s.meta.get("tool_name") or "?"
        text = s.tool_result.content or ""
        infra_err = s.tool_result.is_error and bool(pat.search(text))
        if infra_err and name == run_name:
            run_len += 1
        else:
            _close()
            if infra_err:
                run_name, run_len, run_start, run_text = name, 1, s.idx, text
            else:
                run_name, run_len, run_start, run_text = None, 0, None, ""
    _close()
    if best is None:
        return []
    n, start, name, text = best
    return [
        PrecheckFlag(
            kind=PrecheckKind.TOOL_UNAVAILABLE,
            evidence_step_idx=start,
            note=f"tool '{name}' failed {n}x consecutively: {text[:80]}",
        )
    ]


def rule_permission_denied(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    pat = re.compile(cfg.permission_pattern, re.I)
    excl = re.compile(cfg.permission_exclude_pattern, re.I)
    for s in trace.steps:
        if s.role != Role.TOOL or s.tool_result is None or not s.tool_result.is_error:
            continue
        text = s.tool_result.content or ""
        if pat.search(text) and not excl.search(text):
            return [
                PrecheckFlag(
                    kind=PrecheckKind.PERMISSION_DENIED,
                    evidence_step_idx=s.idx,
                    note=f"tool error: {text[:80]}",
                )
            ]
    return []


def rule_user_sim_broken(trace: Trace, cfg: PrecheckConfig) -> list[PrecheckFlag]:
    flags: list[PrecheckFlag] = []
    users: list[Step] = [s for s in trace.steps if s.role == Role.USER]
    empties = [s for s in users if not (s.content or "").strip() and not s.tool_calls]
    if cfg.user_empty_min and len(empties) >= cfg.user_empty_min:
        flags.append(
            PrecheckFlag(
                kind=PrecheckKind.USER_SIM_BROKEN,
                evidence_step_idx=empties[0].idx,
                note=f"{len(empties)} empty user message(s)",
            )
        )
    counts = Counter((s.content or "").strip() for s in users if (s.content or "").strip())
    for text, n in counts.items():
        if n >= cfg.user_repeat_min:
            first = next(s.idx for s in users if (s.content or "").strip() == text)
            flags.append(
                PrecheckFlag(
                    kind=PrecheckKind.USER_SIM_BROKEN,
                    evidence_step_idx=first,
                    note=f"user message repeated {n}x: {text[:60]}",
                )
            )
            break
    return flags


RULES: list[RuleFn] = [
    rule_empty_trace,
    rule_infra_error,
    rule_timeout,
    rule_context_overflow,
    rule_tool_unavailable,
    rule_permission_denied,
    rule_user_sim_broken,
]


# ---- entry points ----------------------------------------------------------


def run(trace: Trace, cfg: PrecheckConfig | None = None, rules: Iterable[RuleFn] | None = None) -> PrecheckResult:
    cfg = cfg or PrecheckConfig()
    flags: list[PrecheckFlag] = []
    for rule in rules or RULES:
        flags.extend(rule(trace, cfg))
    return PrecheckResult(trace_id=trace.trace_id, flags=flags)


def run_many(traces: Iterable[Trace], cfg: PrecheckConfig | None = None) -> list[PrecheckResult]:
    cfg = cfg or PrecheckConfig()
    return [run(t, cfg) for t in traces]
