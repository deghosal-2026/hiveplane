#!/usr/bin/env bash
#
# Field-test setup (M23, #99): bring a stack to "ready to certify".
#
# Seeds the MCP tool registry with every tool referenced by the workload
# manifests, then registers the three Tier 1 field-test workloads plus the
# negative variants used by the certification/governance scenarios. Idempotent:
# already-registered workloads are left alone (the API returns 409).
#
# Usage: scripts/field-test-setup.sh [--api-url URL] [--workloads-dir DIR]
#
# Prerequisite: the stack is up (scripts/field-test.sh brings it up, or
# `docker compose --env-file .env.local --profile local --profile test up -d`).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

api_url="http://localhost:8100"
workloads_dir="$repo_root/field_test/workloads"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url) api_url="$2"; shift 2 ;;
    --workloads-dir) workloads_dir="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -x "$repo_root/.venv/bin/hiveplane" ]]; then
  hiveplane="$repo_root/.venv/bin/hiveplane"
else
  hiveplane="$(command -v hiveplane)"
fi

log() { echo "$@" ; }

log "==> Seeding MCP tool registry"
"$repo_root/scripts/seed-tools.sh" "$workloads_dir"

workloads=(support-agent eval-judge uncertified-agent model-swap-agent regressed-agent)
log "==> Registering workloads (api: ${api_url})"
failed=0
for name in "${workloads[@]}"; do
  manifest="$workloads_dir/${name}.yaml"
  if [[ ! -f "$manifest" ]]; then
    log "    SKIP  ${name} (no manifest at ${manifest})"
    continue
  fi
  output="$("$hiveplane" register "$manifest" --api-url "$api_url" 2>&1)" || true
  if grep -qi "already" <<<"$output" || grep -qi "409" <<<"$output" || grep -qi "registered" <<<"$output"; then
    log "    OK    ${name}"
  else
    log "    FAIL  ${name}: ${output}"
    failed=1
  fi
done

if (( failed != 0 )); then
  log "!! setup incomplete"
  exit 1
fi
log "==> Setup complete: three Tier 1 workloads + negative variants registered, tools seeded"
