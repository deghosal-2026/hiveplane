#!/usr/bin/env bash
#
# Seed the MCP tool registry with every tool referenced by the workload
# manifests (M23, #131). Idempotent: tools already present are left alone.
#
# Usage: scripts/seed-tools.sh [workloads-dir]
#   workloads-dir  Directory of AgentWorkload manifests (default: examples/workloads)
#
# Environment:
#   HIVEPLANE_API_URL  Control-plane base URL (default: http://localhost:8100)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workloads_dir="${1:-$repo_root/examples/workloads}"
api_url="${HIVEPLANE_API_URL:-http://localhost:8100}"

echo "==> Seeding tools from ${workloads_dir}"
hiveplane tools seed --workloads-dir "$workloads_dir" --api-url "$api_url"