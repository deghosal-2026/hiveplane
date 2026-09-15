#!/usr/bin/env bash
#
# Bring up the HivePlane local reference stack.
#
# Usage: scripts/dev-up.sh [--no-build]
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -f .env ]]; then
  echo "==> Creating .env from .env.example"
  cp .env.example .env
fi

build_flag="--build"
if [[ "${1:-}" == "--no-build" ]]; then
  build_flag=""
fi

echo "==> Starting HivePlane stack"
# shellcheck disable=SC2086
docker compose up -d ${build_flag}

echo "==> Waiting for services to report healthy"
docker compose ps

cat <<'EOF'

HivePlane stack is starting. Endpoints:
  API          http://localhost:8000/healthz
  Grafana      http://localhost:3000
  Prometheus   http://localhost:9090
  Tempo        http://localhost:3200
  OTel OTLP    localhost:4317 (gRPC) / localhost:4318 (HTTP)

Inspect with: docker compose logs -f
Tear down:    docker compose down
EOF
