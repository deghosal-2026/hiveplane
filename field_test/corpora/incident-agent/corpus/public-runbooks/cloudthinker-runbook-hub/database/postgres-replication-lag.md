# PostgreSQL Replication Lag

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | `<your-backend-service>`, `<your-worker-services>` |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-01-15 |
| **Alert** | `RDSReplicationLagHigh` |
| **Tags** | `database`, `postgresql`, `replication`, `rds`, `read-replica` |

## Summary

The read replica is falling behind the primary RDS instance, causing stale reads for application queries routed to the replica. This runbook covers diagnosis and mitigation of replication lag across multiple root causes including write-heavy workloads, slow WAL replay, long-running transactions on the replica, and network throughput bottlenecks.

## Impact

- **User-facing**: Stale data returned from read-heavy endpoints (e.g., dashboard analytics, search, audit logs). Users may see outdated counts, reports, or missing recent changes.
- **Backend services**: Read-after-write consistency violations -- a user creates a resource, but the subsequent GET reads from the replica and returns 404.
- **Async workers**: Background tasks that query the replica for batch analytics produce incorrect aggregations.
- **If lag exceeds 5 minutes**: Automated failover logic in the backend marks the replica unhealthy and shifts all read traffic to the primary, risking primary overload.
- **SLA risk**: Breaches the internal 99.9% data freshness SLO (max acceptable lag: 30 seconds).

## Prerequisites

- AWS IAM credentials with `rds:Describe*`, `cloudwatch:GetMetricData` permissions
- `psql` client (v15+) with connection strings for both primary and replica
- `kubectl` access to `production` namespace
- Access to Grafana: `https://<your-grafana-url>/d/rds-replication/rds-replication-overview`
- Access to Datadog: Monitor `RDSReplicationLagHigh`
- Familiarity with PostgreSQL WAL (Write-Ahead Log) architecture

## Triage & Diagnosis

### Step 1: Confirm the Alert and Current Lag

```bash
# Check current replication lag from CloudWatch (seconds)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Maximum \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Check lag directly on the replica via SQL
psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  now() - pg_last_xact_replay_timestamp() AS replication_lag,
  pg_last_wal_receive_lsn() AS last_received,
  pg_last_wal_replay_lsn() AS last_replayed,
  pg_last_wal_receive_lsn() - pg_last_wal_replay_lsn() AS receive_replay_delta_bytes
;"
```

```bash
# Check Grafana dashboard for lag trend (last 1 hour)
# https://<your-grafana-url>/d/rds-replication/rds-replication-overview?orgId=1&from=now-1h&to=now
```

### Step 2: Check Primary Write Workload

```bash
# On the PRIMARY: check current write rate (transactions per second)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  datname,
  xact_commit + xact_rollback AS total_xacts,
  tup_inserted,
  tup_updated,
  tup_deleted,
  pg_size_pretty(pg_database_size(datname)) AS db_size
FROM pg_stat_database
WHERE datname = '${DB_NAME}';
"
```

```bash
# Check WAL generation rate on primary (bytes per second over last 5 min)
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pg_current_wal_lsn() AS current_lsn,
  pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0') AS total_wal_bytes;
"
```

```bash
# Check for bulk operations currently running on primary
psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pid,
  now() - xact_start AS xact_duration,
  now() - query_start AS query_duration,
  state,
  left(query, 120) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
  AND datname = '${DB_NAME}'
  AND query !~* '^(SET|SHOW|BEGIN|COMMIT|ROLLBACK)'
ORDER BY xact_start ASC
LIMIT 20;
"
```

### Step 3: Check Replica Replay Status

```bash
# On REPLICA: check if WAL replay is paused or blocked
psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pg_is_in_recovery() AS is_replica,
  pg_last_wal_receive_lsn() AS received_lsn,
  pg_last_wal_replay_lsn() AS replayed_lsn,
  pg_last_xact_replay_timestamp() AS last_replay_time,
  now() - pg_last_xact_replay_timestamp() AS replay_lag;
"
```

```bash
# Check for long-running queries on the replica that block replay
# (hot_standby_feedback or long SELECT queries can delay WAL replay)
psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  pid,
  now() - query_start AS duration,
  state,
  wait_event_type,
  wait_event,
  left(query, 150) AS query_snippet
FROM pg_stat_activity
WHERE datname = '${DB_NAME}'
  AND state != 'idle'
  AND query_start < now() - interval '2 minutes'
ORDER BY query_start ASC;
"
```

