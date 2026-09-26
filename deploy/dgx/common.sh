# Shared helpers for the DGX scripts. `source deploy/dgx/common.sh` after cd to the repo root.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/dgx/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a; # shellcheck disable=SC1090
  source "$ENV_FILE"; set +a
fi
: "${MODELS_DIR:=$HOME/models}"
: "${JUDGE_A_MODEL:=Qwen/Qwen3-30B-A3B-Instruct-2507-FP8}"
: "${JUDGE_B_MODEL:=nvidia/Nemotron-3.5-Lightning-30B-A3B-NVFP4}"
: "${EMBED_MODEL:=Qwen/Qwen3-Embedding-0.6B}"
: "${AGENT_MODEL:=Qwen/Qwen3-8B}"
: "${JUDGE_A_PORT:=8001}"; : "${JUDGE_B_PORT:=8002}"; : "${EMBED_PORT:=8003}"; : "${AGENT_PORT:=8004}"; : "${API_PORT:=9000}"
: "${JUDGE_A_MEM:=0.32}"; : "${JUDGE_B_MEM:=0.22}"; : "${EMBED_MEM:=0.03}"; : "${AGENT_MEM:=0.16}"
TMUX_SESSION="sparkjury"
# vLLM: prefer the organisers' preinstalled env (~/envs/vllm, vLLM 0.28 + torch 2.13 on GB10), else our own .venv-vllm
if [[ -x "$HOME/envs/vllm/bin/vllm" ]]; then VLLM_BIN="$HOME/envs/vllm/bin"; else VLLM_BIN="$REPO_ROOT/.venv-vllm/bin"; fi

log() { printf '\033[1;32m[sparkjury]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sparkjury]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[sparkjury]\033[0m %s\n' "$*" >&2; exit 1; }

# local path for a model id: prefer the committee's preloaded dir, then MODELS_DIR, else the id (HF cache)
model_path() {
  local id="$1" base="${1##*/}"
  for cand in "/home/xsuper/models/$base" "/home/xsuper/models/$id" "$MODELS_DIR/$base" "$MODELS_DIR/$id"; do
    [[ -d "$cand" ]] && { echo "$cand"; return; }
  done
  echo "$id"
}

# start a vLLM OpenAI server detached (setsid+nohup, survives tmux/SSH loss); a tmux window tails its log.
# args: name, model id, port, mem fraction, extra args...
vllm_window() {
  local win="$1" model="$2" port="$3" mem="$4"; shift 4
  local path; path="$(model_path "$model")"
  mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/run"
  if curl -fsS "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; then log "$win already up on $port"; return; fi
  ( cd "$REPO_ROOT" && setsid nohup "$VLLM_BIN/vllm" serve "$path" --served-model-name "$model" --host 127.0.0.1 --port "$port" \n      --gpu-memory-utilization "$mem" --dtype auto "$@" >> "logs/$win.log" 2>&1 < /dev/null & echo $! > "run/$win.pid" )
  tmux new-window -t "$TMUX_SESSION" -n "$win" "cd '$REPO_ROOT' && tail -F logs/$win.log"
  log "$win: $model on 127.0.0.1:$port (mem $mem, pid $(cat "$REPO_ROOT/run/$win.pid"))"
}

wait_http() {  # url, seconds
  local url="$1" secs="${2:-600}" i=0
  until curl -fsS "$url" >/dev/null 2>&1; do
    sleep 5; i=$((i+5)); [[ $i -ge $secs ]] && return 1
  done
}
