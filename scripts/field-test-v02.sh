#!/usr/bin/env bash
#
# Run the v0.2.0 real-agent field test (M61).
#
# Brings up the stack on the local model, seeds tools, then drives the Tier 1
# workloads and every supported scenario (S1-S25) plus the post-remediation
# regressions (S26-S31) and harness checks (H1-H5) through
# scripts/field_test_runner_v02.py, writing raw evidence to
# field_test/v0.2.0/results/ and a report to
# docs/field-test/v0.2.0/FIELD_TEST_REPORT.md.
#
# This is the REAL-AGENT track; the container/API/UI layers are covered by
# scripts/docker-test-v02.sh and are not re-run here.
#
# Usage: scripts/field-test-v02.sh [--keep] [--no-build] [--auth] [--priced] [--env-file FILE] [--only S1,S26]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   API_PORT / UI_PORT               Host ports (default: 8100 / 3001)
#   POSTGRES_PORT / REDIS_PORT / ... Override host port mappings
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

version_dir="v0.2.0"
base_url="${HIVEPLANE_MODEL__BASE_URL:-http://127.0.0.1:8000/v1}"
base_url="${base_url%/}"
api_port="${API_PORT:-8100}"
ui_port="${UI_PORT:-3001}"
env_file=".env.local"

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
export HIVEPLANE_API_URL="${HIVEPLANE_API_URL:-http://localhost:${api_port}}"
export HIVEPLANE_UI_URL="${HIVEPLANE_UI_URL:-http://localhost:${ui_port}}"
# Tag the image with the source revision so H1 can detect a stale build.
export GIT_SHA="${GIT_SHA:-$(git rev-parse HEAD 2>/dev/null || echo "")}"
# HMAC secrets so the S6/S27 webhook ingest can be signed.
export HIVEPLANE_TRIGGERS__SECRETS="${HIVEPLANE_TRIGGERS__SECRETS:-{\"ft-wh\":\"ft-wh-secret\",\"ft-s27\":\"ft-s27-secret\",\"ft-dlq\":\"ft-dlq-secret\"}}"
export HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL="${HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL:-http://webhook-sink:8081/slack}"
export HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL="${HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL:-http://webhook-sink:8081/webhook}"
# S23 exercises GitOps delete -> deregister, so destructive reconcile actions are allowed
# for the field run (confirmed per apply).
export HIVEPLANE_RECONCILE__ALLOW_DESTRUCTIVE="${HIVEPLANE_RECONCILE__ALLOW_DESTRUCTIVE:-true}"

compose=(docker compose --env-file "$env_file" --profile local --profile test)
if [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
  export PATH="$repo_root/.venv/bin:$PATH"
else
  python_bin="$(command -v python3)"
fi

results_dir="$repo_root/field_test/${version_dir}/results"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="$repo_root/docs/field-test/${version_dir}/FIELD_TEST_REPORT.md"
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
auth=0
priced=0
only=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) keep=1; shift ;;
    --no-build) build_flag=""; shift ;;
    --auth) auth=1; shift ;;
    --priced) priced=1; shift ;;
    --env-file) env_file="$2"; shift 2 ;;
    --only) only="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ "$auth" -eq 1 ]]; then
  export HIVEPLANE_AUTH__ENABLED="true"
  export HIVEPLANE_AUTH__ADMIN_KEY="${HIVEPLANE_AUTH__ADMIN_KEY:-hp-field-test-bootstrap-admin}"
  export HIVEPLANE_API__RATE_LIMIT_ENABLED="true"
  export HIVEPLANE_API__RATE_LIMIT_REQUESTS="${HIVEPLANE_API__RATE_LIMIT_REQUESTS:-50}"
  export HIVEPLANE_API__RATE_LIMIT_WINDOW_SECONDS="${HIVEPLANE_API__RATE_LIMIT_WINDOW_SECONDS:-60}"
fi
if [[ "$priced" -eq 1 ]]; then
  # Price the local model and drop the zero-cost prefix so budget/velocity guards
  # see real spend (S13, S21). The default profile stays zero-cost.
  export HIVEPLANE_BUDGET__PRICES="${HIVEPLANE_BUDGET__PRICES:-{\"omlx/qwen3-4b-instruct-2507/4bit\":{\"input_per_1k\":0.01,\"output_per_1k\":0.01}}}"
  export HIVEPLANE_BUDGET__ZERO_COST_PREFIXES="${HIVEPLANE_BUDGET__ZERO_COST_PREFIXES:-[]}"
fi

mkdir -p "$results_dir"
printf '%s\n' "$$" > "$pid_file"
log "==> HivePlane v0.2.0 field test run $run_id"
log "==> Results: $results_dir"

set_status "preflight"
if [[ "${HIVEPLANE_MODEL__PROVIDER:-local}" == "fake" ]]; then
  log "==> Preflight: fake replay provider (no local LLM required)"
else
  log "==> Preflight: local LLM at ${base_url}"
  if ! curl -fsS --max-time 5 "${base_url}/models" >/dev/null 2>&1; then
    log "FATAL: local LLM unreachable at ${base_url} — failure, not a skip."
    exit 1
  fi
fi

set_status "resetting stack"
"${compose[@]}" down -v >/dev/null 2>&1 || true

set_status "bringing up stack"
"${compose[@]}" up -d ${build_flag} > "$results_dir/compose-up.log" 2>&1 || {
  tail -n 50 "$results_dir/compose-up.log" >&2 || true
  exit 1
}

set_status "waiting for readyz"
deadline=$((SECONDS + 300))
until curl -fsS "http://localhost:${api_port}/readyz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    log "FATAL: control plane never became ready"
    "${compose[@]}" logs --no-color > "$results_dir/compose.log" 2>&1 || true
    exit 1
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ > "$heartbeat_file"
  sleep 3
done

set_status "setup (seed tools + register workloads)"
bash "$repo_root/scripts/seed-tools.sh" "$repo_root/examples/workloads" \
  > "$results_dir/seed-tools.log" 2>&1 || true
bash "$repo_root/scripts/seed-tools.sh" "$repo_root/field_test/workloads" \
  >> "$results_dir/seed-tools.log" 2>&1 || true
if [[ -x "$repo_root/scripts/field-test-setup.sh" ]]; then
  bash "$repo_root/scripts/field-test-setup.sh" --api-url "http://localhost:${api_port}" \
    > "$results_dir/setup.log" 2>&1 || true
fi

set_status "running scenarios"
export HIVEPLANE_FIELD_RESTART_CMD="${HIVEPLANE_FIELD_RESTART_CMD:-${compose[*]} restart api}"

set +e
"$python_bin" -u "$repo_root/scripts/field_test_runner_v02.py" \
  --api-url "$HIVEPLANE_API_URL" --results-dir "$results_dir" \
  ${only:+--only "$only"} 2>&1 | tee "$results_dir/scenarios.log"
runner_exit=${PIPESTATUS[0]}
set -e

set_status "rendering report"
# Never clobber the curated FIELD_TEST_REPORT.md. Render the machine summary beside
# the raw evidence; the curated narrative report is edited by hand from that summary.
generated_report="$results_dir/report.generated.md"
set +e
"$python_bin" "$repo_root/scripts/field_test_report_v02.py" \
  --summary "$results_dir/summary.json" --output "$generated_report" 2>&1 | tee -a "$results_dir/run.log"
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
log "==> Machine summary: $generated_report"
log "==> Curated report (not overwritten): $report_path"
