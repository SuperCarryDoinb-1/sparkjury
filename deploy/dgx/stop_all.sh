#!/usr/bin/env bash
# Stop the tmux session and any stray vLLM / sparkjury processes we started. Never touches other teams' processes.
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null && log "tmux session stopped" || warn "no tmux session"
for f in run/*.pid; do [[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null && log "stopped $(basename "$f" .pid)"; rm -f "$f"; done
pkill -u "$USER" -f "^[^ ]*vllm serve" 2>/dev/null || true
pkill -u "$USER" -f "sparkjury serve" 2>/dev/null && log "api stopped" || true
sleep 2; bash deploy/dgx/status.sh
