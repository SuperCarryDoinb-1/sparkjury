#!/usr/bin/env bash
# Unattended end-to-end loop for the demo, meant to run in tmux overnight:
#   wait for the baseline tau2 run -> evaluate it with the real judges (A)
#   -> apply the prompt fix -> re-run tau2 (B) -> evaluate B -> regress A vs B -> bundle both -> revert the fix
# Usage: bash deploy/dgx/full_loop.sh [NUM_TASKS=30] [NUM_TRIALS=3] [CONCURRENCY=6] [MAX_STEPS=60]
# Progress markers are written to logs/full_loop.log (LOOP_STEP ..., LOOP_DONE / LOOP_FAILED).
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
export PATH="$HOME/.local/bin:$PATH"
NUM_TASKS="${1:-30}"; NUM_TRIALS="${2:-3}"; CONC="${3:-6}"; MAX_STEPS="${4:-60}"
LOG="$REPO_ROOT/logs/full_loop.log"
mkdir -p logs
step() { log "LOOP_STEP $*"; echo "LOOP_STEP $(date +%H:%M) $*" >> "$LOG"; }
fail() { warn "LOOP_FAILED $*"; echo "LOOP_FAILED $(date +%H:%M) $*" >> "$LOG"; exit 1; }
set -a; source deploy/dgx/.env 2>/dev/null; set +a

make_cfg() {  # run_id, results json, title -> deploy/run.<run_id>.toml (panel/arbiter/cluster copied from run.node.toml)
  local rid="$1" json="$2" title="$3" out="deploy/run.$1.toml"
  {
    printf 'run_id = "%s"\ndb = "runs/%s/sparkjury.db"\nreset_db = true\n[[inputs]]\npath = "%s"\nsource = "tau2"\n' "$rid" "$rid" "$json"
    awk '/^\[panel\]/{p=1} p' deploy/run.node.toml | sed '/^\[report\]/,$d'
    printf '[report]\ntitle = "%s"\n' "$title"
  } > "$out"
  echo "$out"
}

evaluate() {  # run_id, json, title
  local cfg; cfg="$(make_cfg "$1" "$2" "$3")"
  step "evaluate $1 from $2"
  uv run sparkjury run --config "$cfg" --quiet > "logs/run-$1.log" 2>&1 || fail "evaluation $1 failed (see logs/run-$1.log)"
  grep -E "recommendation" "logs/run-$1.log" | head -1 | cut -c1-200
}

# 1. wait for the baseline tau2 run started by run_tau2.sh (tmux tau2full)
step "waiting for baseline tau2 run (logs/tau2full.log FULL_DONE)"
until grep -q "FULL_DONE" logs/tau2full.log 2>/dev/null; do sleep 60; done
A_JSON="$(ls -t data/simulations/*.json 2>/dev/null | head -1)"
[[ -f "$A_JSON" ]] || fail "no baseline results json in data/simulations/"
step "baseline results: $A_JSON"

# 2. evaluate baseline
evaluate tau2-baseline "$A_JSON" "Retail agent Qwen3-8B, baseline, 30 tasks x 3 trials"

# 3. apply the prompt fix and re-run tau2
bash deploy/dgx/apply_prompt_fix.sh apply || fail "apply fix"
step "tau2 run B with fix v1 ($NUM_TASKS x $NUM_TRIALS)"
bash deploy/dgx/run_tau2.sh "$NUM_TASKS" "$NUM_TRIALS" "$CONC" "$MAX_STEPS" > logs/tau2fix.log 2>&1 || { bash deploy/dgx/apply_prompt_fix.sh revert; fail "tau2 run B failed (logs/tau2fix.log)"; }
B_JSON="$(ls -t data/simulations/*.json 2>/dev/null | head -1)"
[[ "$B_JSON" != "$A_JSON" && -f "$B_JSON" ]] || { bash deploy/dgx/apply_prompt_fix.sh revert; fail "no results json for run B"; }
bash deploy/dgx/apply_prompt_fix.sh revert
step "run B results: $B_JSON"

# 4. evaluate B and compare
evaluate tau2-fix-v1 "$B_JSON" "Retail agent Qwen3-8B, prompt fix v1, 30 tasks x 3 trials"
step "regress baseline vs fix v1"
uv run sparkjury regress --before runs/tau2-baseline/sparkjury.db --after runs/tau2-fix-v1/sparkjury.db --pairwise mock --out runs/regress_baseline_vs_fix_v1.md > logs/regress.log 2>&1 || fail "regress"
head -3 logs/regress.log | cut -c1-160

# 5. bundles for offline replay
bash deploy/dgx/make_demo_bundle.sh tau2-baseline >> "$LOG" 2>&1 || true
bash deploy/dgx/make_demo_bundle.sh tau2-fix-v1 >> "$LOG" 2>&1 || true
step "done"
echo "LOOP_DONE $(date +%H:%M)" >> "$LOG"
