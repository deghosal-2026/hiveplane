# Distribution & supply chain

How to install HivePlane on a real cluster and verify what you install.

## Helm (M59-01)

`deploy/helm/hiveplane` deploys the full stack (API, UI, PostgreSQL, Redis,
OTel/Tempo/Prometheus/Grafana). `values.yaml` is a public contract;
`values.schema.json` validates it.

```bash
helm lint deploy/helm/hiveplane
helm template hiveplane deploy/helm/hiveplane
helm install hiveplane deploy/helm/hiveplane --namespace hiveplane --create-namespace
```

## k3d reference deploy (M59-02)

See [`docs/runbooks/k3d-reference-deploy.md`](runbooks/k3d-reference-deploy.md)
and `deploy/k3d/up.sh`.

## Backup & restore (M59-03)

```bash
hiveplane backup public-key --output backup.pub
hiveplane backup create --output backup.json
hiveplane backup verify backup.json --public-key backup.pub
hiveplane backup restore backup.json --public-key backup.pub
```

An archive is a signed (Ed25519) manifest of per-target payload digests.
Restore refuses a schema mismatch (the archive records the Alembic head) and any
integrity or signature failure. `requiem` targets currently cover runs and
registered workloads; the target set is pluggable.

## Air-gapped bundle (M59-04)

```bash
scripts/airgap-bundle.sh            # writes dist/hiveplane-airgap-<tag>.tar.gz + .sha256
```

The bundle contains gzipped `docker save` images, the Helm chart, the compose
file, and install notes, so a restricted-network host installs with no internet.

## Homebrew & PyPI (M59-05)

The release workflow builds the sdist/wheel, publishes to PyPI, and updates the
`deploy/homebrew/hiveplane.rb` formula (checksum substituted per release).

## Release supply chain (M59-06)

`.github/workflows/release.yml` runs on `v*` tags: build + push images, generate
an SPDX SBOM (`syft`), cosign-sign the images, emit SLSA-style provenance
(`scripts/release_provenance.py`), and package the Helm chart. Verification:

```bash
cosign verify ghcr.io/<owner>/hiveplane-api:<tag>
slsa-verifier verify-image ghcr.io/<owner>/hiveplane-api:<tag>
```

## Demo profile (M59-07)

```bash
hiveplane demo seed
```

Seeds two tenants with certified workloads, runs, and metering; idempotent and
deterministic.

## Federation (M59-08, stretch)

Off by default. Enable with `HIVEPLANE_FEDERATION__ENABLED=true`, then register
remote planes and read the aggregate view:

```bash
curl -X POST "$API/federation/planes" -H 'Content-Type: application/json' \
  -d '{"plane_id":"edge-1","name":"Edge One","base_url":"https://edge-1","workload_count":3}'
curl "$API/federation/aggregate"
```