---
name: sparkjury-clean
description: Ingest agent traces (tau2-bench results JSON or OpenTelemetry GenAI span exports) into a SparkJury store and label environment-caused failures (timeouts, tool outages, permission errors, broken user simulator) so they are excluded from judging. Use when raw trace files need cleaning before an agent evaluation, or when someone asks which failures are the environment's fault rather than the agent's.
license: MIT
compatibility: Requires Python 3.12+ and the sparkjury package (uv sync in the SparkJury repo); model endpoints optional, offline mock mode available
metadata:
  author: sparkjury-team
  version: "0.1.0"
  product: sparkjury
  command: "sparkjury ingest + precheck"
---

# sparkjury-clean

## When to use
- You have a trace file from `tau2 run` (`data/simulations/*.json`) or an OTLP/JSON span export and want it in a SparkJury store.
- You need to separate real agent failures from environment failures before scoring.

## Steps
1. Ingest: `sparkjury ingest --path <file> --source tau2|otel --db <store.db>`
2. Precheck: `sparkjury precheck --db <store.db> [--step-latency-ms 120000] [--max-duration-s N]`
3. Inspect: `sparkjury stats --db <store.db>` and `sparkjury precheck --db <store.db> --json`

Or run both in one call: `python scripts/run.py --path <file> --source tau2 --db <store.db>`

## Rules applied (deterministic, no model involved)
`empty_trace`, `infra_error`, `timeout`, `context_overflow`, `tool_unavailable` (same tool failing >= 2x with 5xx / connection errors), `permission_denied` (excluding "user not authenticated", which is the agent's fault), `user_sim_broken`.

## Output
- Rows in `traces` and `precheck` tables; `precheck --json` returns `{summary: {n_checked, n_env_failures, n_scorable, kinds}, flagged: [...]}`.
- Flagged traces are skipped by `sparkjury-score` and listed on the evidence card as environment issues.

## Edge cases
- Business errors such as "order not found" are not outages: they stay scorable.
- Re-running is idempotent; traces are upserted by `trace_id`.

## References
- Architecture and data contracts: `../../docs/ARCHITECTURE.md`
- Module acceptance log: `../../docs/MODULES.md`
