#!/usr/bin/env bash
#
# Run the HivePlane v0.2.0 container-layer + scenario suite (M61).
#
# Real local inference is mandatory: the preflight below FAILS (never skips) when
# the OpenAI-compatible endpoint is unreachable. All logs/artifacts are written to
# field_test/v0.2.0/docker/ and the report to docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md.
#
# Usage: scripts/docker-test-v02.sh [--no-build] [--keep] [--only S6,S7] [<pytest args>]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   HIVEPLANE_MODEL__DEFAULT_MODEL   Model id the stack binds to (Qwen3-4B-Instruct-2507-4bit)
#   API_PORT / UI_PORT               Host ports (default: 8100 / 3001)
#   HIVEPLANE_AUTH__ENABLED          true (RBAC/tenancy scenarios require it)
#   HIVEPLANE_API__RATE_LIMIT_ENABLED     true (gate 34 / 429 scenario)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

version_dir="v0.2.0"
base_url="${HIVEPLANE_MODEL__BASE_URL:-http://127.0.0.1:8000/v1}"
base_url="${base_url%/}"
api_port="${API_PORT:-8100}"
ui_port="${UI_PORT:-3001}"
env_file=".env.local"
no_build=0
keep=0
auth=0
only=""
extra=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) no_build=1; shift ;;
    --keep) keep=1; shift ;;
    --auth) auth=1; shift ;;
    --only) only="$2"; shift 2 ;;
    *) extra+=("$1"); shift ;;
  esac
done

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
# Observability endpoints the L1 layer polls (host-remapped ports).
export GRAFANA_URL="http://localhost:${GRAFANA_PORT}"
export PROMETHEUS_URL="http://localhost:${PROMETHEUS_PORT}"
export TEMPO_URL="http://localhost:${TEMPO_PORT}"
# Fan-out destinations so deliveries are actually exercised (test profile).
export HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL="${HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL:-http://webhook-sink:8081/slack}"
export HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL="${HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL:-http://webhook-sink:8081/webhook}"
# Auth + rate limiting are enabled only for the dedicated RBAC/tenant pass
# (--auth), because the base layers and the UI assume an unauthenticated plane.
if [[ "$auth" -eq 1 ]]; then
  export HIVEPLANE_AUTH__ENABLED="true"
  export HIVEPLANE_AUTH__ADMIN_KEY="${HIVEPLANE_AUTH__ADMIN_KEY:-hp-field-test-bootstrap-admin}"
  export HIVEPLANE_AUTH__SYSTEM_KEY="${HIVEPLANE_AUTH__SYSTEM_KEY:-hp-field-test-system-admin}"
  export HIVEPLANE_API__RATE_LIMIT_ENABLED="true"
  export HIVEPLANE_API__RATE_LIMIT_REQUESTS="${HIVEPLANE_API__RATE_LIMIT_REQUESTS:-200}"
else
  export HIVEPLANE_AUTH__ENABLED="${HIVEPLANE_AUTH__ENABLED:-false}"
  export HIVEPLANE_API__RATE_LIMIT_ENABLED="${HIVEPLANE_API__RATE_LIMIT_ENABLED:-false}"
fi

compose=(docker compose --env-file "$env_file" --profile local --profile test)
python_bin="$repo_root/.venv/bin/python"; [[ -x "$python_bin" ]] || python_bin="$(command -v python3)"

log_root="$repo_root/field_test/${version_dir}/docker"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$log_root"
mkdir -p "$run_dir"
find "$run_dir" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} +
report_path="$repo_root/docs/field-test/${version_dir}/DOCKER_TEST_REPORT.md"
log() { echo "$@" | tee -a "$run_dir/run.log"; }

# --------------------------------------------------------------------------- #
# Preflight — the local LLM is mandatory (zero-skip policy)
# --------------------------------------------------------------------------- #
log "==> Preflight local LLM at $base_url"
if ! curl -sf "$base_url/models" >"$run_dir/preflight.log" 2>&1; then
  log "FATAL: local LLM unreachable at $base_url — this is a failure, not a skip."
  exit 1
fi

# --------------------------------------------------------------------------- #
# Build + up
# --------------------------------------------------------------------------- #
if [[ "$no_build" -eq 0 ]]; then
  log "==> Build images"
  "${compose[@]}" build 2>&1 | tee "$run_dir/build.log"
fi
log "==> Reset stack volumes (hermetic run)"
"${compose[@]}" down -v >"$run_dir/compose-reset.log" 2>&1 || true
log "==> docker compose up"
"${compose[@]}" up -d 2>&1 | tee "$run_dir/compose-up.log"

# --------------------------------------------------------------------------- #
# Wait for readiness
# --------------------------------------------------------------------------- #
log "==> Wait for /readyz"
deadline=$((SECONDS + 300))
until curl -sf "http://localhost:${api_port}/readyz" >"$run_dir/wait-ready.log" 2>&1; do
  if [[ $SECONDS -ge $deadline ]]; then
    log "FATAL: control plane did not become ready"
    "${compose[@]}" ps >"$run_dir/compose-ps.txt" 2>&1
    "${compose[@]}" logs --no-color >"$run_dir/compose.log" 2>&1 || true
    exit 1
  fi
  sleep 3
done

