# Celery Worker Queue Backlog

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | celery (worker-high, worker-low) |
| **Owner** | @backend-team |
| **Last Reviewed** | 2025-11-28 |
| **Alert** | `CeleryQueueDepthHigh` |
| **Tags** | `application`, `celery`, `queue`, `workers` |

## Summary

Celery task queues have accumulated a backlog exceeding the configured threshold (default: 500 pending tasks for `worker-high`, 2000 for `worker-low`). Tasks are not being consumed at the rate they are produced, which delays background processing including runbook execution, cost analysis, and notification delivery.

## Impact

- Runbook execution tasks queue indefinitely, blocking automated remediation workflows.
- Cost analysis and cloud scanning jobs are delayed, producing stale optimization recommendations.
- Notification delivery (Slack, email) is delayed, impacting incident response time.
- Payment webhook processing via `payment-service` may timeout if relay tasks are stuck.
- If the queue grows unbounded, Redis memory pressure escalates (see related runbook).

## Prerequisites

- `kubectl` configured for the `production` EKS cluster
- `redis-cli` access to the Celery broker
- Familiarity with CloudThinker Celery task routing (`worker-high` for critical tasks, `worker-low` for batch/analytics)
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/celery-workers/celery-worker-overview`
- Access to Datadog monitor: `CeleryQueueDepthHigh`

## Triage & Diagnosis

### Step 1: Check queue depths

```bash
# Check all queue lengths in Redis
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  LLEN celery

kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  LLEN worker-high

kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  LLEN worker-low
```

### Step 2: Check worker pod status

```bash
# Check if worker pods are running
kubectl get pods -n production -l app=worker-high -o wide
kubectl get pods -n production -l app=worker-low -o wide

# Check for recent restarts or OOMKills
kubectl get pods -n production -l app.kubernetes.io/component=celery-worker \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"\n"}{end}'
```

### Step 3: Inspect active workers and their tasks

```bash
# List registered workers
kubectl exec -n production deploy/backend -- celery -A app.celery inspect active

# Check reserved (prefetched) tasks per worker
kubectl exec -n production deploy/backend -- celery -A app.celery inspect reserved

# Check registered task types
kubectl exec -n production deploy/backend -- celery -A app.celery inspect registered
```

### Step 4: Check for stuck or long-running tasks

```bash
# Show tasks that have been running for a long time
kubectl exec -n production deploy/backend -- celery -A app.celery inspect active \
  2>/dev/null | grep -E "started|id|name"

# Check worker logs for errors
kubectl logs -n production -l app=worker-high --tail=200 --since=30m | grep -iE "error|exception|timeout|traceback"
kubectl logs -n production -l app=worker-low --tail=200 --since=30m | grep -iE "error|exception|timeout|traceback"
```

### Step 5: Identify the task composition in the queue

```bash
# Peek at the next 10 tasks in worker-high queue
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  LRANGE worker-high 0 9

# Count task types in the queue (sample first 100)
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 \
  LRANGE worker-high 0 99 | python3 -c "
import sys, json
tasks = {}
for line in sys.stdin:
    try:
        body = json.loads(line)
        name = body.get('headers', {}).get('task', 'unknown')
        tasks[name] = tasks.get(name, 0) + 1
    except: pass
for k, v in sorted(tasks.items(), key=lambda x: -x[1]):
    print(f'{v:>6}  {k}')
