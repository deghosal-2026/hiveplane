# PostgreSQL Long-Running Queries & Locks

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | postgresql (RDS) |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |
| **Alert** | `PostgresLongRunningQuery`, `PostgresLockWaiting` |
| **Tags** | `database`, `postgresql`, `locks`, `queries`, `performance` |

## Summary

Long-running queries or lock contention are blocking other transactions, causing application timeouts, degraded performance, and potential cascading failures. This runbook covers identifying problematic queries, analyzing lock dependency chains, diagnosing missing indexes, and resolving autovacuum blockages.

## Impact

- **User-facing**: API requests timeout waiting for database locks, users see "Request Timeout" or "Service Unavailable" errors.
- **Transaction rollback**: Long-running queries holding locks force other transactions to timeout and rollback, causing data consistency issues.
- **Application degradation**: Backend API, async workers, and dependent services all experience increased latency or complete failures.
- **Cascading failures**: Lock waits consume database connections, potentially triggering connection pool exhaustion (see related runbook).
- **Autovacuum blockage**: Long-running queries prevent autovacuum from running, leading to table bloat and further performance degradation.
- **SLA risk**: Query timeouts breach the 99.95% uptime SLA and P99 latency targets.

## Prerequisites

- `psql` client (v15+) with superuser or `rds_superuser` access to the primary RDS instance
- Access to RDS Performance Insights: `https://console.aws.amazon.com/rds/home#performance-insights`
- `kubectl` access to `production` namespace
- Access to Grafana: `https://<your-grafana-url>/d/postgres/postgres-overview`
- Access to Datadog: Monitor `PostgresLongRunningQuery` and `PostgresLockWaiting`
- Familiarity with PostgreSQL query execution plans and locking mechanisms

## Triage & Diagnosis

### Step 1: Identify Long-Running Queries

```bash
# Check for queries running longer than 5 minutes
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pid,
  now() - pg_stat_activity.query_start AS query_duration,
  usename,
  application_name,
  client_addr,
  state,
  wait_event_type,
  wait_event,
  LEFT(query, 200) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
  AND query NOT ILIKE '%pg_stat_activity%'
  AND now() - pg_stat_activity.query_start > interval '5 minutes'
ORDER BY query_start ASC;
"
```

```bash
# Alternative: Query via kubectl exec if direct access is not available
kubectl exec -it -n production deploy/${BACKEND_DEPLOYMENT} -- psql "${DATABASE_URL}" -c "
SELECT
  pid,
  now() - query_start AS duration,
  usename,
  application_name,
  state,
  LEFT(query, 150) AS query
FROM pg_stat_activity
WHERE state = 'active'
  AND query_start < now() - interval '5 minutes'
ORDER BY query_start ASC;
"
```

### Step 2: Check for Blocked Queries (Lock Contention)

```bash
# Find blocked queries and their blockers
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  blocked_locks.pid AS blocked_pid,
  blocked_activity.usename AS blocked_user,
  blocked_activity.application_name AS blocked_app,
  now() - blocked_activity.query_start AS blocked_duration,
  blocking_locks.pid AS blocking_pid,
  blocking_activity.usename AS blocking_user,
  blocking_activity.application_name AS blocking_app,
  now() - blocking_activity.query_start AS blocking_duration,
  blocking_activity.state AS blocking_state,
  LEFT(blocked_activity.query, 100) AS blocked_query,
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
ORDER BY blocked_duration DESC;
"
```

```bash
# Count of waiting queries (quick check)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT count(*) AS lock_waiting_count
FROM pg_stat_activity
WHERE wait_event_type = 'Lock';
"
```

### Step 3: Identify Lock Dependency Chains

