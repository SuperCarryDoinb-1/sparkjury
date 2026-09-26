"""NeMo Agent Toolkit (NAT) evaluation item → Trace.

`nat eval` hands each evaluator an EvalInputItem: `id`, `input_obj` (the question), `expected_output_obj`
(gold answer), `output_obj` (generated answer) and `trajectory` (a list of IntermediateStep). The same
shape is what `workflow_output.json` stores (`question`, `answer`, `generated_answer`, `intermediate_steps`).

IntermediateStep payloads carry `event_type` (LLM_START / LLM_END / TOOL_START / TOOL_END / ...), `name`
and `data: {input, output}`. This adapter is tolerant: it accepts NAT objects or plain dicts, serialized
either flat or under `payload`.
"""

from __future__ import annotations

import json
from typing import Any

from sparkjury.models.trace import Outcome, Role, Step, ToolCall, ToolResult, Trace, TraceSource


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _text(x: Any, width: int | None = None) -> str | None:
    if x is None:
        return None
    if isinstance(x, str):
        s = x
    else:
        try:
            s = json.dumps(x, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            s = str(x)
    return s if width is None or len(s) <= width else s[:width]


def nat_item_to_trace(item: Any, *, domain: str = "nat", trial: int = 0, agent_model: str | None = None) -> Trace:
    item_id = str(_get(item, "id", default="0"))
    question = _get(item, "input_obj", "question")
    generated = _get(item, "output_obj", "generated_answer")
    expected = _get(item, "expected_output_obj", "answer")
    trajectory = _get(item, "trajectory", "intermediate_steps", default=[]) or []

    steps: list[Step] = []
    if question is not None:
        steps.append(Step(idx=0, role=Role.USER, content=_text(question)))
    pending_tools: dict[str, str] = {}  # uuid -> tool name
    for raw in trajectory:
        payload = _get(raw, "payload", default=raw)
        etype = str(_get(payload, "event_type", default="") or "")
        etype = etype.split(".")[-1].upper()  # IntermediateStepType.TOOL_END -> TOOL_END
        name = _get(payload, "name", default=None)
        data = _get(payload, "data", default={}) or {}
        d_in, d_out = _get(data, "input"), _get(data, "output")
        uuid = str(_get(payload, "UUID", "uuid", default=len(steps)))
        if etype == "LLM_END":
            calls = _extract_tool_calls(d_out)
            content = None if calls else _text(_extract_llm_text(d_out), 2000)
            steps.append(Step(idx=len(steps), role=Role.ASSISTANT, content=content, tool_calls=calls, meta={"llm": name}))
            if agent_model is None and name:
                agent_model = str(name)
        elif etype == "TOOL_START":
            pending_tools[uuid] = str(name or "tool")
            args = d_in if isinstance(d_in, dict) else ({"input": d_in} if d_in is not None else {})
            steps.append(Step(idx=len(steps), role=Role.ASSISTANT, content=None,
                              tool_calls=[ToolCall(call_id=uuid, name=str(name or "tool"), arguments=args)]))
        elif etype == "TOOL_END":
            out = _text(d_out, 4000)
            is_err = bool(out) and out.lower().startswith(("error", "exception", "traceback"))
            steps.append(Step(idx=len(steps), role=Role.TOOL,
                              tool_result=ToolResult(call_id=uuid if uuid in pending_tools else _last_call_id(steps), content=out, is_error=is_err),
                              meta={"tool_name": name}))
    if generated is not None and not (steps and steps[-1].role == Role.ASSISTANT and steps[-1].content):
        steps.append(Step(idx=len(steps), role=Role.ASSISTANT, content=_text(generated, 2000)))

    success = None
    if expected is not None and generated is not None and isinstance(expected, str) and isinstance(generated, str):
        success = expected.strip().lower() == generated.strip().lower() or None  # exact match only; else unknown
    trace = Trace(
        trace_id=f"nat-{item_id}", source=TraceSource.CUSTOM, domain=domain, task_id=item_id, trial=trial,
        agent_model=agent_model, steps=steps,
        outcome=Outcome(success=success, gold={"expected": expected, "generated": generated} if expected is not None else {}),
        meta={"nat": True, "n_intermediate_steps": len(trajectory)},
    )
    trace.compute_metrics()
    return trace


def _extract_tool_calls(out: Any) -> list[ToolCall]:
    calls = _get(out, "tool_calls", default=None)
    if not calls and isinstance(out, dict):
        calls = (out.get("additional_kwargs") or {}).get("tool_calls")
    result: list[ToolCall] = []
    for c in calls or []:
        fn = _get(c, "function", default=None)
        name = _get(fn, "name", default=None) if fn else _get(c, "name", default=None)
        args = _get(fn, "arguments", default=None) if fn else _get(c, "args", "arguments", default={})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"raw": args}
        result.append(ToolCall(call_id=str(_get(c, "id", default="")), name=str(name or "tool"), arguments=args or {}))
    return result


def _extract_llm_text(out: Any) -> Any:
    if isinstance(out, str) or out is None:
        return out
    for k in ("content", "text", "output"):
        v = _get(out, k, default=None)
        if isinstance(v, str):
            return v
    return out


def _last_call_id(steps: list[Step]) -> str:
    for s in reversed(steps):
        if s.tool_calls:
            return s.tool_calls[-1].call_id
    return ""
