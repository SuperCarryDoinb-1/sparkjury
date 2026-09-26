"""Generate the six Agent Skills under skills/ (SKILL.md, skill-card.md, scripts/run.py).

Re-run after changing the CLI; `scripts/validate_skills.py` checks the result against the spec.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"

SKILLS: dict[str, dict[str, str]] = {
    "sparkjury-clean": dict(
        cmd="ingest + precheck",
        mode="ingest+precheck",
        desc=(
            "Ingest agent traces (tau2-bench results JSON or OpenTelemetry GenAI span exports) into a SparkJury store "
            "and label environment-caused failures (timeouts, tool outages, permission errors, broken user simulator) "
            "so they are excluded from judging. Use when raw trace files need cleaning before an agent evaluation, "
            "or when someone asks which failures are the environment's fault rather than the agent's."
        ),
        body="""## When to use
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
""",
    ),
    "sparkjury-evalset": dict(
        cmd="run --stages EVALSET",
        mode="evalset",
        desc=(
            "Build the evaluation set for a SparkJury run: pick the scorable traces (precheck-clean), optionally restrict "
            "to given task ids or cap the count, and write runs/<run_id>/evalset.json. Use when you want a reproducible, "
            "smaller subset of traces to judge, or need the exact list of traces a run evaluated."
        ),
        body="""## When to use
- Before scoring, to fix exactly which traces will be judged (reproducibility, demo speed).
- To answer "which traces did run X evaluate?" (read `runs/<run_id>/evalset.json`).

## Steps
1. Make sure the store has been prechecked (`sparkjury-clean`).
2. Run only the EVALSET stage of the harness:
   `sparkjury run --config <run.toml> --stages EVALSET` (set `[evalset] limit` / `task_ids` in the TOML), or
   `python scripts/run.py --db <store.db> --run-id <id> [--limit N] [--task-ids a,b]`
3. The stage writes `runs/<run_id>/evalset.json` (list of trace ids) and records `n_traces`, `n_tasks` in the manifest.

## Output
- `runs/<run_id>/evalset.json`
- Manifest entry `stages.EVALSET`

## Edge cases
- If precheck was never run, every trace counts as scorable.
- `limit` applies after `task_ids`.
""",
    ),
    "sparkjury-score": dict(
        cmd="score + arbitrate",
        mode="score+arbitrate",
        desc=(
            "Score agent traces with a three-judge panel (three different model families) on four dimensions - outcome, "
            "tool_use, efficiency, safety - then resolve disagreements: cloud Jev decision model when configured, local "
            "judge otherwise (marked degraded), with a 5% audit sample. Use when you need per-trace quality scores, judge "
            "rationales, or want to know which traces the judges disagreed on."
        ),
        body="""## When to use
- After `sparkjury-clean`, to get a 0-4 score per dimension per trace with evidence steps and rationales.
- To find contested traces (`needs arbitration`) and how they were resolved.

## Steps
1. Choose judges: `mock` (offline) or a panel TOML (see `deploy/judges.example.toml`; keys come from env vars such as `STEPFUN_API_KEY`).
2. Score: `sparkjury score --db <store.db> --judges mock|<panel.toml> [--dims outcome,tool_use,efficiency,safety] [--limit N]`
3. Arbitrate: `sparkjury arbitrate --db <store.db> --judges mock|<panel.toml> [--jev auto|off] [--audit-rate 0.05]`
   (`TYPESAFE_API_KEY` enables Jev; without it every disagreement is resolved locally and flagged degraded)
4. Inspect one trace: `sparkjury verdicts <trace_id> --db <store.db>`

One call: `python scripts/run.py --db <store.db> --judges mock`

## Agreement rule
- outcome: three pass/fail labels must match; score dimensions: max - min <= 1. Any errored judge counts as disagreement.

## Output
- `verdicts`, `panel`, `arbitration` tables; `score --json` and `arbitrate --json` return summaries and per-trace results.

## Edge cases
- Judges that fail their health check are swapped for mock judges by the harness (`sparkjury run`), never silently.
- The agent under test's own model must not sit on the panel.
""",
    ),
    "sparkjury-cluster": dict(
        cmd="cluster",
        mode="cluster",
        desc=(
            "Group failing traces (badcases) into clusters by embedding similarity, label each cluster with a failure "
            "category (wrong_tool, missing_confirmation, hallucinated_info, loop, ...) and rank clusters by frequency x "
            "severity. Use when there are many failures and someone asks what kinds of problems dominate or what to fix first."
        ),
        body="""## When to use
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
""",
    ),
    "sparkjury-report": dict(
        cmd="report",
        mode="report",
        desc=(
            "Produce the SparkJury evidence card for a store: totals, environment failures, pass^1 and pass^k, judge "
            "agreement, degraded decisions, ranked failure clusters with representative evidence and judge opinions, and "
            "one recommendation of what to fix first. Renders JSON, Markdown and a self-contained HTML page. Use when a PM "
            "or reviewer needs a one-page summary of an agent evaluation."
        ),
        body="""## When to use
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
""",
    ),
    "sparkjury-regress": dict(
        cmd="regress",
        mode="regress",
        desc=(
            "Compare two SparkJury evaluation stores (before and after a prompt or tool change): pass^1 and pass^k deltas, "
            "tasks fixed or broken, per-dimension mean score changes, cluster label shifts, and optional order-swapped "
            "pairwise judging. Use when someone asks whether a change made the agent better, or needs a regression report for a PR."
        ),
        body="""## When to use
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
""",
    ),
}

RUN_PY = '''#!/usr/bin/env python3
"""Thin wrapper: runs the SparkJury CLI for this skill. Extra arguments are passed through.

