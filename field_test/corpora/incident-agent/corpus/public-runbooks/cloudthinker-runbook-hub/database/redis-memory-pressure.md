# Redis Memory Pressure / OOM

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | redis (ElastiCache) |
| **Owner** | @backend-team |
| **Last Reviewed** | 2025-12-15 |
| **Alert** | `RedisMemoryUsageHigh` |
| **Tags** | `database`, `redis`, `memory`, `elasticache` |

## Summary

Redis memory usage has exceeded the warning threshold (typically 80% of `maxmemory`) or the instance is actively evicting keys / returning OOM errors. This runbook covers diagnosis, immediate relief, and longer-term capacity fixes for the CloudThinker ElastiCache Redis cluster.

## Impact

- Celery task queues (`worker-high`, `worker-low`) may fail to enqueue or dequeue jobs.
- API response caching becomes unavailable, increasing latency on the backend service.
- Session and rate-limit data may be evicted, causing user-facing auth errors or throttling misfires.
- If memory hits 100%, Redis returns `OOM command not allowed` and write operations fail globally.

## Prerequisites

- `kubectl` configured for the `production` EKS cluster
- AWS CLI with `elasticache:Describe*` and `elasticache:Modify*` permissions
- `redis-cli` (v6+) installed locally or available in the backend pod
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/redis-overview/redis-cluster-overview`
- Access to Datadog monitor: `RedisMemoryUsageHigh`

## Triage & Diagnosis

### Step 1: Confirm the alert and check current memory usage

```bash
# Open the Grafana dashboard for Redis overview
# https://grafana.internal.cloudthinker.io/d/redis-overview/redis-cluster-overview

# Connect to Redis via a backend pod
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO memory
```

Key fields to check:
- `used_memory_human` -- current consumption
- `used_memory_peak_human` -- historical peak
- `maxmemory_human` -- configured limit
- `mem_fragmentation_ratio` -- values > 1.5 indicate fragmentation

### Step 2: Check eviction and key statistics

```bash
kubectl exec -n production deploy/backend -- bash -c 'redis-cli -h ${REDIS_HOST} -p 6379 INFO stats | grep -E "evicted_keys|keyspace_hits|keyspace_misses|expired_keys"'
```

```bash
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO keyspace
```

### Step 3: Identify large keys consuming memory

```bash
# Scan for the largest keys (non-blocking, production-safe)
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 --bigkeys
```

```bash
# Check memory usage of a specific suspicious key
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 MEMORY USAGE ${KEY_NAME}
```

### Step 4: Check keys without TTL

```bash
# Sample keys from a database and check TTL (-1 = no expiry)
kubectl exec -n production deploy/backend -- bash -c '
  redis-cli -h ${REDIS_HOST} -p 6379 SCAN 0 COUNT 100 | tail -n +2 | while read key; do
    ttl=$(redis-cli -h ${REDIS_HOST} -p 6379 TTL "$key")
    if [ "$ttl" = "-1" ]; then echo "NO_TTL: $key"; fi
  done
'
```

### Step 5: Check connected clients

```bash
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO clients
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 CLIENT LIST
```

## Mitigation Steps

### Scenario A: Stale or oversized cache keys

Keys that are no longer needed or have grown unexpectedly large.

1. Identify the offending key pattern:
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 --bigkeys
   ```

2. Delete specific stale keys:
   ```bash
   # Delete a single known key
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 DEL ${KEY_NAME}

   # Delete keys matching a pattern (use UNLINK for async, non-blocking delete)
   kubectl exec -n production deploy/backend -- bash -c '
     redis-cli -h ${REDIS_HOST} -p 6379 SCAN 0 MATCH "${KEY_PATTERN}:*" COUNT 1000 \
       | tail -n +2 \
       | xargs -I {} redis-cli -h ${REDIS_HOST} -p 6379 UNLINK {}
   '
   ```

