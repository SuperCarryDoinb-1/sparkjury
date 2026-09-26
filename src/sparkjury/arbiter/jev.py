"""Minimal client for TypeSafe's Jev (System One) decision model.

POST https://api.typesafe.ai/v1/systemone
  Authorization: Bearer <key>
  {"model": "jev-latest", "state": "<text>", "questions": {"<id>": {...}, ...}}

Question types:
  choice  {"type": "choice", "instructions": str, "criteria": {label: description}}
          -> {"type": "choice", "choice": label, "probabilities": {...}, "confidence": float}
  score   {"type": "score",  "instructions": str, "criteria": [level0, level1, ...]}
          -> {"type": "score", "score": float, "legend": {...}, "probabilities": {...}, "confidence": float}
  noul    {"type": "noul",   "instructions": str}
          -> {"type": "noul", "noul": float}   # probability the answer is yes

Jev does not generate text and cannot be self-hosted; it is only used to break ties.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"


class JevError(RuntimeError):
    pass


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._client = httpx.Client(timeout=timeout_s, transport=transport)
        self.last_latency_ms: float | None = None
        self.last_usage: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def close(self) -> None:
        self._client.close()

    # ---- raw call ----------------------------------------------------------

    def ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if not self.api_key:
            raise JevError("TYPESAFE_API_KEY not set")
        body = {"model": self.model, "state": state, "questions": questions}
        t0 = time.perf_counter()
        try:
            resp = self._client.post(
                f"{self.base_url}/v1/systemone",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise JevError(f"transport error: {e}") from e
        finally:
            self.last_latency_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code >= 400:
            raise JevError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise JevError("non-JSON response") from e
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise JevError("response has no 'answers'")
        self.last_usage = data.get("usage")
        return answers

    # ---- typed helpers -----------------------------------------------------

    @staticmethod
    def q_score(instructions: str, levels: list[str]) -> dict[str, Any]:
        return {"type": "score", "instructions": instructions, "criteria": list(levels)}

    @staticmethod
    def q_noul(instructions: str) -> dict[str, Any]:
        return {"type": "noul", "instructions": instructions}

    @staticmethod
    def q_choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
        return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}

    @staticmethod
    def parse_score(answer: dict[str, Any], n_levels: int) -> tuple[float, int, float | None]:
        """Return (raw position, rounded 0-based level index, confidence).

        Jev positions the score along the given levels and may return it 1-based
        (the legend numbers the levels); normalise to 0-based using the legend keys.
        """
        raw = answer.get("score")
        if not isinstance(raw, (int, float)):
            raise JevError("score answer missing 'score'")
        raw = float(raw)
        legend = answer.get("legend")
        offset = 0.0
        if isinstance(legend, dict) and legend:
            try:
                offset = float(min(float(k) for k in legend.keys()))
            except (TypeError, ValueError):
                offset = 0.0
        pos = raw - offset
        idx = int(round(pos))
        idx = max(0, min(n_levels - 1, idx))
        conf = answer.get("confidence")
        return pos, idx, float(conf) if isinstance(conf, (int, float)) else None

    @staticmethod
    def parse_noul(answer: dict[str, Any]) -> float:
        p = answer.get("noul")
        if not isinstance(p, (int, float)):
            raise JevError("noul answer missing 'noul'")
        return float(p)

    @staticmethod
    def parse_choice(answer: dict[str, Any]) -> tuple[str, float | None]:
        c = answer.get("choice")
        if not isinstance(c, str):
            raise JevError("choice answer missing 'choice'")
        conf = answer.get("confidence")
        return c, float(conf) if isinstance(conf, (int, float)) else None