"
```

## Mitigation Steps

### Scenario A: Workers are crashed or OOMKilled

Workers are not running, causing the queue to grow with no consumers.

1. Check pod status and recent events:
   ```bash
   kubectl describe pods -n production -l app=worker-high | grep -A 5 "State\|Events\|Reason"
   ```

2. Restart the worker deployment:
   ```bash
   kubectl rollout restart deployment/worker-high -n production
   kubectl rollout restart deployment/worker-low -n production
   ```

3. Monitor the rollout:
   ```bash
   kubectl rollout status deployment/worker-high -n production --timeout=120s
   kubectl rollout status deployment/worker-low -n production --timeout=120s
   ```

### Scenario B: Scale up workers to handle burst

Legitimate spike in task volume requires more consumer capacity.

1. Check current replica count:
   ```bash
   kubectl get deployment worker-high worker-low -n production \
     -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas
   ```

2. Scale up workers:
   ```bash
   # Scale worker-high (default: 3 replicas, max recommended: 10)
   kubectl scale deployment/worker-high -n production --replicas=${DESIRED_REPLICAS}

   # Scale worker-low (default: 2 replicas, max recommended: 8)
   kubectl scale deployment/worker-low -n production --replicas=${DESIRED_REPLICAS}
   ```

3. Verify new pods are consuming tasks:
   ```bash
   watch -n 10 "kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 LLEN worker-high && \
     kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 LLEN worker-low"
   ```

### Scenario C: Stuck / poison tasks blocking the queue

A specific task type is failing repeatedly and consuming worker slots.

1. Identify the failing task:
   ```bash
   kubectl logs -n production -l app=worker-high --tail=500 --since=1h | \
     grep -oE "Task [a-z_.]+\[" | sed 's/^Task //; s/\[$//' | sort | uniq -c | sort -rn | head -10
   ```

2. Revoke stuck tasks by ID:
   ```bash
   kubectl exec -n production deploy/backend -- celery -A app.celery control revoke ${TASK_ID} --terminate --signal=SIGKILL
   ```

3. Purge a specific queue if it contains only poison messages:
   ```bash
   # DANGER: This deletes ALL tasks in the queue. Confirm before running.
   kubectl exec -n production deploy/backend -- celery -A app.celery purge -Q ${QUEUE_NAME} -f
   ```

4. If a specific task type is the problem, disable it temporarily:
   ```bash
   # Add to backend ConfigMap or environment to block the task
   kubectl set env deployment/worker-high -n production \
     CELERY_TASK_REJECT_ON_WORKER_LOST=true

   # Restart to pick up the change
   kubectl rollout restart deployment/worker-high -n production
   ```

### Scenario D: Redis broker connectivity issues

Workers cannot connect to the Redis broker.

1. Test Redis connectivity from a worker pod:
   ```bash
   kubectl exec -n production $(kubectl get pod -n production -l app=worker-high -o jsonpath='{.items[0].metadata.name}') \
     -- redis-cli -h ${REDIS_HOST} -p 6379 PING
   ```

2. Check Redis connection count:
   ```bash
   kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 INFO clients | grep connected_clients
   ```

3. If Redis is unreachable, see the Redis Memory Pressure runbook for broker health steps.

## Verification

```bash
# Confirm queue depths are decreasing
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 LLEN worker-high
kubectl exec -n production deploy/backend -- redis-cli -h ${REDIS_HOST} -p 6379 LLEN worker-low

# Confirm workers are actively processing
kubectl exec -n production deploy/backend -- celery -A app.celery inspect active

# Check worker pod health
kubectl get pods -n production -l app.kubernetes.io/component=celery-worker -o wide

# Verify the Datadog monitor has cleared
# https://app.datadoghq.com/monitors/manage?q=CeleryQueueDepthHigh
```

Expected: Queue depths dropping steadily, all worker pods in `Running` state with 0 recent restarts, Datadog monitor returning to OK.

## Rollback

If scaling up workers caused resource pressure on the cluster:

```bash
# Scale back to default replica count
kubectl scale deployment/worker-high -n production --replicas=3
kubectl scale deployment/worker-low -n production --replicas=2
```

If a queue purge removed legitimate tasks:

- Tasks are not recoverable from Redis after purge. Downstream services that depend on those tasks will need to re-trigger them. Check the `executor` service for any pending runbook executions that need to be resubmitted.

If a rollout restart introduced a bad config:

```bash
kubectl rollout undo deployment/worker-high -n production
kubectl rollout undo deployment/worker-low -n production
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Queue depth still growing after 15 min of mitigation | @backend-team-lead |
| Worker pods in CrashLoopBackOff after restart | @platform-team |
| Payment webhook tasks delayed > 5 min | @payment-team + @incident-commander |
| Redis broker is down | @platform-team (see Redis runbook) |
| Suspected poison tasks from external integration | @integrations-team |

## Related Runbooks

- [Redis Memory Pressure / OOM](../database/redis-memory-pressure.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)
- [PVC Pending / Storage Full](../kubernetes/pvc-pending-storage-full.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-11-28 | @dlee | Added poison task identification steps |
| 2025-08-15 | @mpark | Added queue composition analysis script |
| 2025-05-22 | @asingh | Initial version |