# The UI is a first-class part of the stack (Playwright runs against it) — it must
# be healthy too, not just the API.
log "==> Wait for UI /healthz"
ui_deadline=$((SECONDS + 300))
until curl -sf "http://localhost:${ui_port}/healthz" >"$run_dir/wait-ui.log" 2>&1; do
  if [[ $SECONDS -ge $ui_deadline ]]; then
    log "FATAL: operator UI did not become healthy"
    "${compose[@]}" ps >"$run_dir/compose-ps.txt" 2>&1
    "${compose[@]}" logs --no-color >"$run_dir/compose.log" 2>&1 || true
    exit 1
  fi
  sleep 3
done
"${compose[@]}" ps >"$run_dir/compose-ps.txt" 2>&1
log "==> Stack healthy (api :${api_port}, ui :${ui_port})"

# --------------------------------------------------------------------------- #
# Seed fixtures (tools are required before any workload can register)
# --------------------------------------------------------------------------- #
log "==> Seed tool registry"
export PATH="$repo_root/.venv/bin:$PATH"
if [[ -f "$repo_root/scripts/seed-tools.sh" ]]; then
  bash "$repo_root/scripts/seed-tools.sh" "$repo_root/examples/workloads" 2>&1 \
    | tee "$run_dir/seed-tools.log" || true
  bash "$repo_root/scripts/seed-tools.sh" "$repo_root/field_test/workloads" 2>&1 \
    | tee -a "$run_dir/seed-tools.log" || true
fi
if [[ -x "$repo_root/scripts/field-test-setup.sh" ]]; then
  bash "$repo_root/scripts/field-test-setup.sh" --api-url "$HIVEPLANE_API_URL" 2>&1 \
    | tee "$run_dir/seed-tenants.log" || true
fi

# --------------------------------------------------------------------------- #
# Container suite (L0-L13) + Playwright UI v2
# --------------------------------------------------------------------------- #
log "==> pytest tests/docker -m docker"
set +e
if [[ "$auth" -eq 1 ]]; then
  # Dedicated RBAC / tenant / 429 pass against an auth-enabled plane.
  "$python_bin" -m pytest tests/docker/test_v02_secrets_tenancy.py -m docker -p no:randomly \
    --junitxml="$run_dir/junit.xml" -ra "${extra[@]+"${extra[@]}"}" 2>&1 | tee "$run_dir/pytest.log"
else
  # Full v0.2.0 container + scenario suite against an unauthenticated plane.
  # The RBAC module runs in the --auth pass. v0.1.0 example-workload modules
  # (test_ui screenshots, test_control_loop, test_governance) are deselected:
  # the v0.2.0 track runs the Tier 1 field-test agents (support-agent/eval-judge)
  # with CORPORA_DIR=field_test, and must not touch v0.1.0 evidence.
  "$python_bin" -m pytest tests/docker -m docker -p no:randomly \
    --deselect tests/docker/test_v02_secrets_tenancy.py \
    --deselect tests/docker/test_ui.py \
    --deselect tests/docker/test_control_loop.py \
    --deselect tests/docker/test_governance.py \
    --junitxml="$run_dir/junit.xml" -ra "${extra[@]+"${extra[@]}"}" 2>&1 | tee "$run_dir/pytest.log"
fi
pytest_rc=${PIPESTATUS[0]:-1}
if [[ "$auth" -eq 0 ]]; then
  log "==> Playwright UI v2 (screenshots -> docs/field-test/${version_dir}/screenshots)"
  "$python_bin" -m playwright install chromium 2>&1 | tee "$run_dir/playwright-install.log" || true
  "$python_bin" -m pytest tests/e2e -m e2e -p no:randomly 2>&1 | tee -a "$run_dir/pytest.log"
  e2e_rc=${PIPESTATUS[0]:-1}
else
  e2e_rc=0
fi
set -e

"${python_bin}" - "$run_dir" "$version_dir" <<'PY'
import json, subprocess, sys, time
run_dir, version_dir = sys.argv[1], sys.argv[2]
def out(cmd):
    try:
        return subprocess.check_output(cmd, text=True).strip()
    except Exception:
        return "unknown"
env = {
    "version": version_dir,
    "run_id": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
    "git_sha": out(["git", "rev-parse", "HEAD"]),
    "docker": out(["docker", "--version"]),
    "model": "Qwen3-4B-Instruct-2507-4bit",
}
open(f"{run_dir}/environment.json", "w").write(json.dumps(env, indent=2))
PY

if [[ -f "$repo_root/scripts/docker_report.py" ]]; then
  "$python_bin" "$repo_root/scripts/docker_report.py" \
    --junit "$run_dir/junit.xml" --environment "$run_dir/environment.json" \
    --log-dir "$run_dir" --output "$report_path" --version-dir "$version_dir" || true
fi
if [[ -f "$repo_root/scripts/field_test_report_v02.py" ]]; then
  # Never clobber the curated FIELD_TEST_REPORT.md; render the generated summary beside
  # the raw evidence (M61-21).
  "$python_bin" "$repo_root/scripts/field_test_report_v02.py" \
    --summary "$repo_root/field_test/${version_dir}/results/summary.json" \
    --output "$repo_root/field_test/${version_dir}/results/report.generated.md" || true
fi

status="passed"
[[ "$pytest_rc" -eq 0 && "$e2e_rc" -eq 0 ]] || status="failed"
printf '%s\n' "$status" > "$run_dir/status.txt"
log "==> Status: $status (pytest=$pytest_rc e2e=$e2e_rc)"

if [[ "$keep" -eq 0 ]]; then
  log "==> docker compose down"
  "${compose[@]}" logs --no-color >"$run_dir/compose.log" 2>&1 || true
  "${compose[@]}" down -v >"$run_dir/teardown.log" 2>&1 || true
fi

[[ "$status" == "passed" ]]
