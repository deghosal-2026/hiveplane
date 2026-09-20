# PostgreSQL Connection Pool Exhaustion

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | backend, worker-high, worker-low, payment-service, executor |
| **Owner** | @backend-team |
| **Last Reviewed** | 2026-01-20 |
| **Alert** | `PostgresConnectionPoolExhausted` |
| **Tags** | `database`, `postgresql`, `connections`, `pgbouncer`, `pool` |

## Summary

Application services are unable to acquire database connections because the connection pool (PgBouncer) or PostgreSQL's `max_connections` limit has been reached. This runbook covers identifying connection-leaking code, analyzing `pg_stat_activity` for stuck sessions, PgBouncer pool diagnostics, emergency connection cleanup, and long-term pool tuning.

## Impact

- **User-facing**: All API requests that require database access return HTTP 503 or timeout. Users see "Service Unavailable" errors across the entire platform.
- **Payment processing**: `payment-service` cannot record transactions, leading to potential double-charges if retries hit Stripe without idempotency.
- **Celery workers**: All task execution halts. `worker-high` tasks (runbook execution, AI analysis) and `worker-low` tasks (analytics, cleanup) fail with `OperationalError: connection pool exhausted`.
- **Cascading failure**: Connection exhaustion on one service causes all services sharing the pool to fail simultaneously.
- **SLA risk**: Breaches the 99.95% uptime SLA within minutes. Every second of total outage counts.

## Prerequisites

- `psql` client (v15+) with superuser or `rds_superuser` access to the primary RDS instance
- Access to PgBouncer admin console (if deployed): `psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer`
- `kubectl` access to `production` namespace
- Access to Grafana: `https://grafana.internal.cloudthinker.io/d/pgbouncer/pgbouncer-pool-overview`
- Access to Datadog: Monitor `PostgresConnectionPoolExhausted`
- Familiarity with PgBouncer configuration and PostgreSQL connection management

## Triage & Diagnosis

### Step 1: Confirm Connection Exhaustion

```bash
# Check current connection count vs limit on RDS primary
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  count(*) AS total_connections,
  current_setting('max_connections')::int AS max_connections,
  count(*) * 100.0 / current_setting('max_connections')::int AS usage_pct
FROM pg_stat_activity;
"
```

```bash
# Breakdown by state
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  state,
  count(*) AS count,
  round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM pg_stat_activity
WHERE datname = 'cloudthinker'
GROUP BY state
ORDER BY count DESC;
"
```

```bash
# Check PgBouncer pool status (if applicable)
psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
```

```bash
# Check PgBouncer client connections
psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW CLIENTS;"
```

### Step 2: Identify Connection Consumers by Application

```bash
# Connections grouped by application name
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  application_name,
  usename,
  state,
  count(*) AS connections,
  min(backend_start) AS oldest_connection,
  max(now() - state_change) AS longest_in_state
FROM pg_stat_activity
WHERE datname = 'cloudthinker'
GROUP BY application_name, usename, state
ORDER BY connections DESC;
"
```

```bash
# Connections grouped by client IP (identifies which pods are hogging connections)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  client_addr,
  application_name,
  count(*) AS connections,
  count(*) FILTER (WHERE state = 'idle') AS idle,
  count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn,
  count(*) FILTER (WHERE state = 'active') AS active
FROM pg_stat_activity
WHERE datname = 'cloudthinker'
GROUP BY client_addr, application_name
ORDER BY connections DESC
LIMIT 20;
"
```

### Step 3: Identify Problematic Sessions

```bash
# Find idle-in-transaction connections (leak indicator)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  pid,
  usename,
  application_name,
  client_addr,
  state,
  now() - xact_start AS xact_duration,
  now() - state_change AS time_in_state,
  left(query, 200) AS last_query
FROM pg_stat_activity
WHERE datname = 'cloudthinker'
  AND state = 'idle in transaction'
ORDER BY xact_start ASC;
"
```

