#!/usr/bin/env bash
# Run tau2-bench retail with the agent-under-test on local vLLM, N trials per task, and drop the result
# where SparkJury expects it. The simulated user runs on judge_a's endpoint (bigger model, different weights).
# Usage: bash deploy/dgx/run_tau2.sh [NUM_TASKS=30] [NUM_TRIALS=3] [CONCURRENCY=6] [MAX_STEPS=60]
# Env:   TAU2_HOME (default ~/tau2-bench, the source checkout; tau2 writes results under its data/simulations/)
# MAX_STEPS bounds runaway conversations (tau2 default 200): an 8B agent that loops otherwise eats 10+ minutes per task.
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
NUM_TASKS="${1:-30}"; NUM_TRIALS="${2:-3}"; CONC="${3:-6}"; MAX_STEPS="${4:-60}"
TAU2_HOME="${TAU2_HOME:-$HOME/tau2-bench}"
TAU2_SIM_DIR="${TAU2_DATA_DIR:-$TAU2_HOME/data}/simulations"
TAU2_BIN="$REPO_ROOT/.venv-tau2/bin/tau2"

[[ -x "$TAU2_BIN" ]] || die "tau2 not installed at $TAU2_BIN; run deploy/dgx/setup_node.sh"
[[ -d "$TAU2_HOME" ]] || die "tau2-bench checkout not found at $TAU2_HOME (set TAU2_HOME)"
curl -fsS "http://127.0.0.1:$AGENT_PORT/v1/models" >/dev/null || die "agent endpoint 127.0.0.1:$AGENT_PORT is down (start_judges.sh)"
curl -fsS "http://127.0.0.1:$JUDGE_A_PORT/v1/models" >/dev/null || die "judge_a endpoint 127.0.0.1:$JUDGE_A_PORT (user simulator) is down"

export OPENAI_API_KEY=EMPTY
mkdir -p data/simulations logs "$TAU2_SIM_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="retail_${AGENT_MODEL##*/}_trials${NUM_TRIALS}_${STAMP}"
OUT="$REPO_ROOT/data/simulations/$NAME.json"

# litellm routes "openai/<model>" to an OpenAI-compatible base; per-role api_base goes through --*-llm-args.
# --save-to is a NAME: tau2 writes $TAU2_SIM_DIR/<NAME>.json
log "tau2 run: retail, $NUM_TASKS tasks x $NUM_TRIALS trials, agent=$AGENT_MODEL (:$AGENT_PORT), user=$JUDGE_A_MODEL (:$JUDGE_A_PORT)"
( cd "$TAU2_HOME" && "$TAU2_BIN" run \
  --domain retail \
  --agent-llm "openai/$AGENT_MODEL" \
  --agent-llm-args "{\"api_base\": \"http://127.0.0.1:$AGENT_PORT/v1\", \"temperature\": 0.0}" \
  --user-llm "openai/$JUDGE_A_MODEL" \
  --user-llm-args "{\"api_base\": \"http://127.0.0.1:$JUDGE_A_PORT/v1\", \"temperature\": 0.7}" \
  --num-tasks "$NUM_TASKS" --num-trials "$NUM_TRIALS" --max-concurrency "$CONC" --max-steps "$MAX_STEPS" \
  --save-to "$NAME" 2>&1 | tee -a "$REPO_ROOT/logs/tau2.log" )

SRC="$TAU2_SIM_DIR/$NAME.json"
[[ -f "$SRC" ]] || SRC="$(ls -t "$TAU2_SIM_DIR"/*.json 2>/dev/null | head -1)"
[[ -f "$SRC" ]] || die "tau2 produced no results file under $TAU2_SIM_DIR"
cp "$SRC" "$OUT"
log "results: $OUT"
uv run sparkjury ingest --path "$OUT" --source tau2 --db "runs/tau2-${STAMP}/sparkjury.db"
uv run sparkjury stats --db "runs/tau2-${STAMP}/sparkjury.db"
log "next: uv run sparkjury run --config deploy/run.toml   (set [[inputs]] path = \"$OUT\")"
