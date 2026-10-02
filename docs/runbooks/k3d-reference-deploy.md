# k3d reference deployment

Deploy the full HivePlane stack to a disposable [k3d](https://k3d.io) cluster and
verify the control loop. This is the reference runbook referenced by M59-02; the
chart render is covered by `tests/test_helm_chart.py`.

## Prerequisites

- `docker`, `k3d`, `kubectl`, `helm`
- Local images `hiveplane/api:0.2.0` and `hiveplane/ui:0.2.0`, or point
  `api.image.repository`/`ui.image.repository` at a registry.

## 1. Create the cluster and import images

```bash
k3d cluster create hiveplane --agents 1
k3d image import hiveplane/api:0.2.0 hiveplane/ui:0.2.0 -c hiveplane
```

## 2. Install the chart

```bash
helm install hiveplane deploy/helm/hiveplane \
  --namespace hiveplane --create-namespace \
  --set postgres.auth.password="$(openssl rand -hex 16)"
kubectl -n hiveplane rollout status deploy/hiveplane-api --timeout=180s
```

## 3. Verify the loop

```bash
kubectl -n hiveplane port-forward svc/hiveplane-api 8100:8100 &
curl -fsS http://localhost:8100/ready

# Seed a demo fleet and confirm the catalog is non-empty.
hiveplane --api-url http://localhost:8100 demo seed
hiveplane --api-url http://localhost:8100 workloads list

# Register + certify a workload and run it end-to-end (see USER_GUIDE.md).
```

## 4. Tear down

```bash
k3d cluster delete hiveplane
```

## CI

`deploy/k3d/up.sh` automates steps 1–3 for CI; it is a documented runbook
helper, not part of the Python test suite.
