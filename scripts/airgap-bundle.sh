#!/usr/bin/env bash
# Build an air-gapped install bundle for restricted networks (M59-04).
#
# The bundle contains the container images (as tarballs), the Helm chart, and
# the compose file, so a target host can install with no internet access.
set -euo pipefail

IMAGE_TAG="${IMAGE_TAG:-0.2.0}"
OUT_DIR="${OUT_DIR:-dist/airgap}"
BUNDLE="${BUNDLE:-dist/hiveplane-airgap-${IMAGE_TAG}.tar.gz}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$OUT_DIR/images" "$OUT_DIR/certs"
cp -r "$ROOT/deploy/helm/hiveplane" "$OUT_DIR/helm"
cp "$ROOT/docker-compose.yml" "$OUT_DIR/docker-compose.yml"

for image in "hiveplane/api:${IMAGE_TAG}" "hiveplane/ui:${IMAGE_TAG}"; do
  name="$(echo "$image" | tr '/:' '__')"
  docker save "$image" | gzip > "$OUT_DIR/images/${name}.tar.gz"
done

cat > "$OUT_DIR/INSTALL.md" <<'EOF'
# HivePlane air-gapped install

1. `k3d image import images/*.tar.gz -c hiveplane` (or `docker load`).
2. `helm install hiveplane ./helm --set api.image.tag=<tag>`.
3. No internet access is required: all images are bundled.
EOF

tar -czf "$BUNDLE" -C "$(dirname "$OUT_DIR")" "$(basename "$OUT_DIR")"
sha256sum "$BUNDLE" > "${BUNDLE}.sha256"
echo "wrote ${BUNDLE} ($(du -h "$BUNDLE" | cut -f1))"
