#!/usr/bin/env bash
# One-time setup on the DGX Spark node: preflight, uv, project venv, vLLM, tau2-bench.
# Usage: bash deploy/dgx/setup_node.sh            (safe to re-run)
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh

log "== preflight (from the node manual) =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || die "nvidia-smi failed"
df -h "$HOME" | tail -1; free -h | head -2; nproc; python3 -V
[[ -d /home/xsuper/models ]] && { log "preloaded models:"; ls /home/xsuper/models | head -50; } || warn "/home/xsuper/models not found"
command -v docker >/dev/null && docker info >/dev/null 2>&1 && log "docker: ok" || warn "docker not usable (fine, using pip vLLM)"

log "== uv =="
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

log "== sparkjury venv =="
uv sync --python 3.12
uv run sparkjury --version

log "== vLLM (separate venv; GB10/aarch64 needs a CUDA-13 build) =="
if [[ -x "$HOME/envs/vllm/bin/vllm" ]]; then log "using preinstalled vLLM at ~/envs/vllm"; elif [[ ! -d .venv-vllm ]]; then
  uv venv .venv-vllm --python 3.12
fi
# Try the official wheel first; if it fails on this platform, fall back to the NVIDIA container (see README).
if [[ ! -x "$HOME/envs/vllm/bin/vllm" ]] && ! .venv-vllm/bin/python -c "import vllm" 2>/dev/null; then
  VIRTUAL_ENV="$PWD/.venv-vllm" uv pip install "vllm>=0.11" || warn "pip vLLM failed: use the container path in deploy/README.md"
fi
"$VLLM_BIN/python" -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda', torch.cuda.is_available())" || true

log "== tau2-bench (separate venv) =="
if [[ ! -d .venv-tau2 ]]; then
  uv venv .venv-tau2 --python 3.12
  VIRTUAL_ENV="$PWD/.venv-tau2" uv pip install "git+https://github.com/sierra-research/tau2-bench.git"
fi
.venv-tau2/bin/tau2 --help >/dev/null && log "tau2: ok"

loginctl enable-linger "$USER" 2>/dev/null && log "linger enabled (processes survive logout)" || warn "could not enable linger"

log "== env file =="
[[ -f deploy/dgx/.env ]] || { cp deploy/dgx/env.example deploy/dgx/.env; warn "created deploy/dgx/.env from the example: fill in keys and SPARKJURY_API_TOKEN"; }
log "setup done. next: bash deploy/dgx/download_models.sh && bash deploy/dgx/start_judges.sh"
