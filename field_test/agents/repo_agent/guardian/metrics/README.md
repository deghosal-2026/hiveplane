# metrics — Analytics and Monitoring

Aggregates Guardian data for the dashboard and Prometheus.

- `collector.py` — Computes metrics from audit log: PRs analyzed, AI code %, violations by rule, FPR, trends
- `prometheus.py` — Exposes metrics in Prometheus text format for /metrics endpoint
