# API P99 Latency SLA Breach

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | backend (FastAPI) |
| **Owner** | @backend-team |
| **Last Reviewed** | 2025-12-20 |
| **Alert** | `APIP99LatencyHigh` |
| **Tags** | `application`, `api`, `latency`, `sla`, `performance` |

## Summary

The backend API P99 latency has exceeded the SLA threshold (500ms for standard endpoints, 2s for LLM-powered endpoints). This indicates degraded user experience across the CloudThinker platform and may constitute an SLA violation for enterprise customers.

## Impact

- Enterprise customers experience slow dashboard loads, timeout errors, and failed API integrations.
- Frontend Next.js application shows loading spinners and timeout toasts to end users.
- Webhook callbacks to customer systems may timeout, causing missed events.
- SLA breach clock starts ticking -- contractual penalties apply after sustained violations (>15 min for Tier 1 customers).
- Cascading failures possible: slow responses tie up worker threads, reducing overall throughput.

## Prerequisites

- `kubectl` configured for the `production` EKS cluster
- Access to Datadog APM: `https://app.datadoghq.com/apm/services/backend`
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/api-latency/api-latency-overview`
- `psql` access to the production RDS instance
- Familiarity with FastAPI application structure and middleware chain

## Triage & Diagnosis

### Step 1: Confirm the scope of the latency issue

```bash
# Check Datadog APM for the top slow endpoints (last 15 min)
# https://app.datadoghq.com/apm/traces?query=service%3Abackend%20%40duration%3A%3E500ms&sort=duration&start=now-15m

# Check API response times from within the cluster
kubectl exec -n production deploy/backend -- python3 -c "
import requests, time
endpoints = ['/api/v1/health', '/api/v1/runbooks', '/api/v1/incidents', '/api/v1/connections']
for ep in endpoints:
    start = time.time()
    try:
        r = requests.get(f'http://localhost:8000{ep}', timeout=10)
        elapsed = (time.time() - start) * 1000
        print(f'{ep}: {elapsed:.0f}ms (HTTP {r.status_code})')
    except Exception as e:
        print(f'{ep}: FAILED ({e})')
"
```

### Step 2: Check backend pod health and resource usage

```bash
# Pod status and restarts
kubectl get pods -n production -l app=backend -o wide

# CPU and memory consumption
kubectl top pods -n production -l app=backend

# Check for OOMKill or throttling
kubectl describe pods -n production -l app=backend | grep -A 3 -E "State|Reason|Last State|cpu|memory"
```

### Step 3: Inspect application-level metrics

```bash
# Check active request count and thread pool
kubectl exec -n production deploy/backend -- curl -s http://localhost:8000/api/v1/health | python3 -m json.tool

# Check uvicorn worker count
kubectl exec -n production deploy/backend -- ps aux | grep uvicorn
```

### Step 4: Database query performance

```bash
# Check for long-running queries on RDS
kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state, wait_event_type
FROM pg_stat_activity
WHERE state != 'idle'
  AND query NOT ILIKE '%pg_stat_activity%'
ORDER BY duration DESC
LIMIT 20;
"
```

```bash
# Check database connection pool usage
kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT count(*) AS total_connections,
       count(*) FILTER (WHERE state = 'active') AS active,
       count(*) FILTER (WHERE state = 'idle') AS idle,
       count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_tx
FROM pg_stat_activity
WHERE datname = 'cloudthinker';
"
```

### Step 5: Check Redis cache hit rates

```bash
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO stats | grep -E "keyspace_hits|keyspace_misses"
```

Calculate hit rate: `hits / (hits + misses) * 100`. Below 80% indicates cache inefficiency contributing to latency.

### Step 6: Check upstream dependencies

```bash
# Check Qdrant vector DB responsiveness
kubectl exec -n production deploy/backend -- curl -s -o /dev/null -w "%{time_total}s" \
  http://qdrant.production.svc.cluster.local:6333/healthz

# Check executor service health
kubectl exec -n production deploy/backend -- curl -s -o /dev/null -w "%{time_total}s" \
  http://executor.production.svc.cluster.local:8080/health

# Check payment-service health
kubectl exec -n production deploy/backend -- curl -s -o /dev/null -w "%{time_total}s" \
  http://payment-service.production.svc.cluster.local:8000/health
```

## Mitigation Steps

### Scenario A: Slow database queries

One or more SQL queries are taking significantly longer than expected.

1. Identify the slow queries:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT query, calls, mean_exec_time, total_exec_time
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC
   LIMIT 10;
   "
   ```

2. Kill long-running queries causing lock contention:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'active'
     AND (now() - query_start) > interval '60 seconds'
     AND query NOT ILIKE '%pg_stat_activity%';
   "
   ```

3. Check for missing indexes:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch
   FROM pg_stat_user_tables
   WHERE seq_scan > 1000
   ORDER BY seq_tup_read DESC
   LIMIT 10;
   "
   ```