Usage: python scripts/run.py [CLI options...]
Requires the `sparkjury` package (uv sync in the SparkJury repo, or pip install -e <repo>).
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

MODE = "__MODE__"


def cli(*args: str) -> int:
    exe = shutil.which("sparkjury")
    cmd = [exe, *args] if exe else [sys.executable, "-m", "sparkjury.cli", *args]
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


def main(argv: list[str]) -> int:
    if MODE == "ingest+precheck":
        db = argv[argv.index("--db") + 1] if "--db" in argv else "runs/sparkjury.db"
        rc = cli("ingest", *argv)
        return rc or cli("precheck", "--db", db)
    if MODE == "score+arbitrate":
        rc = cli("score", *argv)
        if rc:
            return rc
        keep, skip = [], {"--dims", "--limit", "--workers"}
        i = 0
        while i < len(argv):
            if argv[i] in skip:
                i += 2
                continue
            keep.append(argv[i])
            i += 1
        return cli("arbitrate", *keep)
    if MODE == "evalset":
        args = dict(zip(argv[::2], argv[1::2]))
        db = args.get("--db", "runs/sparkjury.db")
        run_id = args.get("--run-id", "evalset")
        lines = [f'run_id = "{run_id}"', f'db = "{db}"', 'stages = ["EVALSET"]', "[evalset]"]
        if "--limit" in args:
            lines.append(f"limit = {int(args['--limit'])}")
        if "--task-ids" in args:
            lines.append("task_ids = [" + ", ".join(f'"{t}"' for t in args["--task-ids"].split(",")) + "]")
        p = pathlib.Path(tempfile.mkdtemp()) / "evalset.toml"
        p.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
        return cli("run", "--config", str(p))
    return cli(MODE, *argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

CARD = """# Skill card: {name}

| Field | Value |
|---|---|
| Owner | SparkJury team (DGX Spark Hackathon, 3rd edition) |
| Version | 0.1.0 |
| Product | SparkJury agent evaluation harness |
| Underlying command | `sparkjury {cmd}` |
| Risk level | {risk} |
| Data handling | Reads local trace files / SQLite stores only. Sends transcript excerpts to configured judge endpoints (local vLLM by default) and, when `TYPESAFE_API_KEY` is set, disagreement summaries to the Jev cloud API. Nothing else leaves the machine. |
| Network | Optional: StepFun / Jev / vLLM endpoints as configured |
| Side effects | Writes to the given SQLite store and `runs/<run_id>/` |
| Evaluation dataset | `../../data/samples/` (tau2 + OTel samples), `../../tests/` |
| Signature | `skill.oms.sig` to be produced with `model_signing` before publishing (see `../../scripts/sign_skills.sh`) |
"""


def main() -> None:
    for name, s in SKILLS.items():
        d = ROOT / name
        (d / "scripts").mkdir(parents=True, exist_ok=True)
        skill_md = (
            "---\n"
            f"name: {name}\n"
            f"description: {s['desc']}\n"
            "license: MIT\n"
            "compatibility: Requires Python 3.12+ and the sparkjury package (uv sync in the SparkJury repo); model endpoints optional, offline mock mode available\n"
            "metadata:\n"
            "  author: sparkjury-team\n"
            '  version: "0.1.0"\n'
            "  product: sparkjury\n"
            f'  command: "sparkjury {s["cmd"]}"\n'
            "---\n\n"
            f"# {name}\n\n"
            f"{s['body']}\n"
            "## References\n"
            "- Architecture and data contracts: `../../docs/ARCHITECTURE.md`\n"
            "- Module acceptance log: `../../docs/MODULES.md`\n"
        )
        (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (d / "scripts" / "run.py").write_text(RUN_PY.replace("__MODE__", s["mode"]), encoding="utf-8")
        risk = "read/write (local files)" if name == "sparkjury-report" else "write (local store)"
        (d / "skill-card.md").write_text(CARD.format(name=name, cmd=s["cmd"], risk=risk), encoding="utf-8")
    print(f"wrote {len(SKILLS)} skills to {ROOT}")


if __name__ == "__main__":
    main()
