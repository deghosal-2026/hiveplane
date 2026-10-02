#!/usr/bin/env bash
# k3d reference deploy for HivePlane (M59-02). See docs/runbooks/k3d-reference-deploy.md.
set -euo pipefail

CLUSTER="${CLUSTER:-hiveplane}"
NAMESPACE="${NAMESPACE:-hiveplane}"
API_TAG="${API_TAG:-0.2.0}"
CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../helm/hiveplane" && pwd)"

for tool in docker k3d kubectl helm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing required tool: $tool" >&2; exit 1; }
done

if ! k3d cluster list "$CLUSTER" >/dev/null 2>&1; then
  k3d cluster create "$CLUSTER" --agents 1
fi

for image in "hiveplane/api:${API_TAG}" "hiveplane/ui:${UI_TAG:-0.2.0}"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    k3d image import "$image" -c "$CLUSTER"
  fi
done

helm upgrade --install hiveplane "$CHART_DIR" \
  --namespace "$NAMESPACE" --create-namespace \
  --set "api.image.tag=${API_TAG}" \
  --set postgres.auth.password="${POSTGRES_PASSWORD:-hiveplane-local}"

kubectl -n "$NAMESPACE" rollout status deploy -l app.kubernetes.io/component=api --timeout=180s

echo "HivePlane deployed. Port-forward the API with:"
echo "  kubectl -n ${NAMESPACE} port-forward svc/hiveplane-api 8100:8100"
