#!/usr/bin/env bash
# The "change one thing, re-run, compare" step of the loop.
# tau2's llm_agent takes the domain policy as its system prompt, so the fix is a short block appended to
# data/tau2/domains/retail/policy.md in the tau2-bench checkout. Keeps a backup; `revert` restores it.
# Usage: bash deploy/dgx/apply_prompt_fix.sh apply|revert|show
cd "$(dirname "$0")/../.." && source deploy/dgx/common.sh
TAU2_HOME="${TAU2_HOME:-$HOME/tau2-bench}"
POLICY="$(ls "$TAU2_HOME"/data/tau2/domains/retail/policy*.md 2>/dev/null | head -1)"
[[ -f "$POLICY" ]] || die "retail policy not found under $TAU2_HOME/data/tau2/domains/retail/"
BACKUP="$POLICY.sparkjury.orig"
MARK="<!-- sparkjury-fix-v1 -->"

FIX=$(cat <<'EOF'

<!-- sparkjury-fix-v1 -->
## Mandatory operating rules (added after SparkJury evidence card, fix v1)

These three rules address the failure clusters found in the evaluation: wrong tool for the intent, actions before
identity verification, and destructive actions without explicit confirmation.

1. Before calling ANY tool that changes data (cancel, modify, return, exchange), you MUST already have the user's
   verified user_id from find_user_id_by_email or find_user_id_by_name_zip. Never modify anything for an unverified user.
2. Distinguish the target of an address change: "the address on order #X" -> modify_pending_order_address;
   "my address / my profile / default address" -> modify_user_address. When unsure, ask which one before acting.
   If the user says the change was wrong, do NOT repeat the same call; read the record again and ask.
3. Before every data-changing call, state in one sentence exactly what you will change and wait for the user to
   answer "yes". Only proceed on an explicit yes; anything else is not consent.
EOF
)

case "${1:-show}" in
  apply)
    if grep -q "$MARK" "$POLICY"; then log "fix already applied to $POLICY"; exit 0; fi
    cp -n "$POLICY" "$BACKUP"
    printf '%s\n' "$FIX" >> "$POLICY"
    log "applied fix v1 to $POLICY (backup: $BACKUP)"
    ;;
  revert)
    [[ -f "$BACKUP" ]] || die "no backup at $BACKUP"
    cp "$BACKUP" "$POLICY" && log "restored $POLICY from backup"
    ;;
  show)
    echo "policy: $POLICY"; grep -q "$MARK" "$POLICY" && echo "fix v1: APPLIED" || echo "fix v1: not applied"
    ;;
  *) die "usage: apply_prompt_fix.sh apply|revert|show" ;;
esac