```bash
# Find very old idle connections (potential leaks from connection pool misconfiguration)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  pid,
  usename,
  application_name,
  client_addr,
  state,
  now() - backend_start AS connection_age,
  now() - state_change AS idle_duration,
  left(query, 150) AS last_query
FROM pg_stat_activity
WHERE datname = 'cloudthinker'
  AND state = 'idle'
  AND now() - state_change > interval '10 minutes'
ORDER BY state_change ASC
LIMIT 30;
"
```

### Step 4: Check Application-Side Pool Configuration

```bash
# Check backend deployment env vars for pool settings
kubectl get deployment backend -n production -o jsonpath='{.spec.template.spec.containers[0].env}' | python3 -m json.tool | grep -iE 'pool|conn|db'
```

```bash
# Check worker pool settings
kubectl get deployment worker-high -n production -o jsonpath='{.spec.template.spec.containers[0].env}' | python3 -m json.tool | grep -iE 'pool|conn|db'
```

```bash
# Check total possible connections from all deployments
# Formula: replicas * pool_size_per_pod
for deploy in backend worker-high worker-low payment-service executor; do
  replicas=$(kubectl get deployment ${deploy} -n production -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
  echo "${deploy}: ${replicas} replicas"
done
```

### Step 5: Check for Recent Deployments or Scaling Events

```bash
# Recent deployment rollouts (connection leak may have been introduced)
kubectl rollout history deployment/backend -n production | tail -5
kubectl rollout history deployment/worker-high -n production | tail -5
```

```bash
# Recent HPA scaling events (more pods = more connections)
kubectl get events -n production --sort-by='.lastTimestamp' | grep -i "scaled\|replica" | tail -10
```

```bash
# Check Grafana for connection count timeline
# https://grafana.internal.cloudthinker.io/d/pgbouncer/pgbouncer-pool-overview?orgId=1&from=now-2h&to=now
```

## Mitigation Steps

### Scenario A: Idle-in-Transaction Sessions Consuming Connections

Connections stuck in `idle in transaction` state indicate application code that opens a transaction but never commits or rolls back, often due to unhandled exceptions in request handlers.

1. Terminate all idle-in-transaction sessions older than 5 minutes:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT
     pg_terminate_backend(pid),
     pid,
     application_name,
     now() - xact_start AS txn_age,
     left(query, 100) AS last_query
   FROM pg_stat_activity
   WHERE datname = 'cloudthinker'
     AND state = 'idle in transaction'
     AND now() - xact_start > interval '5 minutes'
     AND pid != pg_backend_pid();
   "
   ```

2. Set a server-side timeout to prevent future accumulation:

   ```bash
   # Set idle_in_transaction_session_timeout (milliseconds) via RDS parameter group
   aws rds modify-db-parameter-group \
     --db-parameter-group-name ${DB_PARAM_GROUP} \
     --parameters "ParameterName=idle_in_transaction_session_timeout,ParameterValue=300000,ApplyMethod=immediate" \
     --region ${AWS_REGION}
   ```

3. Identify the leaking code path from the terminated sessions' last query:

   ```bash
   # Cross-reference application_name and last_query from Step 3
   # Search backend codebase for the query pattern
   kubectl exec -n production deploy/backend -- grep -r "${QUERY_PATTERN}" /app/app/ --include="*.py" -l
   ```

### Scenario B: PgBouncer Pool Saturated (Server Connections Maxed)

PgBouncer has reached its `default_pool_size` or `max_db_connections`, queuing client requests.

1. Check PgBouncer detailed stats:

   ```bash
   psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
   ```

   Key columns: `cl_active` (client connections actively using a server connection), `cl_waiting` (clients waiting for a server connection), `sv_active` (server connections in use), `sv_idle` (server connections idle), `pool_mode`.

2. If `cl_waiting > 0`, increase PgBouncer pool size temporarily:

   ```bash
   # Edit PgBouncer ConfigMap
   kubectl edit configmap pgbouncer-config -n production
   # Increase: default_pool_size = 40  (from 20)
   # Increase: max_db_connections = 200  (from 100)
   ```

   ```bash
   # Restart PgBouncer to apply
   kubectl rollout restart deployment/pgbouncer -n production
   kubectl rollout status deployment/pgbouncer -n production --timeout=60s
   ```

3. If PgBouncer is in `session` mode, switch to `transaction` mode for better connection reuse:

   ```bash
   # Check current pool mode
   psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW CONFIG;" | grep pool_mode
   ```

   ```bash
   # Update ConfigMap to transaction pooling
   kubectl edit configmap pgbouncer-config -n production
   # Set: pool_mode = transaction
   ```

   > **Warning**: Transaction pooling mode breaks session-level features like `SET`, `LISTEN/NOTIFY`, and prepared statements. Verify application compatibility.

4. Verify PgBouncer recovery:

   ```bash
   psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
   # cl_waiting should be 0
   ```

### Scenario C: Application Connection Leak (No Pool or Broken Pool)

A service is opening connections directly without a pool, or the pool's cleanup is not working.

1. Identify the leaking service by connection count growth:

   ```bash
   # Run this every 30 seconds to see which app's connections are growing
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT
     application_name,
     count(*) AS total,
     count(*) FILTER (WHERE state = 'idle') AS idle,
     count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn,
     count(*) FILTER (WHERE state = 'active') AS active
   FROM pg_stat_activity
   WHERE datname = 'cloudthinker'
   GROUP BY application_name
   ORDER BY total DESC;
   "
   ```

2. Emergency: restart the leaking service to release connections:

   ```bash
   kubectl rollout restart deployment/${LEAKING_SERVICE} -n production
   kubectl rollout status deployment/${LEAKING_SERVICE} -n production --timeout=120s
   ```

3. Verify connections dropped after restart:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT application_name, count(*) AS connections
   FROM pg_stat_activity
   WHERE datname = 'cloudthinker'
   GROUP BY application_name
   ORDER BY connections DESC;
   "
   ```

