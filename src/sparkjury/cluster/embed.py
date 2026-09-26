"""Text embedders.

- `OpenAIEmbedder`: any OpenAI-compatible /v1/embeddings endpoint (vLLM serving Qwen3-Embedding on the DGX).
- `HashingEmbedder`: dependency-free, deterministic word + character-trigram hashing. Used offline,
  in tests, and as the fallback when the embedding server is unreachable.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol, Sequence


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[a-z0-9_#]+|[一-鿿]")


class HashingEmbedder:
    def __init__(self, dim: int = 512, use_trigrams: bool = True):
        self.name = f"hashing-{dim}"
        self.dim = dim
        self.use_trigrams = use_trigrams

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = _TOKEN_RE.findall(text.lower())
        feats: list[str] = list(toks)
        if self.use_trigrams:
            for t in toks:
                if len(t) > 3:
                    feats.extend(t[i:i + 3] for i in range(len(t) - 2))
        for f in feats:
            h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
            sign = 1.0 if (h >> 1) & 1 else -1.0
            vec[h % self.dim] += sign
        # sub-linear tf then l2 normalise
        vec = [math.copysign(math.log1p(abs(v)), v) if v else 0.0 for v in vec]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class OpenAIEmbedder:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, *, timeout_s: float = 60.0, batch: int = 64):
        from openai import OpenAI

        self.name = f"openai:{model}"
        self.model = model
        self.batch = batch
        self._client = OpenAI(base_url=base_url, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "EMPTY",
                              timeout=timeout_s, max_retries=2)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = list(texts[i:i + self.batch])
            resp = self._client.embeddings.create(model=self.model, input=chunk)
            vecs = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            for v in vecs:
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([x / n for x in v])
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