```bash
# Check recovery conflict stats on replica
psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
SELECT
  datname,
  confl_tablespace,
  confl_lock,
  confl_snapshot,
  confl_bufferpin,
  confl_deadlock
FROM pg_stat_database_conflicts
WHERE datname = '${DB_NAME}';
"
```

### Step 4: Check Network Throughput Between Primary and Replica

```bash
# Check network throughput on the replica instance
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name NetworkReceiveThroughput \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Maximum \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Check if primary's network transmit is saturated
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name NetworkTransmitThroughput \
  --dimensions Name=DBInstanceIdentifier,Value=${PRIMARY_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Maximum \
  --region ${AWS_REGION} \
  --output table
```

### Step 5: Check Replica Resource Utilization

```bash
# Check CPU, memory, IOPS on the replica
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Maximum \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Check replica disk I/O (write IOPS - WAL replay is write-heavy)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name WriteIOPS \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Maximum \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Check freeable memory on replica
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name FreeableMemory \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average Minimum \
  --region ${AWS_REGION} \
  --output table
```

## Mitigation Steps

### Scenario A: Write-Heavy Workload on Primary (Bulk Operations)

This scenario applies when a large data migration, bulk import, or analytics backfill is generating excessive WAL on the primary.

1. Identify the bulk operation:

   ```bash
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     usename,
     application_name,
     now() - xact_start AS xact_age,
     state,
     left(query, 200) AS query_snippet
   FROM pg_stat_activity
   WHERE state = 'active'
     AND datname = '${DB_NAME}'
     AND now() - xact_start > interval '1 minute'
   ORDER BY xact_start ASC;
   "
   ```

2. If the operation is a non-critical batch job (e.g., analytics backfill from Celery), throttle or pause it:

   ```bash
   # Pause the Celery worker running the bulk task
   kubectl exec -n production deploy/${WORKER_DEPLOYMENT} -- celery -A app.celery_app control cancel_consumer default

   # Or scale down worker-low temporarily
   kubectl scale deployment worker-low -n production --replicas=0
   ```

3. If the operation is a data migration that cannot be paused, reduce its impact by batching:

   ```bash
   # On the primary: check if the migration is using a single large transaction
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pid, now() - xact_start AS xact_age, left(query, 200) AS query
   FROM pg_stat_activity
   WHERE state = 'active' AND now() - xact_start > interval '5 minutes';
   "

   # If it is a single large INSERT/UPDATE, cancel it and re-run in smaller batches
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_cancel_backend(${BULK_OP_PID});
   "
   ```

4. Monitor lag recovery after reducing write load:

   ```bash
   watch -n 5 'psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -t -c \
     "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"'
   ```

### Scenario B: Long-Running Queries on Replica Blocking WAL Replay

When `hot_standby_feedback` is enabled or long-running read queries hold snapshots that prevent the replica from replaying WAL, lag accumulates.

1. Identify blocking queries on the replica:

   ```bash
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     pid,
     usename,
     application_name,
     now() - query_start AS query_age,
     state,
     left(query, 200) AS query_snippet
   FROM pg_stat_activity
   WHERE datname = '${DB_NAME}'
     AND state IN ('active', 'idle in transaction')
     AND now() - query_start > interval '5 minutes'
   ORDER BY query_start ASC;
   "
   ```

2. Terminate long-running queries on the replica that are older than the lag threshold:

   ```bash
   # Cancel gracefully first
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_cancel_backend(pid)
   FROM pg_stat_activity
   WHERE datname = '${DB_NAME}'
     AND state IN ('active', 'idle in transaction')
     AND now() - query_start > interval '10 minutes'
     AND pid != pg_backend_pid();
   "
   ```

   ```bash
   # If cancel does not work, terminate forcefully
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = '${DB_NAME}'
     AND state IN ('active', 'idle in transaction')
     AND now() - query_start > interval '10 minutes'
     AND pid != pg_backend_pid();
   "
   ```

3. Check if `hot_standby_feedback` is causing the issue and consider disabling temporarily:

   ```bash
   # Check current setting
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SHOW hot_standby_feedback;
   "
   ```

   ```bash
   # If enabled, disable via RDS parameter group (requires reboot)
   aws rds modify-db-parameter-group \
     --db-parameter-group-name ${REPLICA_PARAM_GROUP} \
     --parameters "ParameterName=hot_standby_feedback,ParameterValue=off,ApplyMethod=pending-reboot" \
     --region ${AWS_REGION}
   ```

4. Set `max_standby_streaming_delay` to allow the replica to cancel conflicting queries sooner:

   ```bash
   # Check current value
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SHOW max_standby_streaming_delay;
   "

   # Reduce to 15 seconds (default is 30s) via parameter group
   aws rds modify-db-parameter-group \
     --db-parameter-group-name ${REPLICA_PARAM_GROUP} \
     --parameters "ParameterName=max_standby_streaming_delay,ParameterValue=15000,ApplyMethod=immediate" \
     --region ${AWS_REGION}
   ```