```bash
# Recursive CTE to find lock chains (root blockers)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
WITH RECURSIVE lock_chain AS (
  -- Root blockers (blocking but not blocked)
  SELECT
    blocking.pid AS blocker_pid,
    blocking.application_name AS blocker_app,
    blocking.state AS blocker_state,
    now() - blocking.query_start AS blocker_duration,
    LEFT(blocking.query, 100) AS blocker_query,
    1 AS chain_level,
    ARRAY[blocking.pid] AS chain_pids
  FROM pg_catalog.pg_locks blocking_locks
  JOIN pg_catalog.pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
  WHERE blocking_locks.granted
    AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_locks blocked_locks
      WHERE blocked_locks.pid = blocking_locks.pid
        AND NOT blocked_locks.granted
    )
    AND EXISTS (
      SELECT 1
      FROM pg_catalog.pg_locks waiting_locks
      JOIN pg_catalog.pg_stat_activity waiting ON waiting.pid = waiting_locks.pid
      WHERE NOT waiting_locks.granted
        AND waiting_locks.locktype = blocking_locks.locktype
        AND waiting_locks.database IS NOT DISTINCT FROM blocking_locks.database
        AND waiting_locks.relation IS NOT DISTINCT FROM blocking_locks.relation
        AND waiting_locks.pid != blocking_locks.pid
    )

  UNION ALL

  -- Blocked sessions
  SELECT
    blocked_locks.pid,
    blocked_activity.application_name,
    blocked_activity.state,
    now() - blocked_activity.query_start,
    LEFT(blocked_activity.query, 100),
    lc.chain_level + 1,
    lc.chain_pids || blocked_locks.pid
  FROM lock_chain lc
  JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.pid = lc.blocker_pid
  JOIN pg_catalog.pg_locks blocked_locks
    ON blocked_locks.locktype = blocking_locks.locktype
    AND blocked_locks.database IS NOT DISTINCT FROM blocking_locks.database
    AND blocked_locks.relation IS NOT DISTINCT FROM blocking_locks.relation
    AND blocked_locks.pid != blocking_locks.pid
    AND NOT blocked_locks.granted
  JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
  WHERE NOT blocked_locks.pid = ANY(lc.chain_pids)
)
SELECT * FROM lock_chain
ORDER BY chain_level, blocker_duration DESC;
"
```

### Step 4: Check Table Bloat and Dead Tuples

```bash
# Find tables with high dead tuple counts (potential autovacuum blockage)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  schemaname,
  relname,
  n_live_tup AS live_tuples,
  n_dead_tup AS dead_tuples,
  ROUND(n_dead_tup::numeric / GREATEST(n_live_tup, 1) * 100, 2) AS dead_pct,
  last_vacuum,
  last_autovacuum,
  last_analyze,
  last_autoanalyze,
  pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 20;
"
```

```bash
# Check if autovacuum is blocked by long-running queries
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pid,
  now() - xact_start AS txn_age,
  usename,
  application_name,
  state,
  LEFT(query, 150) AS query
FROM pg_stat_activity
WHERE state != 'idle'
  AND xact_start IS NOT NULL
  AND now() - xact_start > interval '30 minutes'
ORDER BY xact_start ASC;
"
```

### Step 5: Analyze Query Execution Plans

```bash
# Get execution plan for a slow query (identify missing indexes)
# First, extract the full query from pg_stat_activity if needed
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT query
FROM pg_stat_activity
WHERE pid = ${PID};
"
```

```bash
# Explain the query to identify sequential scans
kubectl exec -it -n production deploy/${BACKEND_DEPLOYMENT} -- psql "${DATABASE_URL}" -c "
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
${SLOW_QUERY_HERE};
"
```

> **WARNING**: `EXPLAIN ANALYZE` actually executes the query. For potentially slow or destructive queries, use `EXPLAIN` without `ANALYZE` first.

### Step 6: Check pg_stat_statements for Top Consumers

```bash
# Identify queries consuming most total time and CPU
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  queryid,
  calls,
  ROUND(total_exec_time::numeric, 2) AS total_time_ms,
  ROUND(mean_exec_time::numeric, 2) AS mean_time_ms,
  ROUND((total_exec_time / SUM(total_exec_time) OVER ()) * 100, 2) AS pct_total_time,
  rows,
  100.0 * shared_blks_hit / NULLIF(shared_blks_hit + shared_blks_read, 0) AS cache_hit_ratio,
  LEFT(query, 150) AS query_snippet
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
  AND calls > 10
ORDER BY total_exec_time DESC
LIMIT 20;
"
```

### Step 7: Check Connection Count and State Distribution

```bash
# Overview of connection states
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  state,
  count(*) AS count,
  ROUND(count(*) * 100.0 / SUM(count(*)) OVER (), 1) AS pct
FROM pg_stat_activity
WHERE datname = '${DB_NAME}'
GROUP BY state
ORDER BY count DESC;
"
```

