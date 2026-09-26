#!/usr/bin/env bash
# Sign every skill with an OMS (model_signing) detached signature, as required by the NVIDIA/skills registry.
# Prerequisites (done by the skill-library owner):
#   pip install model-signing
#   a signing certificate + key issued under the NVIDIA agent root (nv-agent-root-cert.pem)
# Usage: scripts/sign_skills.sh <cert.pem> <key.pem> [nv-agent-root-cert.pem]
set -euo pipefail
CERT="${1:?signing certificate}"; KEY="${2:?private key}"; ROOT="${3:-nv-agent-root-cert.pem}"
cd "$(dirname "$0")/../skills"
for d in sparkjury-*/; do
  d="${d%/}"
  echo "signing $d"
  model_signing sign certificate "$d" --signature "$d/skill.oms.sig" --signing_certificate "$CERT" --private_key "$KEY"
  model_signing verify certificate "$d" --signature "$d/skill.oms.sig" --certificate_chain "$ROOT"
done
echo "all skills signed and verified"
