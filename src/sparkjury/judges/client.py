"""Judge implementations.

- `OpenAICompatJudge`: any OpenAI-compatible chat endpoint (vLLM on DGX, StepFun, OpenRouter).
- `MockJudge`: deterministic heuristics over the trace. Used for offline tests and as the
  degradation path when no backend is reachable. Slight per-judge jitter is applied so a mock
  panel still produces occasional disagreements for the arbitration path to exercise.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Protocol

from sparkjury.judges import heuristics
from sparkjury.judges.prompts import build_messages, parse_verdict_json
from sparkjury.models.trace import Trace
from sparkjury.models.verdict import Dimension, Verdict


class Judge(Protocol):
    name: str
    model: str

    def score(self, trace: Trace, dimension: Dimension) -> Verdict: ...


# ---- LLM judge ---------------------------------------------------------------


class OpenAICompatJudge:
    def __init__(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        temperature: float = 0.0,
        max_tokens: int = 600,
        extra_body: dict | None = None,
        transcript_width: int = 4000,
        transcript_max_chars: int | None = 45000,
        include_gold: bool = False,
        shrink_retries: int = 2,
    ):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install openai (or `uv sync`) to use OpenAICompatJudge") from e
        self.name = name
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self.transcript_width = transcript_width
        self.transcript_max_chars = transcript_max_chars
        self.include_gold = include_gold
        # 提示词超出裁判上下文时把 transcript 预算减半重问几次（见 score）。
        self.shrink_retries = shrink_retries
        # 上一次 _chat 为什么停下来："length" 是被 token 上限截断，"stop" 是自然写完
        self._last_finish_reason: str | None = None
        self._client = OpenAI(base_url=base_url, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "EMPTY",
                              timeout=timeout_s, max_retries=max_retries)

    def healthcheck(self, timeout_s: float = 3.0) -> bool:
        """True when the endpoint answers /models (vLLM, StepFun and OpenRouter all do).

        失败原因存进 self.health_error：401（key 无效）和连接超时（网络不通）在降级
        消息里长得一样，排查方向却完全相反——DGX 节点上实测过 StepFun key 无效被
        误当网络问题查了一轮。"""
        try:
            self._client.with_options(timeout=timeout_s, max_retries=0).models.list()
            self.health_error: str | None = None
            return True
        except Exception as e:  # noqa: BLE001
            self.health_error = f"{type(e).__name__}: {e}"
            return False

    # 提示词撑爆裁判上下文窗口时，后端回的是 400，不是空答案，JSON 追问救不了——只能把 transcript
    # 缩短。事先算准预算需要后端的 tokenizer（我们没有），但错误本身把问题说得很清楚，照着缩再问一次。
    CONTEXT_ERROR_MARKERS = ("maximum context length", "context_length_exceeded", "too many tokens",
                             "reduce the length", "context window")

    def _is_context_overflow(self, err: Exception) -> bool:
        text = f"{type(err).__name__}: {err}".lower()
        return any(m in text for m in self.CONTEXT_ERROR_MARKERS)

    def _bigger_budget(self) -> int:
        """被截断或空答案之后该用的预算：原样再问一遍等于再截一次，4 倍才够（上限 8000）。"""
        return min(self.max_tokens * self.EMPTY_ANSWER_MULTIPLIER, self.EMPTY_ANSWER_MAX_TOKENS)

    # 思维模型的输出预算被推理吃光时，content 会是空串（OpenAI 兼容接口把推理放在
    # reasoning_content 里，我们只读 content）。真批实测（step-3.7-flash、max_tokens=1200、
    # 同一条 prompt 重跑 8 次）：6 次整段输出都花在推理上、content 0 字、finish_reason=length，
    # 三十二个维度里十七个因此废掉；预算给到 4000 后同样 8 次里 8 次都拿到 JSON，三十二个
    # 维度只剩一个失手。空答案重问一次（加大预算）比原样再问一遍有用得多。
    EMPTY_ANSWER_MULTIPLIER = 4
    EMPTY_ANSWER_MAX_TOKENS = 8000

    def score(self, trace: Trace, dimension: Dimension) -> Verdict:
        t0 = time.perf_counter()
        budget = self.transcript_max_chars
        raw: str | None = None
        data: dict[str, Any] | None = None
        last_err: Exception | None = None
        for _ in range(self.shrink_retries + 1):
            messages = build_messages(trace, dimension, transcript_width=self.transcript_width,
                                      transcript_max_chars=budget, include_gold=self.include_gold)
            raw = None
            try:
                raw = self._chat(messages)
                if not raw.strip():
                    # 一个字都没输出：不是 JSON 写坏了，是预算不够写不完。原样再问一遍没用
                    # （真批上这种空答案重问后仍然为空），加大预算才是对症的那一下。
                    raw = self._chat(messages, max_tokens=self._bigger_budget())
                data = parse_verdict_json(raw)
                break
            except Exception as e:  # noqa: BLE001 - any backend/parse failure becomes an errored verdict
                last_err = e
                if self._is_context_overflow(e) and budget and budget > 2000:
                    # 真批实测：transcript 预算 45000 字符时，最长的那条 trace 让 16k 上下文的
                    # Nemotron 回「at least 15185 input tokens」，三个维度各废掉一次判定。
                    budget = max(2000, budget // 2)
                    continue
                # one nudge retry for malformed JSON
                if raw is not None:
                    try:
                        # 上一句是被 token 上限截断的，就加大预算再问：原预算只会截在同一个地方
                        # （上面那一处管「一个字都没出来」，这里管「有内容但 JSON 没写完」）。
                        bump = self._bigger_budget() if self._last_finish_reason == "length" else None
                        raw2 = self._chat(messages + [
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": "Output only the JSON object described in the instructions."},
                        ], max_tokens=bump)
                        # 先记下来再解析：解析再失败时留的是模型最后说的那句，不是上一句
                        raw = raw2
                        data = parse_verdict_json(raw2)
                        break
                    except Exception as e2:  # noqa: BLE001
                        return self._errored(trace, dimension, f"{type(e2).__name__}: {e2}", raw, t0)
                else:
                    return self._errored(trace, dimension, f"{type(e).__name__}: {e}", raw, t0)
        if data is None:
            return self._errored(trace, dimension,
                                 f"{type(last_err).__name__}: {last_err}", raw, t0)
        if data.pop("_salvaged", False):
            # 补救成功但用的是残缺输出：分数保住了，说明白它是补出来的
            data["rationale"] = (data["rationale"] + " [salvaged from malformed judge output]").strip()
        if dimension == Dimension.OUTCOME:
            # rubric 规定 label 是分数的函数（3-4 = pass，0-2 = fail），裁判自报的 label
            # 与分数矛盾时以分数为准。真批上出现过一次（judge_b 给了 score=1、label=pass），
            # 而 panel 的一致性只看 label，那条错的 label 正好把一次真实分歧盖住了：
            # outcome 的 label 一致率读出 42/42，而分数有 3 条不同。
            data["label"] = "pass" if data["score"] >= 3 else "fail"
        else:
            data["label"] = None
        return Verdict(
            trace_id=trace.trace_id, judge=self.name, model=self.model, dimension=dimension,
            latency_ms=(time.perf_counter() - t0) * 1000, raw=raw, **data,
        )

    def _chat(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature,
            max_tokens=max_tokens or self.max_tokens, extra_body=self.extra_body or None,
        )
        choice = resp.choices[0]
        # 记下这次为什么停：被截断（length）和自然写完（stop）要用不同的预算重问
        self._last_finish_reason = getattr(choice, "finish_reason", None)
        return choice.message.content or ""

    def _errored(self, trace: Trace, dimension: Dimension, err: str, raw: str | None, t0: float) -> Verdict:
        return Verdict(trace_id=trace.trace_id, judge=self.name, model=self.model, dimension=dimension,
                       error=err, raw=raw, latency_ms=(time.perf_counter() - t0) * 1000)


# ---- Mock judge --------------------------------------------------------------


class MockJudge:
    """Heuristic judge. `jitter` adds deterministic per-(judge, trace) noise in [0, 1] score points."""

    def __init__(self, name: str = "mock", model: str = "mock-heuristic-v1", *, jitter: float = 0.0, latency_ms: float = 0.0):
        self.name = name
        self.model = model
        self.jitter = jitter
        self.latency_ms = latency_ms

    def score(self, trace: Trace, dimension: Dimension) -> Verdict:
        t0 = time.perf_counter()
        base = heuristics.evaluate(trace, dimension)
        score, label = base.score, base.label
        if self.jitter > 0:
            h = int(hashlib.sha1(f"{self.name}|{trace.trace_id}|{dimension.value}".encode()).hexdigest(), 16)
            u = (h % 1000) / 1000.0
            if u < self.jitter:
                delta = 1 + (1 if (h >> 12) % 4 == 0 else 0)   # mostly +-1, sometimes +-2
                score = max(0, min(4, score + (delta if (h >> 10) % 2 else -delta)))
                if dimension == Dimension.OUTCOME and trace.outcome.success is None:
                    label = "pass" if score >= 3 else "fail"
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        return Verdict(
            trace_id=trace.trace_id, judge=self.name, model=self.model, dimension=dimension,
            score=score, label=label if dimension == Dimension.OUTCOME else None,
            confidence=base.confidence, evidence_steps=base.evidence_steps, rationale=base.rationale,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
