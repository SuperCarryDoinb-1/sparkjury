# SparkJury × NVIDIA NeMo Agent Toolkit

NeMo Agent Toolkit (NAT, Apache-2.0) already ships a trajectory evaluator and a profiler, and its judge can
point at a local vLLM. What it does not have is multi-judge arbitration across model families, environment-failure
precheck, and failure clustering with priorities. That is the layer SparkJury adds.

## What is here

| Path | Purpose |
|---|---|
| `nat_sparkjury/` | NAT plugin package. Registers `_type: sparkjury` as an evaluator through the `nat.components` entry point. |
| `configs/sparkjury_eval.yml` | Example `nat eval` config: NAT trajectory evaluator + profiler + SparkJury evaluator, all judges on local vLLM. |
| `../src/sparkjury/integrations/nat_eval.py` | Framework-independent evaluator core (tested without NAT installed). |
| `../src/sparkjury/adapters/nat.py` | Converts a NAT `EvalInputItem` / `workflow_output.json` row (with `intermediate_steps`) into a SparkJury `Trace`. |

## Install (on the DGX node)

```bash
uv pip install "nvidia-nat[eval,profiler]"
uv pip install -e nat/nat_sparkjury          # pulls sparkjury from ../.. as editable
nat info components | grep sparkjury        # the evaluator is discoverable
```

## Run

```bash
nat eval --config_file nat/configs/sparkjury_eval.yml
# or score a recorded run without re-executing the workflow
nat eval --config_file nat/configs/sparkjury_eval.yml --skip_workflow --dataset .tmp/nat/sparkjury/workflow_output.json
```

Outputs land in `eval.general.output_dir`: `workflow_output.json`, `trajectory_output.json`, `sparkjury_output.json`
(`average_score` + per-item `score` in [0,1] and a `reasoning` dict with per-dimension scores, outcome label,
arbitration sources and judge rationales), plus the profiler CSV/JSON.

## Two-way bridge

- NAT → SparkJury: `workflow_output.json` rows are also a valid input for the full SparkJury pipeline (clustering,
  evidence card, regression): `sparkjury ingest --source nat` is planned; today use the adapter from Python.
- SparkJury → NAT: the `sparkjury` evaluator lets any NAT workflow get the three-judge scores inside `nat eval`.