3. Set TTLs on keys missing expiry:
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
     EXPIRE ${KEY_NAME} 3600
   ```

### Scenario B: Celery result backend bloat

Celery stores task results in Redis by default. Results without TTL accumulate.

1. Check Celery result key count:
   ```bash
   # WARNING: Do NOT use the KEYS command in production -- it blocks Redis while scanning the entire keyspace.
   # Use SCAN with a pattern match to count keys safely.
   kubectl exec -n production deploy/backend -- bash -c '
     cursor=0; count=0
     while true; do
       result=$(redis-cli -h ${REDIS_HOST} -p 6379 SCAN $cursor MATCH "celery-task-meta-*" COUNT 1000)
       cursor=$(echo "$result" | head -1)
       keys=$(echo "$result" | tail -n +2)
       if [ -n "$keys" ]; then
         count=$((count + $(echo "$keys" | wc -l)))
       fi
       if [ "$cursor" = "0" ]; then break; fi
     done
     echo "Celery result keys: $count"
   '
   ```

2. Flush stale Celery results (database 1, if separated):
   ```bash
   # Count keys in db 1
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 -n 1 DBSIZE

   # Flush only the Celery result backend database
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 -n 1 FLUSHDB ASYNC
   ```

3. Fix in application -- set `result_expires` in Celery config:
   ```python
   # backend/core/celery_config.py
   result_expires = 3600  # 1 hour
   ```

### Scenario C: Memory fragmentation

High `mem_fragmentation_ratio` (>1.5) wastes allocatable memory.

1. Check fragmentation:
   ```bash
   kubectl exec -n production deploy/backend -- bash -c 'redis-cli -h ${REDIS_HOST} -p 6379 INFO memory | grep mem_fragmentation_ratio'
   ```

2. Enable active defragmentation (if not already):
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
     CONFIG SET activedefrag yes
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
     CONFIG SET active-defrag-threshold-lower 10
   ```

### Scenario D: Eviction policy misconfiguration

1. Check current eviction policy:
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
     CONFIG GET maxmemory-policy
   ```

2. Set appropriate policy (recommended: `allkeys-lru` for cache workloads):
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
     CONFIG SET maxmemory-policy allkeys-lru
   ```

### Scenario E: Vertical scaling (increase node size)

When legitimate workload has outgrown the instance.

1. Check current instance type:
   ```bash
   aws elasticache describe-cache-clusters \
     --cache-cluster-id cloudthinker-redis-prod \
     --show-cache-node-info \
     --query 'CacheClusters[0].{Type:CacheNodeType,Engine:Engine,Nodes:CacheNodes}' \
     --output table
   ```

2. Scale to a larger node type.

   > **Note**: `modify-cache-cluster` cannot change `--cache-node-type` directly.
   > If using a replication group, use `modify-replication-group` with `--cache-node-type`.
   > For standalone clusters, you must create a new cluster with the desired node type,
   > restore from a snapshot, and switch over.

   ```bash
   # For replication groups (recommended setup): scale the node type (causes brief failover)
   aws elasticache modify-replication-group \
     --replication-group-id cloudthinker-redis-prod \
     --cache-node-type ${NEW_NODE_TYPE} \
     --apply-immediately
   ```

3. Monitor the modification:
   ```bash
   watch -n 10 "aws elasticache describe-cache-clusters \
     --cache-cluster-id cloudthinker-redis-prod \
     --query 'CacheClusters[0].CacheClusterStatus' --output text"
   ```

## Verification

```bash
# Confirm memory usage is below threshold
kubectl exec -n production deploy/backend -- bash -c 'redis-cli -h ${REDIS_HOST} -p 6379 INFO memory | grep used_memory_human'

# Confirm evictions have stopped
kubectl exec -n production deploy/backend -- bash -c 'redis-cli -h ${REDIS_HOST} -p 6379 INFO stats | grep evicted_keys'

# Confirm Celery tasks are processing normally
kubectl exec -n production deploy/backend -- celery -A app.celery inspect active_queues

# Check Datadog monitor has returned to OK
# https://app.datadoghq.com/monitors/manage?q=RedisMemoryUsageHigh
```

Expected: `used_memory_human` below 80% of `maxmemory`, eviction rate at zero, Celery queues draining normally.

## Rollback

If eviction policy change caused unexpected key loss:

```bash
# Revert to previous eviction policy
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  CONFIG SET maxmemory-policy ${PREVIOUS_POLICY}
```

If a `FLUSHDB` was performed in error on the wrong database:

- Redis does not support undo for `FLUSHDB`. Restore from the latest ElastiCache snapshot:
  ```bash
  aws elasticache describe-snapshots \
    --cache-cluster-id cloudthinker-redis-prod \
    --query 'Snapshots | sort_by(@, &NodeSnapshots[0].SnapshotCreateTime) | [-1]'
  ```

If vertical scaling causes issues, revert to the previous node type using `modify-replication-group` with the original node type.

## Escalation

| Condition | Contact |
|-----------|---------|
| Memory above 90% and rising after 15 min | @backend-team-lead |
| Complete OOM -- write failures across services | @incident-commander |
| Vertical scaling needed (cost approval) | @platform-team-lead |
| Suspected data loss from flush | @vp-engineering |
| ElastiCache API errors or AWS service issue | Open AWS Support case (Severity: Urgent) |

## Related Runbooks

- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-15 | @jchen | Added Celery result backend scenario |
| 2025-09-20 | @mpark | Added vertical scaling steps |
| 2025-06-10 | @asingh | Initial version |
