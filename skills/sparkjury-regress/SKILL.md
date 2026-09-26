---
name: sparkjury-regress
description: Compare two SparkJury evaluation stores (before and after a prompt or tool change): pass^1 and pass^k deltas, tasks fixed or broken, per-dimension mean score changes, cluster label shifts, and optional order-swapped pairwise judging. Use when someone asks whether a change made the agent better, or needs a regression report for a PR.
license: MIT
compatibility: Requires Python 3.12+ and the sparkjury package (uv sync in the SparkJury repo); model endpoints optional, offline mock mode available
metadata:
  author: sparkjury-team
  version: "0.1.0"
  product: sparkjury
  command: "sparkjury regress"
---

# sparkjury-regress

## When to use
- After re-running the same tasks with a changed agent, to show whether it improved.
- To guard a PR: fail if any task went from pass to fail.

## Steps
1. Ensure both stores went through `sparkjury-score` (and ideally `sparkjury-cluster`).
2. `sparkjury regress --before <old.db> --after <new.db> [--pairwise mock|<panel.toml>] [--out report.md] [--json]`

One call: `python scripts/run.py --before <old.db> --after <new.db>`

## Output
- Verdict: improved | improved with regressions | unchanged | regressed.
- Markdown report with metric table, fixed/broken task lists, cluster changes, pairwise summary.
- `--json` returns `RegressionReport`.

## Edge cases
- pass^k is computed at the largest k both runs share; with single-trial runs only pass^1 is shown.
- Pairwise results only count when both A/B orders agree; inconsistent pairs are reported separately.

## References
- Architecture and data contracts: `../../docs/ARCHITECTURE.md`
- Module acceptance log: `../../docs/MODULES.md`