4. If a specific service keeps leaking after restart, scale it down temporarily:

   ```bash
   kubectl scale deployment/${LEAKING_SERVICE} -n production --replicas=1
   ```

### Scenario D: max_connections Limit Reached on RDS

All PostgreSQL connection slots are consumed, even PgBouncer cannot get new server connections.

1. Emergency: terminate all idle connections to free slots:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT pg_terminate_backend(pid), pid, application_name, state
   FROM pg_stat_activity
   WHERE datname = 'cloudthinker'
     AND state = 'idle'
     AND now() - state_change > interval '2 minutes'
     AND pid != pg_backend_pid();
   "
   ```

2. If still at capacity, terminate idle-in-transaction connections:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT pg_terminate_backend(pid), pid, application_name, state,
          now() - xact_start AS txn_age
   FROM pg_stat_activity
   WHERE datname = 'cloudthinker'
     AND state = 'idle in transaction'
     AND pid != pg_backend_pid();
   "
   ```

3. Check remaining connections after cleanup:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
   SELECT count(*) AS current, current_setting('max_connections') AS max
   FROM pg_stat_activity;
   "
   ```

4. If persistent, increase `max_connections` on RDS (requires reboot):

   ```bash
   aws rds modify-db-parameter-group \
     --db-parameter-group-name ${DB_PARAM_GROUP} \
     --parameters "ParameterName=max_connections,ParameterValue=${NEW_MAX_CONNECTIONS},ApplyMethod=pending-reboot" \
     --region ${AWS_REGION}

   # Schedule a maintenance window reboot or reboot immediately
   aws rds reboot-db-instance \
     --db-instance-identifier ${PRIMARY_INSTANCE_ID} \
     --region ${AWS_REGION}
   ```

   > **Warning**: Rebooting the primary causes a brief outage (30-60 seconds with Multi-AZ). Coordinate with @incident-commander.

### Scenario E: HPA-Triggered Scaling Exceeded Connection Budget

Horizontal Pod Autoscaler scaled up services, causing total connection demand to exceed the pool/RDS limit.

1. Check current replica counts vs normal:

   ```bash
   kubectl get hpa -n production
   ```

2. Manually cap the HPA to prevent further scaling:

   ```bash
   kubectl patch hpa backend-hpa -n production -p '{"spec":{"maxReplicas":${SAFE_MAX_REPLICAS}}}'
   kubectl patch hpa worker-high-hpa -n production -p '{"spec":{"maxReplicas":${SAFE_MAX_REPLICAS}}}'
   ```

3. Scale down non-critical services to free connections:

   ```bash
   kubectl scale deployment/worker-low -n production --replicas=1
   kubectl scale deployment/executor -n production --replicas=1
   ```

4. Reduce per-pod pool size via environment variable:

   ```bash
   kubectl set env deployment/backend -n production DB_POOL_SIZE=3 DB_MAX_OVERFLOW=2
   kubectl set env deployment/worker-high -n production DB_POOL_SIZE=2 DB_MAX_OVERFLOW=1
   kubectl rollout status deployment/backend -n production --timeout=120s
   ```

## Verification

```bash
# Verify connection usage is below 80% of max
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d cloudthinker -c "
SELECT
  count(*) AS current_connections,
  current_setting('max_connections')::int AS max_connections,
  round(count(*) * 100.0 / current_setting('max_connections')::int, 1) AS usage_pct