```bash
# Check for idle-in-transaction sessions (often indicates application bugs)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pid,
  usename,
  application_name,
  client_addr,
  now() - xact_start AS txn_duration,
  now() - state_change AS idle_duration,
  LEFT(query, 150) AS last_query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_start ASC;
"
```

### Step 8: Check for Sequential Scans on Large Tables

```bash
# Identify tables with high sequential scan ratios (missing indexes)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  schemaname,
  relname,
  seq_scan AS sequential_scans,
  seq_tup_read AS rows_scanned,
  idx_scan AS index_scans,
  ROUND(100.0 * seq_scan / NULLIF(seq_scan + idx_scan, 0), 2) AS seq_scan_pct,
  n_live_tup AS live_rows,
  pg_size_pretty(pg_relation_size(schemaname || '.' || relname)) AS table_size
FROM pg_stat_user_tables
WHERE seq_scan > 0
  AND pg_relation_size(schemaname || '.' || relname) > 1048576  -- Tables larger than 1MB
ORDER BY seq_tup_read DESC
LIMIT 15;
"
```

## Mitigation Steps

### Scenario A: Runaway Query Consuming Resources

One or more queries are running indefinitely or consuming excessive CPU/IO.

1. Identify the runaway query (from Triage Step 1):

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     now() - query_start AS duration,
     usename,
     application_name,
     LEFT(query, 300) AS query
   FROM pg_stat_activity
   WHERE state = 'active'
     AND query_start < now() - interval '5 minutes'
   ORDER BY query_start ASC;
   "
   ```

2. Cancel the query gracefully (recommended first step):

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_cancel_backend(${PID});
   "
   ```

   This sends a `SIGINT` to the backend process, allowing it to rollback gracefully.

3. If `pg_cancel_backend` fails (query does not respond within 10 seconds), terminate the connection forcefully:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_terminate_backend(${PID});
   "
   ```

   This sends a `SIGTERM`, forcing an immediate connection close and rollback.

4. Verify the connection is gone:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT count(*) FROM pg_stat_activity WHERE pid = ${PID};
   "
   ```

   Expected output: `0`

5. Check connection pool recovery:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT count(*) AS active_connections
   FROM pg_stat_activity
   WHERE state = 'active';
   "
   ```

6. Identify the root cause by analyzing the query:

   ```bash
   # Search backend codebase for the query pattern
   kubectl exec -n production deploy/${BACKEND_DEPLOYMENT} -- grep -r "${QUERY_PATTERN}" /app/app/ --include="*.py" -l
   ```

### Scenario B: Lock Contention Blocking Transactions

Queries are waiting on locks held by other transactions, forming a dependency chain.

1. Identify the root blocker (from Triage Step 3):

   ```bash
   # Run the lock chain query to find the root cause
   # The root blocker will be at chain_level = 1
   ```

2. Review the blocking query to understand its purpose:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     state,
     now() - xact_start AS txn_duration,
     now() - query_start AS query_duration,
     query
   FROM pg_stat_activity
   WHERE pid = ${BLOCKING_PID};
   "
   ```

3. If the blocker is in `idle in transaction` state (application bug), terminate it immediately:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_terminate_backend(${BLOCKING_PID});
   "
   ```

4. If the blocker is actively running, assess whether it's safe to cancel:

   - For long-running reads (SELECT), canceling is safe: `pg_cancel_backend()`
   - For writes (UPDATE, DELETE, INSERT), consult with the application team before canceling

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_cancel_backend(${BLOCKING_PID});
   "
   ```

5. Verify locks are released:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT count(*) AS waiting_locks
   FROM pg_stat_activity
   WHERE wait_event_type = 'Lock';
   "
   ```

   Expected output: `0` or significantly reduced count.

6. Check Datadog/Grafana for lock wait metrics:

   ```bash
   # https://<your-grafana-url>/d/postgres/postgres-overview?orgId=1&refresh=10s
   ```

7. If lock contention persists, terminate all idle-in-transaction sessions:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pg_terminate_backend(pid),
     pid,
     application_name,
     now() - xact_start AS txn_age
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
     AND now() - xact_start > interval '2 minutes'
     AND pid != pg_backend_pid();
   "
   ```

### Scenario C: Missing Index Causing Full Table Scans

A query is performing sequential scans on large tables instead of using an index.

