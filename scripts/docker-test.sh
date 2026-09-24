#!/usr/bin/env bash
#
# Run the HivePlane container-layer test suite (M23, #93/#94).
#
# This suite runs against REAL local inference. It must FAIL — never skip — when
# the local LLM is unavailable. The preflight below enforces that.
#
# Usage: scripts/docker-test.sh [--no-build] [<extra pytest args>]
#
# Environment:
#   HIVEPLANE_MODEL__BASE_URL        Local LLM base URL (default: http://127.0.0.1:8000/v1)
#   HIVEPLANE_MODEL__DEFAULT_MODEL   Model id to probe (optional)
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

echo "==> Preflight: local LLM at ${base_url}"
if ! probe_local_llm; then
  cat >&2 <<EOF
!! Local LLM is not reachable or cannot serve a completion at ${base_url}.
!! The docker test suite requires real local inference and does NOT skip.
!! Start OMLX (mlx_lm.server) or point HIVEPLANE_MODEL__BASE_URL at an
!! OpenAI-compatible endpoint, then re-run.
!! Check served models with: curl ${base_url}/models
EOF
  exit 1
fi
echo "    local LLM OK (probe model: ${resolved_model})"

# --------------------------------------------------------------------------- #
# Stack lifecycle
# --------------------------------------------------------------------------- #
teardown() {
  echo "==> Tearing down stack"
  "${compose[@]}" down -v >/dev/null 2>&1 || true
}
trap teardown EXIT

build_flag="--build"
if [[ "${1:-}" == "--no-build" ]]; then
  build_flag=""
  shift
fi

echo "==> Bringing up stack (${env_file}, profiles: local test)"
"${compose[@]}" up -d ${build_flag}

echo "==> Waiting for /readyz"
deadline=$((SECONDS + 120))
until curl -fsS "http://localhost:${api_port}/readyz" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "!! control plane never became ready at http://localhost:${api_port}/readyz" >&2
    "${compose[@]}" logs api >&2 || true
    exit 1
  fi
  sleep 2
done

echo "==> Seeding tool registry"
scripts/seed-tools.sh >/dev/null

echo "==> Running docker test suite (L0-L7, no skips)"
export HIVEPLANE_MODEL__BASE_URL="$base_url"
export HIVEPLANE_MODEL__DEFAULT_MODEL="$resolved_model"
"$python_bin" -m pytest tests/docker -m docker "$@"
