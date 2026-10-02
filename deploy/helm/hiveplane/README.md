# HivePlane Helm chart

Deploys the full HivePlane stack to Kubernetes: the API control plane, the
operator UI, PostgreSQL, Redis, and the telemetry stack (OTel collector, Tempo,
Prometheus, Grafana).

## Install

```bash
helm install hiveplane deploy/helm/hiveplane \
  --namespace hiveplane --create-namespace \
  --set postgres.auth.password="$(openssl rand -hex 16)"
```

Point the API at a production image tag and an existing Postgres secret:

```bash
helm install hiveplane deploy/helm/hiveplane \
  --set api.image.tag=0.2.0 \
  --set postgres.auth.existingSecret=hiveplane-postgres
```

## Values

`values.yaml` is a public contract; `values.schema.json` validates it. Breaking
changes bump the chart `version`. Key values:

| Value | Default | Description |
|-------|---------|-------------|
| `api.replicaCount` | `1` | Control-plane replicas |
| `api.service.port` | `8100` | API service port |
| `ui.enabled` | `true` | Deploy the operator UI |
| `postgres.auth.existingSecret` | `""` | Use an existing password secret |
| `postgres.persistence.size` | `8Gi` | Postgres volume size |
| `telemetry.enabled` | `true` | Deploy the telemetry stack |
| `ingress.enabled` | `false` | Expose the API/UI through an Ingress |

## Verify

```bash
helm lint deploy/helm/hiveplane
helm template hiveplane deploy/helm/hiveplane
```

See `docs/runbooks/k3d-reference-deploy.md` for a k3d reference deployment.
