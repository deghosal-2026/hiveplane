#!/usr/bin/env bash
#
# Run the HivePlane container-layer test suite (M23, #93/#94).
#
# This suite runs against REAL local inference. It must FAIL — never skip — when
# the local LLM is unavailable. The preflight below enforces that.
#
# All logs and artifacts are written to field_test/v0.1.0/docker/ (one
# consistent directory, overwritten each run) and checked in as release-gate
# evidence. A detailed report is written to
# docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md.
#
# Usage: scripts/docker-test.sh [--no-build] [<extra pytest args>]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   HIVEPLANE_MODEL__DEFAULT_MODEL   Model id to probe (optional; discovered otherwise)
#   API_PORT                         Host port for the control plane (default: 8100)
#   POSTGRES_PORT / REDIS_PORT / ... Override host port mappings (defaults avoid
#                                    common local dev services)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

base_url="${HIVEPLANE_MODEL__BASE_URL:-http://127.0.0.1:8000/v1}"
base_url="${base_url%/}"
api_port="${API_PORT:-8100}"
ui_port="${UI_PORT:-3001}"
env_file=".env.local"

# Resolve the application model identity and its served alias from the env file
# so the preflight probes the model the stack actually uses (not merely the
# alphabetically-first served model) — see DOCKER_TEST_REPORT.md §2 alignment.
app_model=""
alias_map="{}"
if [[ -f "$repo_root/$env_file" ]]; then
  app_model="$(sed -n 's/^HIVEPLANE_MODEL__DEFAULT_MODEL=//p' "$repo_root/$env_file" | tail -n1)"
  alias_map="$(sed -n 's/^HIVEPLANE_MODEL__MODEL_ALIASES=//p' "$repo_root/$env_file" | tail -n1)"
fi
app_model="${HIVEPLANE_MODEL__DEFAULT_MODEL:-$app_model}"
app_model="${app_model:-local-model}"

# Host port mappings: defaults steer clear of services commonly already running
# on a dev machine (e.g. a native Postgres on 5432). Containers talk to each
# other on their internal ports, so only external access is affected.
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

# --------------------------------------------------------------------------- #
# Evidence directory (one consistent location, overwritten each run)
# --------------------------------------------------------------------------- #
log_root="$repo_root/field_test/v0.1.0/docker"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$log_root"
mkdir -p "$run_dir"
find "$run_dir" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} +
report_path="$repo_root/docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md"
pid_file="$run_dir/runner.pid"
status_file="$run_dir/status.txt"
heartbeat_file="$run_dir/heartbeat.txt"
aborted_file="$run_dir/aborted.txt"

log() { echo "$@" | tee -a "$run_dir/run.log"; }

set_status() {
  local status="$1"
  printf '%s\n' "$status" > "$status_file"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$heartbeat_file"
  log "==> Status: $status"
}

touch_heartbeat() {
  date -u +%Y-%m-%dT%H:%M:%SZ > "$heartbeat_file"
}

on_abort() {
  printf 'aborted at %s during %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(cat "$status_file" 2>/dev/null || printf 'unknown')" > "$aborted_file"
  log "!! Runner interrupted; marking run aborted"
  exit 130
}

log "==> HivePlane docker test run $run_id"
log "==> Logs: $run_dir"
printf '%s\n' "$$" > "$pid_file"
set_status "starting"
trap on_abort INT TERM

# --------------------------------------------------------------------------- #
# Preflight: the local LLM is mandatory. Fail fast and loud — no skips.
# Discover a served model and prove it can actually complete.
# --------------------------------------------------------------------------- #
resolved_model=""

probe_local_llm() {
  local models_json probe_model body
  models_json="$(curl -fsS --max-time 5 "${base_url}/models" 2>/dev/null)" || return 1
  probe_model="$(printf '%s' "$models_json" | "$python_bin" -c \
    'import json, sys
app, raw_aliases = sys.argv[1], sys.argv[2]
try:
    aliases = json.loads(raw_aliases or "{}")
except ValueError:
    aliases = {}
data = json.load(sys.stdin).get("data") or []
served = sorted(str(m.get("id", "")) for m in data if m.get("id"))
preferred = sorted(k for k, v in aliases.items() if v == app and k in served)
if preferred:
    print(preferred[0])
elif app in served:
    print(app)
elif served:
    print(served[0])' "$app_model" "$alias_map" 2>/dev/null)" || return 1
  [[ -n "$probe_model" ]] || return 1
  body='{"model":"'"${probe_model}"'","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
  curl -fsS --max-time 60 -H 'Content-Type: application/json' \
    -d "$body" "${base_url}/chat/completions" >/dev/null 2>&1 || return 1
  resolved_model="$probe_model"
}

log "==> Preflight: local LLM at ${base_url}"
set_status "preflight"
if ! probe_local_llm > "$run_dir/preflight.log" 2>&1; then
  cat >&2 <<EOF
!! Local LLM is not reachable or cannot serve a completion at ${base_url}.
!! The docker test suite requires real local inference and does NOT skip.
!! Start OMLX (mlx_lm.server) or point HIVEPLANE_MODEL__BASE_URL at an
!! OpenAI-compatible endpoint, then re-run.
!! Check served models with: curl ${base_url}/models
EOF
  exit 1
fi
log "    local LLM OK (app model: ${app_model}; served: ${resolved_model})"