### Scenario C: Network Throughput Bottleneck

When the primary generates WAL faster than the network can deliver it to the replica, or the replica is in a different AZ with constrained bandwidth.

1. Confirm network is the bottleneck:

   ```bash
   # Compare WAL generation rate vs network receive rate
   # WAL generation on primary (bytes/sec over 5 min):
   psql -h ${PRIMARY_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0') AS total_wal_bytes;
   "
   # Run again after 60 seconds, compute delta

   # Network receive on replica from CloudWatch (already checked in Step 4)
   # If WAL generation rate >> network receive throughput, network is the bottleneck
   ```

2. Check if the replica instance class has sufficient network bandwidth:

   ```bash
   aws rds describe-db-instances \
     --db-instance-identifier ${REPLICA_INSTANCE_ID} \
     --query 'DBInstances[0].[DBInstanceClass, AvailabilityZone]' \
     --output table \
     --region ${AWS_REGION}
   ```

3. If the replica is undersized, scale up the replica instance class:

   ```bash
   # Scale up replica to a larger instance class with higher network bandwidth
   aws rds modify-db-instance \
     --db-instance-identifier ${REPLICA_INSTANCE_ID} \
     --db-instance-class ${TARGET_INSTANCE_CLASS} \
     --apply-immediately \
     --region ${AWS_REGION}
   ```

   > **Warning**: This causes a brief outage on the replica (reboot). Ensure application read traffic is redirected to primary first.

4. Before scaling replica, redirect read traffic to primary:

   ```bash
   # Update the backend config to stop routing reads to the replica
   kubectl set env deployment/${BACKEND_DEPLOYMENT} -n production DB_READ_HOST=${PRIMARY_ENDPOINT}

   # Verify backend restarted with new config
   kubectl rollout status deployment/${BACKEND_DEPLOYMENT} -n production --timeout=120s
   ```

### Scenario D: Replica Instance Under-Provisioned (CPU/IOPS Saturation)

The replica cannot replay WAL fast enough because it is CPU-bound or IOPS-limited.

1. Confirm replica resource saturation:

   ```bash
   # Check CPU > 80% sustained
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name CPUUtilization \
     --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
     --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Average \
     --region ${AWS_REGION} \
     --output table
   ```

   ```bash
   # Check read/write IOPS vs provisioned
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name ReadIOPS \
     --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
     --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Average Maximum \
     --region ${AWS_REGION} \
     --output table
   ```

2. Reduce query load on the replica:

   ```bash
   # Check which applications are querying the replica
   psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -c "
   SELECT
     application_name,
     count(*) AS connections,
     count(*) FILTER (WHERE state = 'active') AS active
   FROM pg_stat_activity
   WHERE datname = '${DB_NAME}'
   GROUP BY application_name
   ORDER BY active DESC;
   "
   ```

   ```bash
   # Redirect non-critical read queries away from the replica
   kubectl set env deployment/${WORKER_DEPLOYMENT} -n production DB_READ_HOST=${PRIMARY_ENDPOINT}
   kubectl rollout status deployment/${WORKER_DEPLOYMENT} -n production --timeout=120s
   ```

3. If IOPS is the bottleneck, increase storage IOPS:

   ```bash
   aws rds modify-db-instance \
     --db-instance-identifier ${REPLICA_INSTANCE_ID} \
     --iops ${TARGET_IOPS} \
     --apply-immediately \
     --region ${AWS_REGION}
   ```

### Scenario E: Emergency -- Promote Replica to Standalone (Last Resort)

If replication lag is unrecoverable and the replica is needed for disaster recovery, promote it. This is a **destructive, irreversible** action.

> **WARNING**: Promoting a replica breaks the replication link permanently. The promoted instance becomes a standalone database. You must recreate the replica afterward.

1. Confirm promotion is necessary (only if primary is failing or replication is permanently broken):

   ```bash
   # Verify primary health
   aws rds describe-db-instances \
     --db-instance-identifier ${PRIMARY_INSTANCE_ID} \
     --query 'DBInstances[0].DBInstanceStatus' \
     --output text \
     --region ${AWS_REGION}
   ```

2. Promote the replica:

   ```bash
   aws rds promote-read-replica \
     --db-instance-identifier ${REPLICA_INSTANCE_ID} \
     --region ${AWS_REGION}
   ```