### Scenario B: Connection pool exhaustion

All database connections are in use, new requests queue waiting for a connection.

1. Check current pool usage:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT count(*) FROM pg_stat_activity WHERE datname = 'cloudthinker';
   "
   ```

2. Check the configured max connections:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "SHOW max_connections;"
   ```

3. Kill idle-in-transaction connections:
   ```bash
   kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
     AND query_start < now() - interval '5 minutes';
   "
   ```

### Scenario C: Cache miss storm

Redis cache has been flushed or keys have expired simultaneously, causing all requests to hit the database.

1. Check if cache was recently flushed:
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO stats | grep evicted_keys
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 DBSIZE
   ```

2. Pre-warm critical caches by hitting key endpoints:
   ```bash
   kubectl exec -n production deploy/backend -- python3 -c "
   import requests
   warmup = ['/api/v1/runbooks', '/api/v1/providers', '/api/v1/cost-categories']
   for ep in warmup:
       r = requests.get(f'http://localhost:8000{ep}', headers={'X-Cache-Warmup': 'true'})
       print(f'Warmed: {ep} -> {r.status_code}')
   "
   ```

### Scenario D: Insufficient backend replicas

CPU throttling or memory pressure on existing pods.

1. Check current HPA status:
   ```bash
   kubectl get hpa backend -n production
   ```

2. Scale up the backend deployment:
   ```bash
   kubectl scale deployment/backend -n production --replicas=${DESIRED_REPLICAS}
   ```

3. If HPA is configured, temporarily raise the max:
   ```bash
   kubectl patch hpa backend -n production -p '{"spec":{"maxReplicas":'${NEW_MAX}'}}'
   ```

4. Monitor the new pods coming online:
   ```bash
   kubectl rollout status deployment/backend -n production --timeout=180s
   ```

### Scenario E: Upstream dependency degradation

A downstream service (Qdrant, executor, payment-service) is slow, causing the API to wait.

1. Identify which dependency is slow from APM traces:
   ```
   https://app.datadoghq.com/apm/traces?query=service%3Abackend%20%40duration%3A%3E1s
   ```

2. If Qdrant is slow, restart the service:
   ```bash
   kubectl rollout restart deployment/qdrant -n production
   ```

3. If the executor is overloaded, scale it:
   ```bash
   kubectl scale deployment/executor -n production --replicas=${DESIRED_REPLICAS}
   ```

4. If external LLM API calls (Anthropic, Bedrock) are slow, enable circuit breaker:
   ```bash
   kubectl set env deployment/backend -n production LLM_CIRCUIT_BREAKER_ENABLED=true
   kubectl rollout restart deployment/backend -n production
   ```

## Verification

```bash
# Check P99 latency is back within SLA
# https://grafana.internal.cloudthinker.io/d/api-latency/api-latency-overview

# Quick latency test
kubectl exec -n production deploy/backend -- curl -s -o /dev/null -w "Total: %{time_total}s\n" \
  http://localhost:8000/api/v1/health

# Confirm no slow queries remain
kubectl exec -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '30 seconds';
"

# Confirm Datadog monitor has cleared
# https://app.datadoghq.com/monitors/manage?q=APIP99LatencyHigh
```

Expected: P99 latency below 500ms for standard endpoints, all pods healthy, database connections within normal range.

## Rollback

If scaling changes caused cluster resource pressure:

```bash
# Revert backend replicas
kubectl scale deployment/backend -n production --replicas=3

# Revert HPA max
kubectl patch hpa backend -n production -p '{"spec":{"maxReplicas":6}}'
```

If an environment variable change worsened the issue:

```bash
kubectl rollout undo deployment/backend -n production
```

## Escalation

| Condition | Contact |
|-----------|---------|
| P99 > 2s for more than 10 minutes | @backend-team-lead + @incident-commander |
| SLA breach confirmed for Tier 1 customer | @incident-commander + @customer-success |
| Database performance issue beyond query tuning | @dba-team |
| Suspected DDoS or traffic anomaly | @security-team |
| LLM provider outage (Anthropic, AWS Bedrock) | @ml-team + open provider status page |
| Not resolved within 30 minutes | @vp-engineering |

## Related Runbooks

- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)
- [Redis Memory Pressure / OOM](../database/redis-memory-pressure.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-20 | @jchen | Added LLM circuit breaker scenario |
| 2025-10-05 | @dlee | Added cache warmup procedure |
| 2025-07-14 | @mpark | Added connection pool exhaustion scenario |
| 2025-04-30 | @asingh | Initial version |
