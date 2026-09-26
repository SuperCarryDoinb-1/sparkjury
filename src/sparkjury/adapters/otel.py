"""OpenTelemetry GenAI span export → Trace.

Accepts either an OTLP/JSON document (`{"resourceSpans": [...]}`) or a flat
list of span objects. Spans follow the GenAI semantic conventions:

    gen_ai.operation.name = invoke_agent | chat | execute_tool
    gen_ai.request.model / gen_ai.agent.name
    gen_ai.input.messages / gen_ai.output.messages   (JSON strings)
    gen_ai.tool.name / gen_ai.tool.call.id / gen_ai.tool.call.arguments / gen_ai.tool.call.result
    error.type                                        (on failed spans)

One Trace is produced per invoke_agent span (or per traceId when no
invoke_agent root exists). Optional custom attributes on the root span are
honoured when present: `sparkjury.task_id`, `sparkjury.trial`,
`sparkjury.success`, `sparkjury.reward`, `sparkjury.domain`.
"""

from __future__ import annotations

import json
from collections import defaultdict
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


def load_otel(path: str | Path) -> list[Trace]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return parse_otel(data, raw_ref=str(path))


def parse_otel(data: Any, raw_ref: str | None = None) -> list[Trace]:
    spans = list(_iter_spans(data))
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sp in spans:
        by_trace[sp["traceId"]].append(sp)

    traces: list[Trace] = []
    for tid, group in by_trace.items():
        group.sort(key=lambda s: int(s.get("startTimeUnixNano") or 0))
        roots = [s for s in group if _attr(s, "gen_ai.operation.name") == "invoke_agent"]
        if not roots:
            traces.append(_build_trace(tid, None, group, raw_ref))
            continue
        for root in roots:
            descendants = _descendants(root, group)
            traces.append(_build_trace(tid, root, descendants, raw_ref))
    return traces


# ---- helpers ---------------------------------------------------------------


def _iter_spans(data: Any):
    if isinstance(data, list):
        for sp in data:
            yield _norm(sp)
        return
    if isinstance(data, dict) and "resourceSpans" in data:
        for rs in data["resourceSpans"]:
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    yield _norm(sp)
        return
    if isinstance(data, dict) and "spans" in data:
        for sp in data["spans"]:
            yield _norm(sp)
        return
    raise ValueError("unrecognised OTel JSON layout")


def _norm(sp: dict[str, Any]) -> dict[str, Any]:
    """Flatten attribute list into a dict once so lookups are cheap."""
    attrs = sp.get("attributes")
    if isinstance(attrs, list):
        flat = {a["key"]: _val(a.get("value")) for a in attrs if "key" in a}
    else:
        flat = dict(attrs or {})
    out = dict(sp)
    out["_attrs"] = flat
    out["traceId"] = str(sp.get("traceId") or sp.get("trace_id") or "")
    out["spanId"] = str(sp.get("spanId") or sp.get("span_id") or "")
    out["parentSpanId"] = str(sp.get("parentSpanId") or sp.get("parent_span_id") or "")
    return out


