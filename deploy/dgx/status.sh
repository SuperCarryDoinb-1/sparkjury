#!/usr/bin/env bash
# Show GPU usage, which endpoints answer, and what is listening. Safe to run any time.
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null || warn "nvidia-smi unavailable"
for spec in "judge_a:$JUDGE_A_PORT" "judge_b:$JUDGE_B_PORT" "embed:$EMBED_PORT" "agent:$AGENT_PORT"; do
  name="${spec%%:*}"; port="${spec##*:}"
  if out="$(curl -fsS "http://127.0.0.1:$port/v1/models" 2>/dev/null)"; then
    printf '  %-8s up   127.0.0.1:%s  %s\n' "$name" "$port" "$(echo "$out" | grep -o '"id":"[^"]*"' | head -1)"
  else
    printf '  %-8s down 127.0.0.1:%s\n' "$name" "$port"
  fi
done
if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then echo "  api      up   0.0.0.0:$API_PORT (public 9030)"; else echo "  api      down 0.0.0.0:$API_PORT"; fi
echo "listening (must be 127.0.0.1 for vLLM, 0.0.0.0 only for $API_PORT):"
ss -tlnp 2>/dev/null | grep -E ":(8001|8002|8003|8004|$API_PORT) " || true
tmux ls 2>/dev/null || true
