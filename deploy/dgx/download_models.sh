#!/usr/bin/env bash
# Download model weights ON THE NODE (never scp > 1 GB through the shared uplink; see the node manual).
# Uses ModelScope (fast from China) and skips anything already present in /home/xsuper/models or MODELS_DIR.
# Usage: bash deploy/dgx/download_models.sh [all|judge_a|judge_b|embed|agent]
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
what="${1:-all}"
mkdir -p "$MODELS_DIR"
# modelscope CLI: node vLLM env, our venv, or install into our venv (uv tool install has no entry point for it)
MS="$(command -v modelscope || true)"
for cand in "$HOME/envs/vllm/bin/modelscope" "$REPO_ROOT/.venv/bin/modelscope"; do [[ -z "$MS" && -x "$cand" ]] && MS="$cand"; done
if [[ -z "$MS" ]]; then
  log "installing modelscope into the project venv"
  uv pip install --python "$REPO_ROOT/.venv/bin/python" modelscope >/dev/null && MS="$REPO_ROOT/.venv/bin/modelscope"
fi
[[ -x "$MS" ]] || die "modelscope CLI unavailable"
log "modelscope: $MS"

fetch() {  # model id
  local id="$1" base="${1##*/}"
  local have; have="$(model_path "$id")"
  if [[ "$have" != "$id" ]]; then log "already present: $have"; return; fi
  log "downloading $id -> $MODELS_DIR/$base"
  "$MS" download --model "$id" --local_dir "$MODELS_DIR/$base" \
    || { warn "modelscope failed for $id; trying huggingface (HF_ENDPOINT=${HF_ENDPOINT:-default})"; \
         uv run --with huggingface_hub hf download "$id" --local-dir "$MODELS_DIR/$base"; }
}

case "$what" in
  all)     fetch "$JUDGE_A_MODEL"; fetch "$JUDGE_B_MODEL"; fetch "$EMBED_MODEL"; fetch "$AGENT_MODEL" ;;
  judge_a) fetch "$JUDGE_A_MODEL" ;;
  judge_b) fetch "$JUDGE_B_MODEL" ;;
  embed)   fetch "$EMBED_MODEL" ;;
  agent)   fetch "$AGENT_MODEL" ;;
  *) die "unknown target $what" ;;
esac
df -h "$MODELS_DIR" | tail -1