FROM pg_stat_activity;
"
```

```bash
# Verify no clients waiting in PgBouncer
psql -h ${PGBOUNCER_HOST} -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;" | grep cl_waiting
```

```bash
# Verify application health (no 503 errors)
kubectl logs -n production deploy/backend --tail=30 | grep -iE "503|connection\s+pool|operational.?error"
```

```bash
# Check API endpoint health
curl -s -o /dev/null -w "%{http_code}" https://api.cloudthinker.io/health
# Expected: 200
```

```bash
# Verify Datadog monitor recovered
# https://app.datadoghq.com/monitors/manage?q=PostgresConnectionPoolExhausted
```

**Expected**: Connection usage below 80%, `cl_waiting = 0` in PgBouncer, API returning 200, Datadog monitor in OK state.

## Rollback

If pool size or `max_connections` changes caused instability:

```bash
# Revert PgBouncer config changes
kubectl edit configmap pgbouncer-config -n production
# Restore: default_pool_size = 20
# Restore: max_db_connections = 100
kubectl rollout restart deployment/pgbouncer -n production
```

```bash
# Revert per-pod pool size
kubectl set env deployment/backend -n production DB_POOL_SIZE=5 DB_MAX_OVERFLOW=5
kubectl set env deployment/worker-high -n production DB_POOL_SIZE=3 DB_MAX_OVERFLOW=3
kubectl rollout status deployment/backend -n production --timeout=120s
```

```bash
# Restore HPA limits
kubectl patch hpa backend-hpa -n production -p '{"spec":{"maxReplicas":${ORIGINAL_MAX_REPLICAS}}}'
kubectl patch hpa worker-high-hpa -n production -p '{"spec":{"maxReplicas":${ORIGINAL_MAX_REPLICAS}}}'
```

```bash
# Restore scaled-down services
kubectl scale deployment/worker-low -n production --replicas=${ORIGINAL_WORKER_LOW_REPLICAS}
kubectl scale deployment/executor -n production --replicas=${ORIGINAL_EXECUTOR_REPLICAS}
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Pool exhausted > 5 min, API returning 503 | @backend-team-lead |
| All services affected, total outage | @incident-commander |
| RDS reboot required (Multi-AZ failover) | @database-team-lead + @incident-commander |
| Payment processing impacted | @payment-team-lead + @incident-commander |
| Connection leak in production code identified | @backend-team-lead (hotfix required) |
| max_connections increase needed beyond RDS instance limits | @database-team (instance class upgrade) |

## Related Runbooks

- [PostgreSQL Replication Lag](postgres-replication-lag.md)
- [PostgreSQL High CPU Usage](postgres-high-cpu.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)
- [Application Memory Leak](../application/memory-leak-diagnosis.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-20 | @backend-team | Added Scenario E (HPA-triggered exhaustion) |
| 2025-11-05 | @backend-team | Added PgBouncer transaction mode migration steps |
| 2025-08-19 | @database-team | Updated max_connections procedure for Multi-AZ |
| 2025-05-12 | @sre-team | Added application-side pool configuration checks |
| 2025-02-28 | @backend-team | Initial version |