3. Update application to point to the promoted instance:

   ```bash
   kubectl set env deployment/${BACKEND_DEPLOYMENT} -n production \
     DB_HOST=${REPLICA_ENDPOINT} \
     DB_READ_HOST=${REPLICA_ENDPOINT}
   kubectl set env deployment/${WORKER_DEPLOYMENT_HIGH} -n production DB_HOST=${REPLICA_ENDPOINT}
   kubectl set env deployment/${WORKER_DEPLOYMENT} -n production DB_HOST=${REPLICA_ENDPOINT}

   # Restart all affected deployments
   kubectl rollout restart deployment/${BACKEND_DEPLOYMENT} deployment/${WORKER_DEPLOYMENT_HIGH} deployment/${WORKER_DEPLOYMENT} -n production
   ```

4. After stabilization, create a new read replica from the promoted primary:

   ```bash
   aws rds create-db-instance-read-replica \
     --db-instance-identifier ${NEW_REPLICA_INSTANCE_ID} \
     --source-db-instance-identifier ${REPLICA_INSTANCE_ID} \
     --db-instance-class ${REPLICA_INSTANCE_CLASS} \
     --availability-zone ${TARGET_AZ} \
     --region ${AWS_REGION}
   ```

## Verification

After applying mitigation, verify lag is recovering:

```bash
# Watch replication lag in real-time (should decrease steadily)
watch -n 10 'psql -h ${REPLICA_ENDPOINT} -U ${DB_USER} -d ${DB_NAME} -t -c \
  "SELECT
    now() - pg_last_xact_replay_timestamp() AS lag,
    pg_last_wal_receive_lsn() AS received,
    pg_last_wal_replay_lsn() AS replayed;"'
```

```bash
# Confirm via CloudWatch (lag should trend toward 0)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=${REPLICA_INSTANCE_ID} \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Verify application health after read traffic re-routing
kubectl get pods -n production -l app.kubernetes.io/name=${BACKEND_DEPLOYMENT} -o wide
kubectl logs -n production deploy/${BACKEND_DEPLOYMENT} --tail=50 | grep -i "replica\|read\|database"
```

```bash
# Check Datadog monitor has recovered
# https://app.datadoghq.com/monitors/manage?q=RDSReplicationLagHigh
```

**Expected**: Replication lag below 5 seconds and trending to sub-second. No `ReplicaLag` CloudWatch alarm. Datadog monitor returns to OK.

## Rollback

If read traffic was redirected to the primary and you need to revert:

```bash
# Restore read routing to replica after lag resolves
kubectl set env deployment/${BACKEND_DEPLOYMENT} -n production DB_READ_HOST=${REPLICA_ENDPOINT}
kubectl set env deployment/${WORKER_DEPLOYMENT} -n production DB_READ_HOST=${REPLICA_ENDPOINT}

# Verify rollout
kubectl rollout status deployment/${BACKEND_DEPLOYMENT} -n production --timeout=120s
kubectl rollout status deployment/${WORKER_DEPLOYMENT} -n production --timeout=120s
```

If `hot_standby_feedback` was disabled, re-enable after lag resolves:

```bash
aws rds modify-db-parameter-group \
  --db-parameter-group-name ${REPLICA_PARAM_GROUP} \
  --parameters "ParameterName=hot_standby_feedback,ParameterValue=on,ApplyMethod=pending-reboot" \
  --region ${AWS_REGION}
```

If `max_standby_streaming_delay` was reduced, restore to original value:

```bash
aws rds modify-db-parameter-group \
  --db-parameter-group-name ${REPLICA_PARAM_GROUP} \
  --parameters "ParameterName=max_standby_streaming_delay,ParameterValue=30000,ApplyMethod=immediate" \
  --region ${AWS_REGION}
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Lag > 60s and not improving after 15 min | @your-team-lead |
| Lag > 5 min or primary also degraded | @your-incident-commander |
| Replica promotion required | @your-team-lead + @your-engineering-leadership |
| Data inconsistency suspected | @your-team-lead + @your-incident-commander |
| Primary RDS instance unhealthy | @your-cloud-provider-support (Severity 1 case) |

## Related Runbooks

- [PostgreSQL High CPU Usage](postgres-high-cpu.md)
- [PostgreSQL Connection Pool Exhaustion](postgres-connection-pool-exhaustion.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-15 | @your-team | Added Scenario D (IOPS saturation) and replica scaling steps |
| 2025-10-22 | @your-team | Added emergency replica promotion procedure |
| 2025-07-08 | @your-sre-team | Updated CloudWatch queries for RDS PostgreSQL 15 |
| 2025-03-14 | @your-team | Initial version |
