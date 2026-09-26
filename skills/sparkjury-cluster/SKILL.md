---
name: sparkjury-cluster
description: Group failing traces (badcases) into clusters by embedding similarity, label each cluster with a failure category (wrong_tool, missing_confirmation, hallucinated_info, loop, ...) and rank clusters by frequency x severity. Use when there are many failures and someone asks what kinds of problems dominate or what to fix first.
license: MIT
compatibility: Requires Python 3.12+ and the sparkjury package (uv sync in the SparkJury repo); model endpoints optional, offline mock mode available
metadata:
  author: sparkjury-team
  version: "0.1.0"
  product: sparkjury
  command: "sparkjury cluster"
---

# sparkjury-cluster

## When to use
- After `sparkjury-score`, to turn dozens or hundreds of failing traces into a handful of ranked problem types.

## Steps
1. `sparkjury cluster --db <store.db> [--embedder hash|openai --embed-base-url http://127.0.0.1:8003/v1] [--min-cluster-size 3] [--jev auto|off]`
2. Read the ranked table; `--json` returns `ClusterRun` with clusters, representatives and the badcases.

One call: `python scripts/run.py --db <store.db> --min-cluster-size 3`

## How it works
- badcase = outcome fail, or any dimension <= 1, or safety <= 2.
- Feature text = failing steps + tool names + judges' rationales; HDBSCAN on embeddings, deterministic threshold clustering as fallback.
- Labels: Jev `choice` over the taxonomy when available, else keyword heuristics. Priority = size x mean severity (safety 3, outcome 2, others 1).

## Output
- `badcases` and `clusters` tables; each cluster has label, size, share, severity, priority, 2-3 representative traces and a suggestion.

## Edge cases
- Fewer than `min_cluster_size` badcases: the threshold method is used automatically.
- Embedding server down: falls back to the offline hashing embedder and says so.
- Suggestions are where to look first, not root causes.

## References
- Architecture and data contracts: `../../docs/ARCHITECTURE.md`
- Module acceptance log: `../../docs/MODULES.md`