1. Identify the problematic table (from Triage Step 8):

   ```bash
   # Find tables with high seq_scan ratios and large sizes
   ```

2. Analyze the slow query with EXPLAIN:

   ```bash
   kubectl exec -it -n production deploy/${BACKEND_DEPLOYMENT} -- psql "${DATABASE_URL}" -c "
   EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
   ${SLOW_QUERY_HERE};
   "
   ```

   Look for: `Seq Scan on ${TABLE_NAME}` with high `rows` or `buffers` counts.

3. Identify the missing index from the WHERE clause or JOIN conditions:

   ```bash
   # Example: If the query filters on `WHERE user_id = 123 AND created_at > '2025-01-01'`
   # You likely need an index on (user_id, created_at)
   ```

4. Create the index using `CONCURRENTLY` to avoid table locks:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_${TABLE}_${COLUMNS}
   ON ${SCHEMA}.${TABLE_NAME} (${COLUMN1}, ${COLUMN2});
   "
   ```

   > **Note**: `CREATE INDEX CONCURRENTLY` allows reads and writes to continue during index creation but takes longer to complete.

5. Monitor index creation progress:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     now() - query_start AS duration,
     state,
     LEFT(query, 100) AS query
   FROM pg_stat_activity
   WHERE query LIKE '%CREATE INDEX%';
   "
   ```

6. Once complete, update table statistics:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   ANALYZE ${SCHEMA}.${TABLE_NAME};
   "
   ```

7. Re-run the slow query and verify it now uses the index:

   ```bash
   kubectl exec -it -n production deploy/${BACKEND_DEPLOYMENT} -- psql "${DATABASE_URL}" -c "
   EXPLAIN (ANALYZE, BUFFERS)
   ${SLOW_QUERY_HERE};
   "
   ```

   Expected: `Index Scan using idx_${TABLE}_${COLUMNS}` instead of `Seq Scan`.

8. Monitor API latency recovery:

   ```bash
   # https://<your-grafana-url>/d/api-latency/api-latency-overview
   ```

### Scenario D: Autovacuum Blocked / Table Bloat

Long-running transactions are preventing autovacuum from cleaning up dead tuples, causing table bloat.

1. Check autovacuum status (from Triage Step 4):

   ```bash
   # Identify tables with high dead_pct and old last_autovacuum timestamps
   ```

2. Check if autovacuum is currently running:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     now() - xact_start AS duration,
     query
   FROM pg_stat_activity
   WHERE query LIKE '%autovacuum%'
     AND state = 'active';
   "
   ```

3. Identify long-running transactions blocking autovacuum:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     now() - xact_start AS txn_age,
     usename,
     application_name,
     state,
     LEFT(query, 150) AS query
   FROM pg_stat_activity
   WHERE xact_start IS NOT NULL
     AND state != 'idle'
     AND now() - xact_start > interval '30 minutes'
   ORDER BY xact_start ASC;
   "
   ```

4. Terminate blocking transactions (after confirming with application team):

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_terminate_backend(${PID});
   "
   ```

5. Manually trigger vacuum on bloated tables:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   VACUUM (VERBOSE, ANALYZE) ${SCHEMA}.${TABLE_NAME};
   "
   ```

   Monitor vacuum progress:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     now() - query_start AS duration,
     wait_event_type,
     wait_event,
     query
   FROM pg_stat_activity
   WHERE query LIKE '%VACUUM%';
   "
   ```

6. For severely bloated tables (dead_pct > 50%), consider `VACUUM FULL` during low-traffic periods:

   ```bash
   # WARNING: VACUUM FULL takes an exclusive lock and rewrites the entire table
   # Only use during maintenance windows
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   VACUUM (FULL, VERBOSE, ANALYZE) ${SCHEMA}.${TABLE_NAME};
   "
   ```

7. Adjust autovacuum parameters to prevent future bloat:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   ALTER TABLE ${SCHEMA}.${TABLE_NAME} SET (
     autovacuum_vacuum_scale_factor = 0.02,    -- Vacuum when 2% of rows are dead
     autovacuum_analyze_scale_factor = 0.01,   -- Analyze when 1% of rows change
     autovacuum_vacuum_cost_delay = 5          -- More aggressive vacuum
   );
   "
   ```

8. Verify dead tuple reduction:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     relname,
     n_live_tup,
     n_dead_tup,
     ROUND(n_dead_tup::numeric / GREATEST(n_live_tup, 1) * 100, 2) AS dead_pct,
     last_autovacuum
   FROM pg_stat_user_tables
   WHERE relname = '${TABLE_NAME}';
   "
   ```

