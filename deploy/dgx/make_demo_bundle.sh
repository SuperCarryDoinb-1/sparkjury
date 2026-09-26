#!/usr/bin/env bash
# Package a finished run (db + manifest + events + card) so the demo can be replayed anywhere, offline.
# Usage: bash deploy/dgx/make_demo_bundle.sh <run_id> [out.tar.gz]
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
RUN_ID="${1:?run id}"; OUT="${2:-data/samples/bundles/${RUN_ID}.tar.gz}"
[[ -f "runs/$RUN_ID/manifest.json" ]] || die "runs/$RUN_ID/manifest.json not found"
mkdir -p "$(dirname "$OUT")"
tar czf "$OUT" -C runs "$RUN_ID"
log "bundle: $OUT ($(du -h "$OUT" | cut -f1)). Replay: tar xzf $OUT -C runs && uv run sparkjury serve"
