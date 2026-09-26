"""Prompt assembly for LLM judges: rubric + trace transcript + strict JSON output contract."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sparkjury.models.trace import Role, Trace
from sparkjury.models.verdict import Dimension

RUBRIC_DIR = Path(__file__).parent / "rubrics"

OUTPUT_CONTRACT = """
Do not think out loud and do not explain before answering. Respond with ONE JSON object and nothing else, in this exact shape:
{"score": <integer 0-4>, "label": <"pass" | "fail" | null>, "confidence": <number 0-1>,
 "evidence_steps": [<step indices>], "rationale": "<two or three sentences>"}
"""


@lru_cache(maxsize=None)
def load_rubric(dimension: Dimension) -> str:
    return (RUBRIC_DIR / f"{dimension.value}.md").read_text(encoding="utf-8").strip()


_LEVEL_RE = re.compile(r"^-\s+([0-4])\s{2,}(.+?)\s*$", re.M)


@lru_cache(maxsize=None)
def rubric_levels(dimension: Dimension) -> list[str]:
    """The five scoring levels of a rubric, ordered 0..4, as short descriptions (for Jev score questions)."""
    found = {int(m.group(1)): m.group(2) for m in _LEVEL_RE.finditer(load_rubric(dimension))}
    if sorted(found) != [0, 1, 2, 3, 4]:
        raise ValueError(f"rubric {dimension.value} must define levels 0..4, found {sorted(found)}")
    return [f"{i}: {found[i]}" for i in range(5)]


def build_messages(trace: Trace, dimension: Dimension, *, transcript_width: int = 400) -> list[dict[str, str]]:
    system = load_rubric(dimension) + "\n" + OUTPUT_CONTRACT
    gold = _gold_summary(trace)
    visible = trace.transcript(width=transcript_width)
    n_visible = sum(1 for st in trace.steps if st.role != Role.SYSTEM)
    user = (
        f"Task id: {trace.task_id} (trial {trace.trial}); domain: {trace.domain}; agent model: {trace.agent_model or 'unknown'}\n"
        f"Termination reason: {trace.outcome.termination_reason or 'unknown'}\n"
        f"{gold}\n"
        f"Transcript ({n_visible} steps; [n] is the step index):\n"
        f"{visible}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _gold_summary(trace: Trace) -> str:
    o = trace.outcome
    if o.success is None:
        return "Gold outcome: not available."
    parts = [f"Gold outcome: {'SUCCESS' if o.success else 'FAIL'} (reward={o.reward})"]
    db = (o.gold or {}).get("db_check")
    if isinstance(db, dict):
        parts.append(f"db_match={db.get('db_match')}")
    return "; ".join(parts) + "."


_THINK_RE = re.compile(r"<think>.*?</think>|<reasoning>.*?</reasoning>", re.S | re.I)


def _candidate_objects(text: str) -> list[str]:
    """Every balanced {...} span in the text, outermost first, in order of appearance."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start:i + 1])
                start = -1
    return out


def parse_verdict_json(text: str) -> dict[str, Any]:
    """Extract the verdict object from model output.

    Tolerates <think> blocks, code fences, prose before/after, and several JSON objects (the last
    valid one that carries a score wins, since models often restate their answer at the end).
    Common JSON slips (trailing commas, raw newlines in strings, smart quotes) are repaired.
    """
    text = _THINK_RE.sub("", text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return _validate(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        pass
    last_err: Exception | None = None
    for cand in reversed(_candidate_objects(text)):
        for attempt in (cand, _repair(cand)):
            try:
                return _validate(json.loads(attempt))
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
                continue
    if last_err is not None:
        raise ValueError(f"no valid verdict object in judge output ({type(last_err).__name__}: {last_err})")
    raise ValueError("no JSON object in judge output")


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _repair(cand: str) -> str:
    """Cheap fixes for the usual LLM JSON slips: trailing commas, raw newlines/tabs inside strings, smart quotes."""
    s = cand.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    s = _CTRL_RE.sub(" ", s)
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                out.append("\\n")
                continue
            elif ch == "\t":
                out.append("\\t")
                continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def _validate(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("judge output is not an object")
    score = obj.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise ValueError("score missing or not numeric")
    score = int(round(score))
    if not 0 <= score <= 4:
        raise ValueError(f"score {score} out of range 0-4")
    label = obj.get("label")
    if label is not None:
        label = str(label).lower()
        if label not in {"pass", "fail"}:
            raise ValueError(f"bad label {label!r}")
    conf = obj.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
    if conf is not None:
        conf = min(1.0, max(0.0, conf))
    steps_raw = obj.get("evidence_steps") or []
    steps = sorted({int(s) for s in steps_raw if isinstance(s, (int, float)) and not isinstance(s, bool)})
    return {
        "score": score,
        "label": label,
        "confidence": conf,
        "evidence_steps": steps,
        "rationale": str(obj.get("rationale") or "").strip(),
    }
