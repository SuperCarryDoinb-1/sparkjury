# Deploying SparkJury on the DGX Spark node

Team node: spark node 80. SSH `ssh -p 6030 asus_gx10@61.172.235.130` (password from the login sheet).
Public ports for this node: 7030 → node 7000, 8030 → node 8888, 9030 → node 9000. Everything else stays on
127.0.0.1 and is reached through SSH port forwarding.

Rules from the node manual that these scripts respect: long jobs run in `tmux`; models are downloaded on the node,
never `scp`'d (uplink is shared by 50 teams); vLLM binds `127.0.0.1`; only the API binds `0.0.0.0:9000`, and it
requires a token; no `reboot`, no system config changes; the node is wiped after the event, so push code and copy
results out.

## 1. First time

```bash
git clone <repo> sparkjury && cd sparkjury
bash deploy/dgx/setup_node.sh          # preflight, uv, venvs (sparkjury, vLLM, tau2-bench), creates deploy/dgx/.env
nano deploy/dgx/.env                    # STEPFUN_API_KEY, TYPESAFE_API_KEY, SPARKJURY_API_TOKEN, model names
bash deploy/dgx/download_models.sh      # skips anything already under /home/xsuper/models
```

If `pip install vllm` does not work on GB10 (aarch64 + CUDA 13), run vLLM from NVIDIA's container instead and keep
the same ports:

```bash
docker run --gpus all --rm -d --name judge_a -p 127.0.0.1:8001:8000 -v $HOME/models:/models \
  nvcr.io/nvidia/vllm:latest vllm serve /models/Qwen3-30B-A3B-Instruct-2507 --served-model-name Qwen/Qwen3-30B-A3B-Instruct-2507 --gpu-memory-utilization 0.30
```

## 2. Start the stack

```bash
bash deploy/dgx/start_judges.sh         # tmux session "sparkjury": judge_a, judge_b, embed, agent, api
bash deploy/dgx/status.sh               # GPU, endpoints, listeners
tmux attach -t sparkjury                # Ctrl+B then D to detach
```

Memory split (128 GB unified, fractions in `.env`): judge_a 0.30, judge_b 0.22, embed 0.04, agent 0.18. Lower them
if a server fails to start; `--no-agent` skips the agent-under-test when tau2 results already exist.

## 3. Produce traces with tau2-bench

```bash
bash deploy/dgx/run_tau2.sh 30 3 4      # 30 retail tasks x 3 trials, concurrency 4 -> data/simulations/<name>/results.json
```

The simulated user runs on judge_a's model (different weights from the agent-under-test, as the plan requires).
Check `.venv-tau2/bin/tau2 run --help` once: flag names (`--agent-llm-args`, `--save-to`) must match the installed version.
Newer tau2 writes `<SIM_DIR>/<NAME>/results.json`, older versions write `<SIM_DIR>/<NAME>.json`; the script takes
either.

### The evaluator's own LLM call is what loses traces

tau2 finishes a conversation and then evaluates it in two steps: a database comparison, and a natural-language
assertion judge. That NL judge is the **only evaluator step that calls an LLM**, and its model name is hardcoded
to `gpt-4.1` in `tau2/config.py`. This node cannot reach `api.openai.com` (measured: connection error after an
8 s timeout, while `api.stepfun.com` answers in 50 ms). So for every task whose `evaluation_criteria` carry
`nl_assertions`:

1. the conversation runs to completion,
2. `evaluate_simulation` raises on the unreachable call,
3. `run_with_retry` re-runs the **whole simulation, conversation included**, up to 4 times,
4. what gets saved is `termination_reason=infrastructure_error` with **`messages: []`** — the conversation is gone.

The 2026-09-26 batch lost 26 of its 90 traces this way. It is not flaky: all 26 belong to the 9 tasks that carry
`nl_assertions` (8 of them lost all three trials, task 19 lost two), and the other 21 tasks lost **zero**. Each
lost trace had four conversations run for it, so the batch also spent GPU time on work it then threw away.

