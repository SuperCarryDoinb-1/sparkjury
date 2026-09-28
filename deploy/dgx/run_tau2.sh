#!/usr/bin/env bash
# Run tau2-bench retail with the agent-under-test on local vLLM, N trials per task, and drop the result
# where SparkJury expects it. The simulated user runs on judge_a's endpoint (bigger model, different weights).
# Usage: bash deploy/dgx/run_tau2.sh [NUM_TASKS=30] [NUM_TRIALS=3] [CONCURRENCY=6] [MAX_STEPS=60]
# Env:   TAU2_HOME (default ~/tau2-bench, the source checkout; tau2 writes results under its data/simulations/)
#        TAU2_BIN / TAU2_PY  tau2 的 venv 在队友主树里时用它指过去（共用节点上很常见）
#        TAU2_DATA_DIR       结果落盘目录（默认 $TAU2_HOME/data）
#        TAU2_TASK_IDS       只跑这几个任务（空格分隔），用于补跑丢了的那几条；设了就不看 NUM_TASKS
# MAX_STEPS bounds runaway conversations (tau2 default 200): an 8B agent that loops otherwise eats 10+ minutes per task.
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
NUM_TASKS="${1:-30}"; NUM_TRIALS="${2:-3}"; CONC="${3:-6}"; MAX_STEPS="${4:-60}"
TAU2_HOME="${TAU2_HOME:-$HOME/tau2-bench}"
TAU2_SIM_DIR="${TAU2_DATA_DIR:-$TAU2_HOME/data}/simulations"
TAU2_BIN="${TAU2_BIN:-$REPO_ROOT/.venv-tau2/bin/tau2}"
# 下面调 patch_tau2_nl_assertions.sh 时它是子进程：不 export 的话它看不到 TAU2_BIN，
# 会退回 ~/sparkjury/.venv-tau2（队友的主树），补丁核对就核在别人的环境上。
export TAU2_BIN TAU2_PY TAU2_HOME

[[ -x "$TAU2_BIN" ]] || die "tau2 not installed at $TAU2_BIN; run deploy/dgx/setup_node.sh"
[[ -d "$TAU2_HOME" ]] || die "tau2-bench checkout not found at $TAU2_HOME (set TAU2_HOME)"
curl -fsS "http://127.0.0.1:$AGENT_PORT/v1/models" >/dev/null || die "agent endpoint 127.0.0.1:$AGENT_PORT is down (start_judges.sh)"
curl -fsS "http://127.0.0.1:$JUDGE_A_PORT/v1/models" >/dev/null || die "judge_a endpoint 127.0.0.1:$JUDGE_A_PORT (user simulator) is down"

# tau2 的评测环节里，NL-assertion 裁判是唯一会调外部 LLM 的一步，模型名在 tau2/config.py 里
# 写死成 gpt-4.1。节点连不上 api.openai.com，于是带 nl_assertions 的任务在对话跑完之后评测抛异常，
# run_with_retry 把整条 simulation（含对话）重跑 4 次，最后存下一条 messages 为空的
# infrastructure_error——9 月 26 日那批因此丢了 26/90 条。这里把它指到本地裁判端点，
# 并在开跑前 import 一次确认真的生效，没生效就 die，不让它静默丢数据。
export OPENAI_API_KEY=EMPTY
export TAU2_LLM_NL_ASSERTIONS="${TAU2_LLM_NL_ASSERTIONS:-openai/${JUDGE_A_MODEL}}"
NL_ARGS_DEFAULT="{\"api_base\": \"http://127.0.0.1:${JUDGE_A_PORT}/v1\", \"temperature\": 0.0, \"response_format\": {\"type\": \"json_object\"}, \"extra_body\": {\"chat_template_kwargs\": {\"enable_thinking\": false}}}"
export TAU2_LLM_NL_ASSERTIONS_ARGS="${TAU2_LLM_NL_ASSERTIONS_ARGS:-${NL_ARGS_DEFAULT}}"
bash deploy/dgx/patch_tau2_nl_assertions.sh ensure
mkdir -p data/simulations logs "$TAU2_SIM_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="retail_${AGENT_MODEL##*/}_trials${NUM_TRIALS}_${STAMP}"
OUT="$REPO_ROOT/data/simulations/$NAME.json"

# litellm routes "openai/<model>" to an OpenAI-compatible base; per-role api_base goes through --*-llm-args.
# --save-to is a NAME: tau2 writes $TAU2_SIM_DIR/<NAME>.json
log "tau2 run: retail, $NUM_TASKS tasks x $NUM_TRIALS trials, agent=$AGENT_MODEL (:$AGENT_PORT), user=$JUDGE_A_MODEL (:$JUDGE_A_PORT)"
# 补跑：TAU2_TASK_IDS="2 3 4 16 19 21 24 28 29" 只跑这 9 个任务。任务号是 tasks.json 里的 id，不是下标。
TASK_ARGS=(--num-tasks "$NUM_TASKS")
if [[ -n "${TAU2_TASK_IDS:-}" ]]; then
  # shellcheck disable=SC2206
  TASK_ARGS=(--task-ids ${TAU2_TASK_IDS})
  log "只跑指定任务：${TAU2_TASK_IDS}（TAU2_TASK_IDS，忽略 NUM_TASKS=${NUM_TASKS}）"
fi

log "eval NL-assertion judge: $TAU2_LLM_NL_ASSERTIONS (本地端点，见 patch_tau2_nl_assertions.sh)"
( cd "$TAU2_HOME" && "$TAU2_BIN" run \
  --domain retail \
  --agent-llm "openai/$AGENT_MODEL" \
  --agent-llm-args "{\"api_base\": \"http://127.0.0.1:$AGENT_PORT/v1\", \"temperature\": 0.0}" \
  --user-llm "openai/$JUDGE_A_MODEL" \
  --user-llm-args "{\"api_base\": \"http://127.0.0.1:$JUDGE_A_PORT/v1\", \"temperature\": 0.7}" \
  "${TASK_ARGS[@]}" --num-trials "$NUM_TRIALS" --max-concurrency "$CONC" --max-steps "$MAX_STEPS" \
  --save-to "$NAME" 2>&1 | tee -a "$REPO_ROOT/logs/tau2.log" )

# 结果落盘路径要看 tau2 版本：新版写 <SIM_DIR>/<NAME>/results.json，老版写 <SIM_DIR>/<NAME>.json。
# 只认老路径的话，一批跑完几个小时，脚本会在这里 die「produced no results file」——数据其实好好地躺在
# 隔壁目录里（9/26 那批就是 <NAME>/results.json）。先找自己这一批，再兜底取任意一份最新的。
shopt -s nullglob
CANDS=("$TAU2_SIM_DIR/$NAME/results.json" "$TAU2_SIM_DIR/$NAME.json" "$TAU2_SIM_DIR"/*/results.json "$TAU2_SIM_DIR"/*.json)
shopt -u nullglob
SRC=""
for c in "${CANDS[@]}"; do [[ -f "$c" ]] && { SRC="$c"; break; }; done
[[ -n "$SRC" ]] || die "tau2 produced no results file under $TAU2_SIM_DIR (looked for $NAME/results.json and $NAME.json)"
log "tau2 results: $SRC"
cp "$SRC" "$OUT"
log "results: $OUT"
uv run sparkjury ingest --path "$OUT" --source tau2 --db "runs/tau2-${STAMP}/sparkjury.db"
uv run sparkjury stats --db "runs/tau2-${STAMP}/sparkjury.db"
log "next: uv run sparkjury run --config deploy/run.toml   (set [[inputs]] path = \"$OUT\")"
