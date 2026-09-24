#!/usr/bin/env bash
#
# Run the HivePlane container-layer test suite (M23, #93/#94).
#
# This suite runs against REAL local inference. It must FAIL — never skip — when
# the local LLM is unavailable. The preflight below enforces that.
#
# All logs and artifacts are written to field_test/v0.1.0/docker/<run-id>/ and
# checked in as release-gate evidence. A detailed report is written to
# docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md.
#
# Usage: scripts/docker-test.sh [--no-build] [<extra pytest args>]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   HIVEPLANE_MODEL__DEFAULT_MODEL   Model id to probe (optional; discovered otherwise)
#   API_PORT                         Host port for the control plane (default: 8100)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

base_url="${HIVEPLANE_MODEL__BASE_URL:-http://127.0.0.1:8000/v1}"
base_url="${base_url%/}"
model="${HIVEPLANE_MODEL__DEFAULT_MODEL:-local-model}"
api_port="${API_PORT:-8100}"
env_file=".env.local"
compose=(docker compose --env-file "$env_file" --profile local --profile test)

if [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
else
  python_bin="$(command -v python3)"
fi

# --------------------------------------------------------------------------- #
# Evidence directory
# --------------------------------------------------------------------------- #
log_root="$repo_root/field_test/v0.1.0/docker"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$log_root/$run_id"
mkdir -p "$run_dir"
ln -sfn "$run_id" "$log_root/latest"
report_path="$repo_root/docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md"

log() { echo "$@" | tee -a "$run_dir/run.log"; }

log "==> HivePlane docker test run $run_id"
log "==> Logs: $run_dir"

# --------------------------------------------------------------------------- #
# Preflight: the local LLM is mandatory. Fail fast and loud — no skips.
# Discover a served model and prove it can actually complete.
# --------------------------------------------------------------------------- #
resolved_model="$model"

probe_local_llm() {
  local models_json probe_model body
  models_json="$(curl -fsS --max-time 5 "${base_url}/models" 2>/dev/null)" || return 1
  probe_model="$(printf '%s' "$models_json" | "$python_bin" -c \
    'import json, sys
data = json.load(sys.stdin).get("data") or []
ids = sorted(str(m.get("id", "")) for m in data if m.get("id"))
print(ids[0] if ids else "")' 2>/dev/null)" || return 1
  [[ -n "$probe_model" ]] || return 1
  body='{"model":"'"${probe_model}"'","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
  curl -fsS --max-time 60 -H 'Content-Type: application/json' \
    -d "$body" "${base_url}/chat/completions" >/dev/null 2>&1 || return 1
  resolved_model="$probe_model"
}

log "==> Preflight: local LLM at ${base_url}"
if ! probe_local_llm 2>&1 | tee "$run_dir/preflight.log"; then
  cat >&2 <<EOF
!! Local LLM is not reachable or cannot serve a completion at ${base_url}.
!! The docker test suite requires real local inference and does NOT skip.
!! Start OMLX (mlx_lm.server) or point HIVEPLANE_MODEL__BASE_URL at an
!! OpenAI-compatible endpoint, then re-run.
!! Check served models with: curl ${base_url}/models
EOF
  exit 1
fi
log "    local LLM OK (probe model: ${resolved_model})"

# --------------------------------------------------------------------------- #
# Run metadata (evidence)
# --------------------------------------------------------------------------- #
HIVEPLANE_RUN_ID="$run_id" \
HIVEPLANE_LOG_DIR="$run_dir" \
HIVEPLANE_REPO_ROOT="$repo_root" \
HIVEPLANE_BASE_URL="$base_url" \
HIVEPLANE_RESOLVED_MODEL="$resolved_model" \
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
    "llm_model": os.environ["HIVEPLANE_RESOLVED_MODEL"],
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

log "==> Bringing up stack (${env_file}, profiles: local test)"
# Point fan-out at the webhook-sink (test profile) so L4 can assert delivery.
export HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL="${HIVEPLANE_FANOUT__SLACK_WEBHOOK_URL:-http://webhook-sink:8081/slack}"
export HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL="${HIVEPLANE_FANOUT__GENERIC_WEBHOOK_URL:-http://webhook-sink:8081/webhook}"
if ! "${compose[@]}" up -d ${build_flag} > "$run_dir/compose-up.log" 2>&1; then
  tail -n 50 "$run_dir/compose-up.log" >&2 || true
  exit 1
fi

log "==> Waiting for /readyz"
deadline=$((SECONDS + 120))
until curl -fsS "http://localhost:${api_port}/readyz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    log "!! control plane never became ready at http://localhost:${api_port}/readyz"
    exit 1
  fi
  sleep 2
done >> "$run_dir/wait-ready.log" 2>&1

log "==> Seeding tool registry"
scripts/seed-tools.sh > "$run_dir/seed-tools.log" 2>&1

log "==> Running docker test suite (L0-L7, no skips)"
export HIVEPLANE_MODEL__BASE_URL="$base_url"
export HIVEPLANE_MODEL__DEFAULT_MODEL="$resolved_model"
set +e
"$python_bin" -m pytest tests/docker -m docker \
  --junitxml="$run_dir/junit.xml" "$@" 2>&1 | tee "$run_dir/pytest.log"
pytest_exit=${PIPESTATUS[0]}
set -e

exit "$pytest_exit"