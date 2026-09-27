"""tau2-bench (a.k.a. tau3-bench) Results JSON → Trace.

Input is the file written by `tau2 run` under data/simulations/. Top level:

    {"info": {...}, "tasks": [...], "simulations": [SimulationRun, ...]}

Each SimulationRun has `messages` (half-duplex) or `ticks` (full-duplex). Both
are handled; ticks are flattened in tick order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sparkjury.models.trace import (
    Outcome,
    Role,
    Step,
    ToolCall,
    ToolResult,
    Trace,
    TraceMetrics,
    TraceSource,
)


def load_tau2(path: str | Path) -> list[Trace]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return parse_tau2(data, raw_ref=str(path))


def parse_tau2(data: dict[str, Any], raw_ref: str | None = None) -> list[Trace]:
    info = data.get("info") or {}
    agent_model = _dig(info, "agent_info", "llm") or _dig(info, "agent_info", "llm_name")
    domain = (
        _dig(info, "environment_info", "domain_name")
        or _dig(info, "environment_info", "domain")
        or "unknown"
    )
    traces: list[Trace] = []
    requirements = _task_requirements(data.get("tasks") or [])
    for i, sim in enumerate(data.get("simulations") or []):
        traces.append(
            _sim_to_trace(sim, i, agent_model, domain, raw_ref,
                          requirements.get(str(sim.get("task_id"))))
        )
    return traces


def _task_requirements(tasks: list[dict[str, Any]]) -> dict[str, str]:
    """task id -> the requirement the task was built around, for the judge prompt.

    Only the user's own statement of what they came for is taken. The reference calls sit next
    to it under `evaluation_criteria.actions` and stay out: they name the exact calls a passing
    run makes, so a judge holding them would diff against the answer key instead of judging the
    transcript. The recorded reward is stripped for the same reason.
    """
    out: dict[str, str] = {}
    for t in tasks:
        text = _dig(t, "user_scenario", "instructions", "reason_for_call")
        if isinstance(text, str) and text.strip():
            out[str(t.get("id"))] = text.strip()
    return out


def _sim_to_trace(
    sim: dict[str, Any],
    index: int,
    agent_model: str | None,
    domain: str,
    raw_ref: str | None,
    task_requirement: str | None = None,
) -> Trace:
    messages = sim.get("messages")
    if not messages and sim.get("ticks"):
        messages = _flatten_ticks(sim["ticks"])
    steps = _messages_to_steps(messages or [])

    reward_info = sim.get("reward_info") or {}
    reward = reward_info.get("reward")
    outcome = Outcome(
        success=(reward >= 1.0) if isinstance(reward, (int, float)) else None,
        reward=float(reward) if isinstance(reward, (int, float)) else None,
        termination_reason=sim.get("termination_reason"),
        gold=reward_info,
    )

    usage = sim.get("agent_usage") or {}
    trace = Trace(
        trace_id=str(sim.get("id") or f"{sim.get('task_id')}#{sim.get('trial', 0)}"),
        source=TraceSource.TAU2,
        domain=domain,
        task_id=str(sim.get("task_id")),
        trial=int(sim.get("trial") or 0),
        agent_model=agent_model,
        task_requirement=task_requirement,
        steps=steps,
        outcome=outcome,
        metrics=TraceMetrics(
            duration_s=sim.get("duration"),
            cost_usd=sim.get("agent_cost"),
            tokens_in=usage.get("prompt_tokens") or usage.get("input_tokens"),
            tokens_out=usage.get("completion_tokens") or usage.get("output_tokens"),
        ),
        raw_ref=f"{raw_ref}#simulations[{index}]" if raw_ref else None,
        meta={
            "seed": sim.get("seed"),
            "mode": sim.get("mode"),
            "timestamp": sim.get("timestamp"),
        },
    )
    tokens = (trace.metrics.tokens_in, trace.metrics.tokens_out)
    trace.compute_metrics()
    # keep session-level token totals if steps carried none
    if trace.metrics.tokens_in is None:
        trace.metrics.tokens_in, trace.metrics.tokens_out = tokens
    return trace


def _flatten_ticks(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in ticks:
        if t.get("user_chunk"):
            out.append(t["user_chunk"])
        if t.get("user_tool_calls"):
            out.append({"role": "user", "content": None, "tool_calls": t["user_tool_calls"]})
        for r in t.get("user_tool_results") or []:
            out.append(r)
        if t.get("agent_chunk"):
            out.append(t["agent_chunk"])
        if t.get("agent_tool_calls"):
            out.append(
                {"role": "assistant", "content": None, "tool_calls": t["agent_tool_calls"]}
            )
        for r in t.get("agent_tool_results") or []:
            out.append(r)
    return out


def _messages_to_steps(messages: list[dict[str, Any]]) -> list[Step]:
    steps: list[Step] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool" and "tool_messages" in msg:  # MultiToolMessage
            for tm in msg["tool_messages"]:
                steps.append(_tool_step(len(steps), tm))
            continue
        if role == "tool":
            steps.append(_tool_step(len(steps), msg))
            continue
        try:
            r = Role(role)
        except ValueError:
            r = Role.SYSTEM
        usage = msg.get("usage") or {}
        gen = msg.get("generation_time_seconds")
        steps.append(
            Step(
                idx=len(steps),
                role=r,
                content=msg.get("content"),
                tool_calls=[
                    ToolCall(
                        call_id=str(tc.get("id") or ""),
                        name=str(tc.get("name")),
                        arguments=tc.get("arguments") or {},
                        requestor=tc.get("requestor") or "assistant",
                    )
                    for tc in (msg.get("tool_calls") or [])
                ],
                timestamp=msg.get("timestamp"),
                latency_ms=gen * 1000 if isinstance(gen, (int, float)) else None,
                tokens_in=usage.get("prompt_tokens") or usage.get("input_tokens"),
                tokens_out=usage.get("completion_tokens") or usage.get("output_tokens"),
                meta={"turn_idx": msg.get("turn_idx")} if msg.get("turn_idx") is not None else {},
            )
        )
    return steps


def _tool_step(idx: int, tm: dict[str, Any]) -> Step:
    return Step(
        idx=idx,
        role=Role.TOOL,
        content=None,
        tool_result=ToolResult(
            call_id=str(tm.get("id") or ""),
            content=tm.get("content"),
            is_error=bool(tm.get("error", False)),
        ),
        timestamp=tm.get("timestamp"),
        meta={"requestor": tm.get("requestor", "assistant")},
    )


def _dig(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur
