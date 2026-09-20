# PostgreSQL High CPU Usage

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | postgresql (RDS) |
| **Owner** | @backend-team |
| **Last Reviewed** | 2025-12-08 |
| **Alert** | `RDSCPUUtilizationHigh` |
| **Tags** | `database`, `postgresql`, `rds`, `cpu`, `performance` |

## Summary

The production PostgreSQL RDS instance CPU utilization has exceeded the warning threshold (typically 80% sustained for 5+ minutes). High CPU on the database impacts all services that depend on it, including the backend API, Celery workers, executor, and payment-service. This runbook covers identifying the root cause (runaway queries, missing indexes, lock contention) and restoring normal operations.

## Impact

- All API endpoints backed by PostgreSQL experience increased latency or timeouts.
- Celery workers that query the database hang or fail, causing queue backlogs.
- The executor service cannot fetch or update runbook execution state.
- Payment-service may fail to process billing events, risking revenue loss.
- If CPU stays at 100% for an extended period, RDS may become unresponsive and require a reboot.

## Prerequisites

- `kubectl` configured for the `production` EKS cluster
- `psql` client (v14+) or access via a backend pod
- AWS CLI with `rds:Describe*`, `pi:GetResourceMetrics` permissions
- Access to RDS Performance Insights: `https://console.aws.amazon.com/rds/home#performance-insights`
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/rds-overview/rds-postgres-overview`
- Access to Datadog monitor: `RDSCPUUtilizationHigh`

## Triage & Diagnosis

### Step 1: Confirm CPU utilization and trends

```bash
# Check current RDS CPU via CloudWatch
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average Maximum \
  --output table
```

```bash
# Open Performance Insights for visual analysis
# https://console.aws.amazon.com/rds/home#performance-insights:resourceId=cloudthinker-prod-db
```

### Step 2: Check active queries and their resource consumption

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT
  pid,
  now() - pg_stat_activity.query_start AS duration,
  usename,
  application_name,
  state,
  wait_event_type,
  wait_event,
  LEFT(query, 120) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
  AND query NOT ILIKE '%pg_stat_activity%'
ORDER BY duration DESC
LIMIT 25;
"
```

### Step 3: Find the most CPU-intensive queries (via pg_stat_statements)

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT
  queryid,
  calls,
  ROUND(total_exec_time::numeric, 2) AS total_time_ms,
  ROUND(mean_exec_time::numeric, 2) AS mean_time_ms,
  rows,
  LEFT(query, 150) AS query_snippet
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 15;
"
```

### Step 4: Check for lock contention

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT
  blocked_locks.pid AS blocked_pid,
  blocked_activity.usename AS blocked_user,
  LEFT(blocked_activity.query, 100) AS blocked_query,
  blocking_locks.pid AS blocking_pid,
  blocking_activity.usename AS blocking_user,
  LEFT(blocking_activity.query, 100) AS blocking_query
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
  ON blocking_locks.locktype = blocked_locks.locktype
  AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
  AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
  AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
  AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
  AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
  AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
  AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
  AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
  AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
  AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
LIMIT 10;
"
```

### Step 5: Check for table bloat and autovacuum status

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT
  schemaname,
  relname,
  n_live_tup,
  n_dead_tup,
  ROUND(n_dead_tup::numeric / GREATEST(n_live_tup, 1) * 100, 2) AS dead_pct,
  last_autovacuum,
  last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 10000
ORDER BY n_dead_tup DESC
LIMIT 15;
"
```

### Step 6: Check connection count and pool pressure

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT
  usename,
  application_name,
  state,
  count(*) AS conn_count
FROM pg_stat_activity
GROUP BY usename, application_name, state
ORDER BY conn_count DESC;
"
```

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT current_setting('max_connections') AS max_connections,
       (SELECT count(*) FROM pg_stat_activity) AS current_connections;
"
```

## Mitigation Steps

### Scenario A: Long-running or runaway queries

One or more queries are consuming disproportionate CPU.

1. Identify the culprit query (from Step 2 above):
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pid, now() - query_start AS duration, LEFT(query, 200) AS query
   FROM pg_stat_activity
   WHERE state = 'active' AND query_start < now() - interval '60 seconds'
   ORDER BY duration DESC LIMIT 5;
   "
   ```

2. Cancel the query gracefully:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_cancel_backend(${PID});
   "
   ```

3. If `pg_cancel_backend` does not work, terminate the connection:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(${PID});
   "
   ```

4. Verify CPU is dropping:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name CPUUtilization \
     --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
     --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Average \
     --output table
   ```

### Scenario B: Missing indexes causing sequential scans

A query is scanning entire tables instead of using indexes.

1. Identify tables with high sequential scan rates:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT
     relname,
     seq_scan,
     seq_tup_read,
     idx_scan,
     ROUND(seq_scan::numeric / GREATEST(seq_scan + idx_scan, 1) * 100, 2) AS seq_scan_pct,
     pg_size_pretty(pg_relation_size(relid)) AS table_size
   FROM pg_stat_user_tables
   WHERE seq_scan > 100
   ORDER BY seq_tup_read DESC
   LIMIT 10;
   "
   ```