def _val(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            x = v[k]
            return int(x) if k == "intValue" else x
    if "arrayValue" in v:
        return [_val(x) for x in v["arrayValue"].get("values", [])]
    return v


def _attr(sp: dict[str, Any], key: str, default: Any = None) -> Any:
    return sp["_attrs"].get(key, default)


def _descendants(root: dict[str, Any], group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sp in group:
        children[sp["parentSpanId"]].append(sp)
    out: list[dict[str, Any]] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(reversed(children.get(cur["spanId"], [])))
    out.sort(key=lambda s: int(s.get("startTimeUnixNano") or 0))
    return out


def _json_attr(sp: dict[str, Any], key: str) -> Any:
    v = _attr(sp, key)
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return v


def _build_trace(
    tid: str,
    root: dict[str, Any] | None,
    spans: list[dict[str, Any]],
    raw_ref: str | None,
) -> Trace:
    steps: list[Step] = []
    seen_input = False
    model: str | None = None

    for sp in spans:
        op = _attr(sp, "gen_ai.operation.name")
        if op == "chat":
            model = model or _attr(sp, "gen_ai.request.model") or _attr(sp, "gen_ai.response.model")
            if not seen_input:
                for m in _json_attr(sp, "gen_ai.input.messages") or []:
                    steps.extend(_msg_to_steps(m, len(steps), sp))
                seen_input = True
            for m in _json_attr(sp, "gen_ai.output.messages") or []:
                for s in _msg_to_steps(m, len(steps), sp):
                    s.latency_ms = _span_ms(sp)
                    s.tokens_in = _attr(sp, "gen_ai.usage.input_tokens")
                    s.tokens_out = _attr(sp, "gen_ai.usage.output_tokens")
                    steps.append(s)
        elif op == "execute_tool":
            result = _attr(sp, "gen_ai.tool.call.result")
            steps.append(
                Step(
                    idx=len(steps),
                    role=Role.TOOL,
                    tool_result=ToolResult(
                        call_id=str(_attr(sp, "gen_ai.tool.call.id") or ""),
                        content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False) if result is not None else None,
                        is_error=bool(_attr(sp, "error.type")) or str(sp.get("status", {}).get("code", "")).upper() in {"STATUS_CODE_ERROR", "2", "ERROR"},
                    ),
                    latency_ms=_span_ms(sp),
                    meta={"tool_name": _attr(sp, "gen_ai.tool.name")},
                )
            )

    root_attrs = root["_attrs"] if root else {}
    success = root_attrs.get("sparkjury.success")
    reward = root_attrs.get("sparkjury.reward")
    trace = Trace(
        trace_id=(root["spanId"] if root else tid) or tid,
        source=TraceSource.OTEL,
        domain=str(root_attrs.get("sparkjury.domain") or "unknown"),
        task_id=str(root_attrs.get("sparkjury.task_id") or root_attrs.get("gen_ai.agent.name") or tid),
        trial=int(root_attrs.get("sparkjury.trial") or 0),
        agent_model=model or (root_attrs.get("gen_ai.request.model") if root else None),
        steps=steps,
        outcome=Outcome(
            success=bool(success) if success is not None else None,
            reward=float(reward) if reward is not None else None,
            termination_reason=root_attrs.get("error.type") if root else None,
        ),
        metrics=TraceMetrics(duration_s=(_span_ms(root) / 1000) if root else None),
        raw_ref=f"{raw_ref}#trace={tid}" if raw_ref else None,
        meta={"otel_trace_id": tid, "n_spans": len(spans)},
    )
    trace.compute_metrics()
    return trace


def _msg_to_steps(m: dict[str, Any], start_idx: int, sp: dict[str, Any]) -> list[Step]:
    role_raw = m.get("role", "user")
    try:
        role = Role(role_raw)
    except ValueError:
        role = Role.SYSTEM
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    for part in m.get("parts", []):
        t = part.get("type")
        if t == "text":
            text_parts.append(str(part.get("content", "")))
        elif t == "tool_call":
            calls.append(
                ToolCall(
                    call_id=str(part.get("id") or ""),
                    name=str(part.get("name")),
                    arguments=part.get("arguments") or {},
                )
            )
        elif t == "tool_call_response":
            r = part.get("response")
            results.append(
                ToolResult(
                    call_id=str(part.get("id") or ""),
                    content=r if isinstance(r, str) else json.dumps(r, ensure_ascii=False),
                )
            )
    if "content" in m and isinstance(m["content"], str):
        text_parts.append(m["content"])

    steps: list[Step] = []
    if role == Role.TOOL or (results and not text_parts and not calls):
        for r in results:
            steps.append(Step(idx=start_idx + len(steps), role=Role.TOOL, tool_result=r))
        return steps
    steps.append(
        Step(
            idx=start_idx,
            role=role,
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=calls,
            timestamp=None,
        )
    )
    return steps


def _span_ms(sp: dict[str, Any] | None) -> float | None:
    if not sp:
        return None
    try:
        return (int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6
    except (KeyError, TypeError, ValueError):
        return None