# --------------------------------------------------------------------------- #
# Run metadata (evidence)
# --------------------------------------------------------------------------- #
HIVEPLANE_RUN_ID="$run_id" \
HIVEPLANE_LOG_DIR="$run_dir" \
HIVEPLANE_REPO_ROOT="$repo_root" \
HIVEPLANE_BASE_URL="$base_url" \
HIVEPLANE_RESOLVED_MODEL="$resolved_model" \
HIVEPLANE_APP_MODEL="$app_model" \
HIVEPLANE_API_PORT="$api_port" \
python_bin="$python_bin" \
"$python_bin" - <<'PY' > "$run_dir/environment.json"
import json, os, platform, subprocess, datetime

def cmd(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""

root = os.environ["HIVEPLANE_REPO_ROOT"]
env = {
    "run_id": os.environ["HIVEPLANE_RUN_ID"],
    "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    "log_dir": os.environ["HIVEPLANE_LOG_DIR"],
    "git_sha": cmd("git", "-C", root, "rev-parse", "HEAD"),
    "git_branch": cmd("git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"),
    "git_dirty": bool(cmd("git", "-C", root, "status", "--porcelain")),
    "platform": platform.platform(),
    "python": platform.python_version(),
    "docker_client": cmd("docker", "--version"),
    "docker_compose": cmd("docker", "compose", "version", "--short"),
    "profile": "local+test",
    "provider": "local",
    "llm_base_url": os.environ["HIVEPLANE_BASE_URL"],
    "llm_model": os.environ.get("HIVEPLANE_APP_MODEL") or os.environ["HIVEPLANE_RESOLVED_MODEL"],
    "llm_served_model": os.environ["HIVEPLANE_RESOLVED_MODEL"],
    "api_url": f"http://localhost:{os.environ['HIVEPLANE_API_PORT']}",
}
print(json.dumps(env, indent=2))
PY

# --------------------------------------------------------------------------- #
# Stack lifecycle
# --------------------------------------------------------------------------- #
capture_and_teardown() {
  local code=$?
  trap - EXIT
  rm -f "$pid_file"
  log "==> Capturing service logs and tearing down"
  "${compose[@]}" logs > "$run_dir/compose.log" 2>&1 || true
  "${compose[@]}" ps > "$run_dir/compose-ps.txt" 2>&1 || true
  "${compose[@]}" down -v > "$run_dir/teardown.log" 2>&1 || true

  log "==> Rendering report"
  set +e
  "$python_bin" "$repo_root/scripts/docker_report.py" \
    --junit "$run_dir/junit.xml" \
    --environment "$run_dir/environment.json" \
    --log-dir "$run_dir" \
    --output "$report_path" 2>&1 | tee -a "$run_dir/run.log"
  local report_exit=${PIPESTATUS[0]}
  set -e
  if (( report_exit != 0 )); then
    code=1
  fi
  if (( code == 0 )); then
    set_status "completed"
  else
    set_status "failed"
  fi
  log "==> Artifacts: $run_dir"
  log "==> Report:    $report_path"
  exit "$code"
}
trap capture_and_teardown EXIT

build_flag="--build"
if [[ "${1:-}" == "--no-build" ]]; then
  build_flag=""
  shift
fi

set_status "resetting stack"
log "==> Resetting stack volumes (fresh database)"
"${compose[@]}" down -v > "$run_dir/compose-reset.log" 2>&1 || true

set_status "bringing up stack"
log "==> Bringing up stack (${env_file}, profiles: local test)"
# Point fan-out at the webhook-sink (test profile) so L4 can assert delivery.
export HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL="${HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL:-http://webhook-sink:8081/slack}"
export HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL="${HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL:-http://webhook-sink:8081/webhook}"
if ! "${compose[@]}" up -d ${build_flag} > "$run_dir/compose-up.log" 2>&1; then
  tail -n 50 "$run_dir/compose-up.log" >&2 || true
  exit 1
fi

set_status "waiting for readyz"
log "==> Waiting for /readyz"
deadline=$((SECONDS + 120))
until curl -fsS "http://localhost:${api_port}/readyz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    log "!! control plane never became ready at http://localhost:${api_port}/readyz"
    exit 1
  fi
  touch_heartbeat
  sleep 2
done >> "$run_dir/wait-ready.log" 2>&1

set_status "seeding tools"
log "==> Seeding tool registry"
scripts/seed-tools.sh > "$run_dir/seed-tools.log" 2>&1

set_status "installing playwright"
log "==> Ensuring Playwright chromium (L3)"
"$python_bin" -m playwright install chromium > "$run_dir/playwright-install.log" 2>&1

set_status "running pytest (L0-L7)"
log "==> Running docker test suite (L0-L7, no skips)"
export HIVEPLANE_MODEL__BASE_URL="$base_url"
export HIVEPLANE_MODEL__DEFAULT_MODEL="$resolved_model"
export HIVEPLANE_API_URL="http://localhost:${api_port}"
export HIVEPLANE_UI_URL="http://localhost:${ui_port}"
export GRAFANA_URL="http://localhost:${GRAFANA_PORT}"
export PROMETHEUS_URL="http://localhost:${PROMETHEUS_PORT}"
export TEMPO_URL="http://localhost:${TEMPO_PORT}"
export HIVEPLANE_DOCKER_RUN_STATUS_FILE="$status_file"
export HIVEPLANE_DOCKER_RUN_HEARTBEAT_FILE="$heartbeat_file"
set +e
"$python_bin" -m pytest tests/docker -m docker \
  -p tests.docker.conftest_progress \
  --junitxml="$run_dir/junit.xml" "$@" 2>&1 | tee "$run_dir/pytest.log"
pytest_exit=${PIPESTATUS[0]}
set -e

exit "$pytest_exit"