2. Analyze the slow query with EXPLAIN:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) ${SLOW_QUERY};
   "
   ```

   > **WARNING**: `EXPLAIN ANALYZE` actually *executes* the query. For destructive queries (UPDATE, DELETE) or very slow queries, use `EXPLAIN` without `ANALYZE` first.

3. Create the missing index (use CONCURRENTLY to avoid table locks):
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   CREATE INDEX CONCURRENTLY IF NOT EXISTS ${INDEX_NAME}
   ON ${TABLE_NAME} (${COLUMN_NAME});
   "
   ```

4. Update table statistics:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   ANALYZE ${TABLE_NAME};
   "
   ```

### Scenario C: Lock contention

Queries are waiting on locks held by other transactions.

1. Identify blocking queries (from Step 4):
   ```bash
   # The lock contention query from Triage Step 4 shows blocking/blocked pairs
   ```

2. Terminate the blocking session:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(${BLOCKING_PID});
   "
   ```

3. If the locks are from an idle-in-transaction session:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
     AND query_start < now() - interval '10 minutes';
   "
   ```

### Scenario D: Table bloat -- autovacuum not keeping up

Dead tuples are accumulating, forcing PostgreSQL to scan more data.

1. Manually trigger VACUUM on bloated tables:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   VACUUM (VERBOSE, ANALYZE) ${TABLE_NAME};
   "
   ```

2. For severely bloated tables, consider a more aggressive approach:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   VACUUM (FULL, VERBOSE) ${TABLE_NAME};
   "
   ```
   **WARNING**: `VACUUM FULL` takes an exclusive lock on the table. Only use during low-traffic periods or for small tables.

3. Tune autovacuum parameters if this is recurring:
   ```bash
   kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
   ALTER TABLE ${TABLE_NAME} SET (
     autovacuum_vacuum_scale_factor = 0.05,
     autovacuum_analyze_scale_factor = 0.02,
     autovacuum_vacuum_cost_delay = 10
   );
   "
   ```

### Scenario E: Vertical scaling (increase RDS instance size)

Workload has legitimately outgrown the instance class.

1. Check current instance details:
   ```bash
   aws rds describe-db-instances \
     --db-instance-identifier cloudthinker-prod-db \
     --query "DBInstances[0].{Class:DBInstanceClass,CPU:ProcessorFeatures,Storage:AllocatedStorage,IOPS:Iops,Engine:EngineVersion}" \
     --output table
   ```

2. Modify to a larger instance class (causes failover, ~30s downtime with Multi-AZ):
   ```bash
   aws rds modify-db-instance \
     --db-instance-identifier cloudthinker-prod-db \
     --db-instance-class ${NEW_INSTANCE_CLASS} \
     --apply-immediately
   ```

3. Monitor the modification:
   ```bash
   watch -n 15 "aws rds describe-db-instances \
     --db-instance-identifier cloudthinker-prod-db \
     --query 'DBInstances[0].DBInstanceStatus' --output text"
   ```

## Verification

```bash
# Confirm CPU utilization is back to normal
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average \
  --output table

# Confirm no long-running queries remain
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
SELECT count(*) FROM pg_stat_activity
WHERE state = 'active' AND query_start < now() - interval '30 seconds';
"

# Confirm API latency has recovered
# https://grafana.internal.cloudthinker.io/d/api-latency/api-latency-overview

# Confirm Datadog monitor has cleared
# https://app.datadoghq.com/monitors/manage?q=RDSCPUUtilizationHigh
```

Expected: CPU utilization below 70%, no queries running longer than 30 seconds, API P99 latency within SLA.

## Rollback

If a new index caused unexpected issues (unlikely but possible with concurrent index creation):

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
DROP INDEX CONCURRENTLY IF EXISTS ${INDEX_NAME};
"
```

If autovacuum parameter changes worsened performance:

```bash
kubectl exec -it -n production deploy/backend -- psql "${DATABASE_URL}" -c "
ALTER TABLE ${TABLE_NAME} RESET (
  autovacuum_vacuum_scale_factor,
  autovacuum_analyze_scale_factor,
  autovacuum_vacuum_cost_delay
);
"
```

If RDS instance scaling caused extended downtime:

- Multi-AZ failover is automatic. If the primary is stuck in `modifying` state for more than 30 minutes, contact AWS Support.
- Applications with connection pooling (backend, workers) will reconnect automatically after failover.

## Escalation

| Condition | Contact |
|-----------|---------|
| CPU above 90% for 15+ minutes after mitigation | @backend-team-lead |
| Cannot identify the problematic query | @dba-team |
| Lock contention from unknown application | @backend-team-lead + @platform-team |
| RDS instance modification stuck or failed | Open AWS Support case (Severity: Urgent) |
| Suspected data corruption | @vp-engineering + @incident-commander |
| API SLA breach confirmed | @incident-commander (see API Latency runbook) |

## Related Runbooks

- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Redis Memory Pressure / OOM](../database/redis-memory-pressure.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-08 | @dlee | Added autovacuum tuning scenario |
| 2025-09-30 | @jchen | Added lock contention diagnosis steps |
| 2025-07-12 | @mpark | Added Performance Insights references |
| 2025-04-18 | @asingh | Initial version |
