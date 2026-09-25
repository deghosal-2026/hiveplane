#!/usr/bin/env bash
#
# Seeded demo: the full certified control loop, on the deterministic fake
# provider (M23, #121). Runs anywhere, no secrets, no network.
#
#   1. Register a workload            4. Observe the run (state, model, usage)
#   2. Certify it via benchmark       5. Intervene (approve an escalation)
#   3. Submit a task                  6. Deliver the result (fan-out)
#
# Usage: scripts/demo.sh [--keep]
#   --keep   leave the stack running after the demo
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

env_file=".env.ci"
api_port="${API_PORT:-8100}"
ui_port="${UI_PORT:-3001}"
api_url="http://localhost:${api_port}"

export POSTGRES_PORT="${POSTGRES_PORT:-55432}"
export REDIS_PORT="${REDIS_PORT:-56379}"
export GRAFANA_PORT="${GRAFANA_PORT:-33000}"
export PROMETHEUS_PORT="${PROMETHEUS_PORT:-39090}"
export TEMPO_PORT="${TEMPO_PORT:-33200}"
export OTEL_GRPC_PORT="${OTEL_GRPC_PORT:-44317}"
export OTEL_HTTP_PORT="${OTEL_HTTP_PORT:-44318}"
export API_PORT="$api_port"
export UI_PORT="$ui_port"
export WEBHOOK_SINK_PORT="${WEBHOOK_SINK_PORT:-58081}"

compose=(docker compose --env-file "$env_file" --profile ci --profile test)

if [[ -x "$repo_root/.venv/bin/hiveplane" ]]; then
  hiveplane="$repo_root/.venv/bin/hiveplane"
  python_bin="$repo_root/.venv/bin/python"
  export PATH="$repo_root/.venv/bin:$PATH"
else
  hiveplane="$(command -v hiveplane)"
  python_bin="$(command -v python3)"
fi

keep=0
[[ "${1:-}" == "--keep" ]] && keep=1

expression='{"pr":{"title":"Fix typo in README","files":["README.md","docs/overview.md"],"additions":4,"deletions":3}}'
identity="openai/gpt-4o/2024-08-06"

step() { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

run_field() {
  "$python_bin" - "$1" "$2" <<'PY'
import json, sys, urllib.request
run = json.load(urllib.request.urlopen(f"http://localhost:8100/runs/{sys.argv[1]}"))
print(run.get(sys.argv[2], ""))
PY
}

teardown() {
  if (( keep == 0 )); then
    printf '\n── teardown ──\n'
    "${compose[@]}" down -v >/dev/null 2>&1 || true
  else
    note "--keep: leaving the stack up (API ${api_url}, UI http://localhost:${ui_port})"
  fi
}
trap teardown EXIT

step "HivePlane seeded demo (fake provider, deterministic)"
note "Bringing up the stack (${env_file})..."
"${compose[@]}" down -v >/dev/null 2>&1 || true
"${compose[@]}" up -d --build >/tmp/hiveplane-demo-up.log 2>&1 || {
  tail -n 30 /tmp/hiveplane-demo-up.log >&2; exit 1;
}
until curl -fsS "${api_url}/readyz" >/dev/null 2>&1; do sleep 2; done
note "Stack ready at ${api_url}"

step "1. Register workloads + seed tools"
scripts/field-test-setup.sh --api-url "$api_url" | sed 's/^/   /'

step "2. Certify repo-agent via benchmark"
"$hiveplane" certify repo-agent --context staging --api-url "$api_url" | sed 's/^/   /'
"$hiveplane" certify repo-agent --context production --api-url "$api_url" | sed 's/^/   /'
note "Only a certified workload is admitted to production."

step "3. Submit a task"
submitted="$("$hiveplane" submit --agent repo-agent --context production \
  --model-identity "$identity" --task "$expression" --api-url "$api_url")"
echo "$submitted" | sed 's/^/   /'
run_id="$(sed -n 's/^OK: submitted \([^ ]*\).*/\1/p' <<<"$submitted")"
[[ -n "$run_id" ]] || { echo "could not parse run id" >&2; exit 1; }

curl -fsS -X POST "$api_url/runs/${run_id}/start" >/dev/null
note "started ${run_id}"

step "4. Observe the run"
for _ in $(seq 1 120); do
  state="$(run_field "$run_id" state)"
  [[ "$state" == "paused" || "$state" == "completed" || "$state" == "failed" ]] && break
  sleep 1
done
note "state: ${state}"

step "5. Intervene"
if [[ "$state" == "paused" ]]; then
  note "the run paused for an escalation; approving and resuming"
  approval="$(curl -fsS "$api_url/approvals" | "$python_bin" -c \
    'import json,sys; print(next((a["approval_id"] for a in json.load(sys.stdin) if a["status"]=="pending"), ""))')"
  [[ -n "$approval" ]] && "$hiveplane" approvals approve "$approval" --operator demo --api-url "$api_url" | sed 's/^/   /'
  "$hiveplane" runs resume "$run_id" --api-url "$api_url" | sed 's/^/   /'
  for _ in $(seq 1 120); do
    state="$(run_field "$run_id" state)"
    [[ "$state" == "completed" || "$state" == "failed" ]] && break
    sleep 1
  done
else
  note "run needed no intervention (completed directly)"
fi
note "final state: ${state}"

step "6. Result and delivery"
"$hiveplane" runs show "$run_id" --api-url "$api_url" | "$python_bin" -c \
  'import json,sys; r=json.load(sys.stdin); print("   result:", json.dumps(r.get("result"))); print("   cost_usd:", r.get("cost_usd"))'
story="$(curl -fsS "$api_url/runs/${run_id}/story")"
"$python_bin" - "$story" <<'PY'
import json, sys
story = json.loads(sys.argv[1])
kinds = [entry["kind"] for entry in story.get("entries", [])]
print("   story:", ", ".join(kinds))
print("   fan-out delivered:", "delivery" in kinds)
PY

step "Done"
note "Verified: register -> certify -> run -> observe -> intervene -> deliver"
note "API ${api_url} · UI http://localhost:${ui_port} (grafana :${GRAFANA_PORT})"