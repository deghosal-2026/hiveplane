#!/usr/bin/env bash
#
# Run the v0.1.0 real-agent field test (M23, P4, #99).
#
# Brings up the stack on the local model, seeds tools, then drives the three
# Tier 1 workloads through the certified control loop (S1-S9) via
# scripts/field_test_runner.py, writing raw evidence to
# field_test/v0.1.0/results/ and a report to
# docs/field-test/v0.1.0/FIELD_TEST_REPORT.md.
#
# This is the REAL-AGENT track. The container/API/UI layers are covered by the
# completed Docker suite (scripts/docker-test.sh) and are not re-run here.
#
# Usage: scripts/field-test.sh [--keep] [--no-build] [--only S1,S2]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   API_PORT                         Host port for the control plane (default: 8100)
#   POSTGRES_PORT / REDIS_PORT / ... Override host port mappings
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

base_url="${HIVEPLANE_MODEL__BASE_URL:-http://127.0.0.1:8000/v1}"
base_url="${base_url%/}"
api_port="${API_PORT:-8100}"
ui_port="${UI_PORT:-3001}"
env_file=".env.local"

app_model=""
alias_map="{}"
if [[ -f "$repo_root/$env_file" ]]; then
  app_model="$(sed -n 's/^HIVEPLANE_MODEL__DEFAULT_MODEL=//p' "$repo_root/$env_file" | tail -n1)"
  alias_map="$(sed -n 's/^HIVEPLANE_MODEL__MODEL_ALIASES=//p' "$repo_root/$env_file" | tail -n1)"
fi
app_model="${HIVEPLANE_MODEL__DEFAULT_MODEL:-$app_model}"
app_model="${app_model:-local-model}"

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

compose=(docker compose --env-file "$env_file" --profile local --profile test)

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
  export PATH="$repo_root/.venv/bin:$PATH"
else
  python_bin="$(command -v python3)"
fi

results_dir="$repo_root/field_test/v0.1.0/results"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="$repo_root/docs/field-test/v0.1.0/FIELD_TEST_REPORT.md"
status_file="$results_dir/status.txt"
heartbeat_file="$results_dir/heartbeat.txt"
pid_file="$results_dir/runner.pid"

log() { echo "$@" | tee -a "$results_dir/run.log"; }

set_status() {
  printf '%s\n' "$1" > "$status_file"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$heartbeat_file"
  log "==> Status: $1"
}

keep=0
build_flag="--build"
only=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) keep=1; shift ;;
    --no-build) build_flag=""; shift ;;
    --only) only="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$results_dir"
printf '%s\n' "$$" > "$pid_file"
log "==> HivePlane field test run $run_id"
log "==> Results: $results_dir"

probe_local_llm() {
  local models_json probe_model body
  models_json="$(curl -fsS --max-time 5 "${base_url}/models" 2>/dev/null)" || return 1
  probe_model="$(printf '%s' "$models_json" | "$python_bin" -c \
    'import json, sys
app, raw = sys.argv[1], sys.argv[2]
try:
    aliases = json.loads(raw or "{}")
except ValueError:
    aliases = {}
served = sorted(str(m.get("id", "")) for m in (json.load(sys.stdin).get("data") or []) if m.get("id"))
pref = sorted(k for k, v in aliases.items() if v == app and k in served)
print(pref[0] if pref else (app if app in served else (served[0] if served else "")))' \
    "$app_model" "$alias_map" 2>/dev/null)" || return 1
  [[ -n "$probe_model" ]] || return 1
  body='{"model":"'"${probe_model}"'","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
  curl -fsS --max-time 60 -H 'Content-Type: application/json' \
    -d "$body" "${base_url}/chat/completions" >/dev/null 2>&1 || return 1
}

set_status "preflight"
log "==> Preflight: local LLM at ${base_url}"
if ! probe_local_llm; then
  cat >&2 <<EOF
!! Local LLM is not reachable or cannot serve a completion at ${base_url}.
!! The field test runs the real agents on real local inference and does NOT skip.
EOF
  exit 1
fi
log "    local LLM OK (app model: ${app_model})"

set_status "resetting stack"
"${compose[@]}" down -v >/dev/null 2>&1 || true

set_status "bringing up stack"
export HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL="${HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL:-http://webhook-sink:8081/slack}"
export HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL="${HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL:-http://webhook-sink:8081/webhook}"
"${compose[@]}" up -d ${build_flag} > "$results_dir/compose-up.log" 2>&1 || {
  tail -n 50 "$results_dir/compose-up.log" >&2 || true
  exit 1
}

set_status "waiting for readyz"
deadline=$((SECONDS + 180))
until curl -fsS "http://localhost:${api_port}/readyz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    log "!! control plane never became ready"
    exit 1
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ > "$heartbeat_file"
  sleep 2
done

set_status "setup (seed tools + register workloads)"
"$repo_root/scripts/field-test-setup.sh" --api-url "http://localhost:${api_port}" \
  > "$results_dir/setup.log" 2>&1

set_status "running scenarios"
export HIVEPLANE_API_URL="http://localhost:${api_port}"
export HIVEPLANE_FIELD_RESTART_CMD="${compose[*]} restart api"

set +e
"$python_bin" -u "$repo_root/scripts/field_test_runner.py" \
  --api-url "$HIVEPLANE_API_URL" --results-dir "$results_dir" \
  ${only:+--only "$only"} 2>&1 | tee "$results_dir/scenarios.log"
runner_exit=${PIPESTATUS[0]}
set -e

set_status "rendering report"
set +e
"$python_bin" "$repo_root/scripts/field_test_report.py" \
  --results-dir "$results_dir" --output "$report_path" 2>&1 | tee -a "$results_dir/run.log"
report_exit=${PIPESTATUS[0]}
set -e

if (( keep == 0 )); then
  set_status "tearing down"
  "${compose[@]}" down -v > "$results_dir/teardown.log" 2>&1 || true
else
  log "==> --keep: leaving the stack up"
fi

if (( runner_exit != 0 || report_exit != 0 )); then
  set_status "failed"
  exit 1
fi
set_status "completed"
log "==> Report: $report_path"