9. Set a global `idle_in_transaction_session_timeout` to prevent future blockage:

   ```bash
   # Set via RDS parameter group (5 minutes = 300000 ms)
   aws rds modify-db-parameter-group \
     --db-parameter-group-name ${DB_PARAM_GROUP} \
     --parameters "ParameterName=idle_in_transaction_session_timeout,ParameterValue=300000,ApplyMethod=immediate" \
     --region ${AWS_REGION}
   ```

## Verification

```bash
# Verify no long-running queries (> 5 minutes)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT count(*) AS long_running_queries
FROM pg_stat_activity
WHERE state = 'active'
  AND query_start < now() - interval '5 minutes';
"
```

Expected output: `0`

```bash
# Verify lock waits are resolved
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT count(*) AS lock_waits
FROM pg_stat_activity
WHERE wait_event_type = 'Lock';
"
```

Expected output: `0`

```bash
# Verify query latency is normal
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  ROUND(mean_exec_time::numeric, 2) AS mean_time_ms
FROM pg_stat_statements
WHERE query LIKE 'SELECT%'
  AND calls > 100
ORDER BY calls DESC
LIMIT 10;
"
```

Expected: Mean execution times below 100ms for common queries.

```bash
# Check API health endpoint
curl -s -o /dev/null -w "%{http_code}" https://<your-api-url>/health
```

Expected: `200`

```bash
# Verify Datadog monitors cleared
# https://app.datadoghq.com/monitors/manage?q=PostgresLongRunningQuery
# https://app.datadoghq.com/monitors/manage?q=PostgresLockWaiting
```

```bash
# Check Grafana for query duration metrics
# https://<your-grafana-url>/d/postgres/postgres-overview?orgId=1&refresh=10s
```

**Expected**: No active long-running queries, lock waits at 0, API latency within SLA (P99 < 500ms), Datadog monitors in OK state.

## Rollback

If a newly created index caused unexpected issues:

```bash
# Drop the index (use CONCURRENTLY to avoid locks)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
DROP INDEX CONCURRENTLY IF EXISTS idx_${TABLE}_${COLUMNS};
"
```

If autovacuum parameter changes worsened performance:

```bash
# Reset table-specific autovacuum parameters
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
ALTER TABLE ${SCHEMA}.${TABLE_NAME} RESET (
  autovacuum_vacuum_scale_factor,
  autovacuum_analyze_scale_factor,
  autovacuum_vacuum_cost_delay
);
"
```

If `idle_in_transaction_session_timeout` caused application errors:

```bash
# Increase the timeout or disable it
aws rds modify-db-parameter-group \
  --db-parameter-group-name ${DB_PARAM_GROUP} \
  --parameters "ParameterName=idle_in_transaction_session_timeout,ParameterValue=0,ApplyMethod=immediate" \
  --region ${AWS_REGION}
```

> **Note**: Setting to `0` disables the timeout. Consider a longer value (e.g., 600000 ms = 10 minutes) instead.

If query cancellation caused data inconsistency:

- Check application logs for failed transactions
- Review `pg_stat_activity` for related queries
- Coordinate with application team to identify and retry failed operations

## Escalation

| Condition | Contact |
|-----------|---------|
| Lock waits > 10 for more than 15 minutes | @your-team-lead |
| Multiple lock dependency chains (> 5 levels deep) | @your-team-lead |
| Cannot identify root blocker | @your-team + @your-team |
| Autovacuum blocked for > 1 hour | @your-team-lead |
| Table bloat causing disk space issues | @your-sre-team + @your-team |
| Query termination causing application errors | @your-backend-team-lead + @your-team |
| Suspected application bug causing lock leaks | @your-backend-team-lead (hotfix required) |
| API SLA breach confirmed | @your-incident-commander |

## Related Runbooks

- [PostgreSQL High CPU Usage](postgres-high-cpu.md)
- [PostgreSQL Connection Pool Exhaustion](postgres-connection-pool-exhaustion.md)
- [PostgreSQL Replication Lag](postgres-replication-lag.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-11 | @your-team | Initial version |
