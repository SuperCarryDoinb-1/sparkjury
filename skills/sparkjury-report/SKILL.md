---
name: sparkjury-report
description: Produce the SparkJury evidence card for a store: totals, environment failures, pass^1 and pass^k, judge agreement, degraded decisions, ranked failure clusters with representative evidence and judge opinions, and one recommendation of what to fix first. Renders JSON, Markdown and a self-contained HTML page. Use when a PM or reviewer needs a one-page summary of an agent evaluation.
license: MIT
compatibility: Requires Python 3.12+ and the sparkjury package (uv sync in the SparkJury repo); model endpoints optional, offline mock mode available
metadata:
  author: sparkjury-team
  version: "0.1.0"
  product: sparkjury
  command: "sparkjury report"
---

# sparkjury-report

## When to use
- After clustering, to hand a decision-ready page to a PM.
- Whenever someone asks "what is the state of the agent and what should we fix first?"

## Steps
1. `sparkjury report --db <store.db> --out <dir> [--format all|json|md|html] [--title "..."]`
2. Open `<dir>/card.html` or paste `<dir>/card.md` into a doc.

One call: `python scripts/run.py --db <store.db> --out runs/card`

## Output
- `card.json` (EvidenceCard), `card.md`, `card.html`.
- The card always carries the disclaimer that clusters and suggestions are not root-cause claims.

## Edge cases
- Empty store: the card says there is nothing to fix.
- Missing stages (no clusters yet): totals and quality still render; the cluster section is empty.

## References
- Architecture and data contracts: `../../docs/ARCHITECTURE.md`
- Module acceptance log: `../../docs/MODULES.md`