`run_tau2.sh` now handles this before starting: it calls `deploy/dgx/patch_tau2_nl_assertions.sh ensure`, which
makes the model name and call args read from `TAU2_LLM_NL_ASSERTIONS` / `TAU2_LLM_NL_ASSERTIONS_ARGS` (default
unchanged, so a machine that *can* reach OpenAI behaves exactly as before), points them at the local judge_a
endpoint, and then **imports `tau2.config` to check the override actually took effect** — if the patch is missing
the script exits non-zero instead of quietly losing a quarter of the batch. The patcher backs up
`config.py.sparkjury.orig` and has `revert`.

## 4. Evaluate

```bash
cp deploy/run.example.toml deploy/run.toml   # set [[inputs]] path to the tau2 result, keep the three real judges
uv run sparkjury run --config deploy/run.toml
```

Watch it live from your laptop with the cockpit: `http://61.172.235.130:9030/?token=<SPARKJURY_API_TOKEN>`
or, safer, forward the port: `ssh -p 6030 -L 9000:localhost:9000 asus_gx10@61.172.235.130` then `http://localhost:9000/?token=...`.

## 5. Bundle the demo

```bash
bash deploy/dgx/make_demo_bundle.sh <run_id>          # -> data/samples/bundles/<run_id>.tar.gz
```

Copy the bundle to a laptop (it is small) and replay offline: `tar xzf ... -C runs && uv run sparkjury serve`.
The cockpit lists every run under `runs/`, so the finals demo does not depend on the node being reachable.

## 6. NeMo Agent Toolkit (optional, platform points)

```bash
uv pip install "nvidia-nat[eval,profiler]" && uv pip install -e nat/nat_sparkjury
nat info components | grep sparkjury
nat eval --config_file nat/configs/sparkjury_eval.yml --skip_workflow --dataset <workflow_output.json>
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| vLLM OOM at start, or the whole tmux session vanishes | the kernel OOM killer took the user session down (seen 9-26 with bf16 Qwen3-30B next to Nemotron). Use the FP8 judge_a, keep the `*_MEM` sum <= 0.75, start servers one at a time, `loginctl enable-linger` |
| judge answers but `sparkjury score` reports errors | `curl 127.0.0.1:8001/v1/models`; check `logs/judge_a.log`; try `extra_body = { chat_template_kwargs = { enable_thinking = false } }` in `deploy/judges.toml` |
| cockpit 401 | add `?token=<SPARKJURY_API_TOKEN>` to the URL once; it is remembered in the browser |
| `start_judges.sh` exits with `SPARKJURY_API_TOKEN 是空的`, or `serve` says `refusing to serve on 0.0.0.0` | the public API must have auth. Put `SPARKJURY_API_TOKEN` in `deploy/dgx/.env` (start_judges.sh fails *before* it creates the tmux session or touches vLLM, so nothing is half-started). For a local-only cockpit use `--host 127.0.0.1`, or start judges alone with `--no-api` |
| API answers 403 `refusing unauthenticated access from a non-loopback client` | the service is running without a token, so it only accepts loopback clients. Set the token and restart, or reach it through `ssh -L` |
| SSH drops kill jobs | everything runs inside tmux; `tmux attach -t sparkjury` |
| `No space left on device` | `df -h`, clear `~/.cache/huggingface`, old `runs/` |
| `infrastructure_error` records with empty `messages`, only in tasks that have `nl_assertions` | tau2's NL-assertion evaluator calls a hardcoded `gpt-4.1` that this node cannot reach, and the failed evaluation throws away the finished conversation. `bash deploy/dgx/patch_tau2_nl_assertions.sh ensure` (run_tau2.sh does it for you) |
| `tau2 produced no results file` right after a multi-hour batch | newer tau2 writes `<SIM_DIR>/<NAME>/results.json`, older writes `<SIM_DIR>/<NAME>.json`; run_tau2.sh now looks for both |
