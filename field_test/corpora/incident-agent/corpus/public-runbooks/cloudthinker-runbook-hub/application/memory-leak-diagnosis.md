# Application Memory Leak Diagnosis

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | backend, worker-high, worker-low, executor |
| **Owner** | @backend-team |
| **Last Reviewed** | 2026-01-25 |
| **Alert** | `PodMemoryUsageHigh` / `OOMKilled` |
| **Tags** | `application`, `memory`, `oom`, `python`, `profiling`, `leak` |

## Summary

One or more pods are exhibiting continuously increasing memory usage that does not stabilize, eventually hitting container memory limits and being OOM-killed by the kernel. This runbook covers identifying leaking pods via Kubernetes metrics, Python-specific memory profiling using `tracemalloc`, `objgraph`, and the `gc` module, heap dump analysis, container memory limit tuning, and both temporary mitigations and long-term fixes.

## Impact

- **Pod restarts**: Pods hitting memory limits are OOM-killed and restarted by Kubernetes, causing brief service interruptions on each restart.
- **Request failures**: Active HTTP requests or Celery tasks in flight during an OOM kill are terminated without graceful shutdown, returning 502/503 to users.
- **Cascading load**: Repeated restarts cause load to shift to remaining healthy pods, potentially triggering a cascading failure if all replicas leak at similar rates.
- **Worker data loss**: `worker-high` tasks processing runbook executions or AI analysis may lose in-progress results when OOM-killed.
- **Resource waste**: Leaking pods consume cluster memory capacity, potentially preventing other pods from scheduling.
- **SLA risk**: Repeated OOM kills during peak traffic breach the 99.95% uptime SLA.

## Prerequisites

- `kubectl` access to `production` namespace
- Ability to `kubectl exec` into pods (for profiling)
- Python profiling tools available in container image: `tracemalloc` (stdlib), `objgraph`, `pympler`
- Access to Grafana: `https://grafana.internal.cloudthinker.io/d/k8s-pods/kubernetes-pod-resources`
- Access to Datadog: Monitors `PodMemoryUsageHigh`, `OOMKilled`
- Familiarity with Python memory management (reference counting, garbage collector, C extensions)

## Triage & Diagnosis

### Step 1: Identify Leaking Pods

```bash
# Check which pods have been OOM-killed recently
kubectl get events -n production --sort-by='.lastTimestamp' | grep -i "oom\|killing\|memory"
```

```bash
# Check pod restarts (high restart count = likely OOM)
kubectl get pods -n production --sort-by='.status.containerStatuses[0].restartCount' \
  -o custom-columns='NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,LAST_STATE:.status.containerStatuses[0].lastState.terminated.reason,STARTED:.status.containerStatuses[0].state.running.startedAt' \
  | grep -v "^NAME" | sort -t' ' -k2 -rn | head -15
```

```bash
# Check current memory usage vs limits for all production pods
kubectl top pods -n production --sort-by=memory | head -20
```

```bash
# Get detailed memory info for a specific suspected pod
kubectl describe pod ${POD_NAME} -n production | grep -A 5 "Limits:\|Requests:\|Last State:\|Restart Count:"
```

### Step 2: Confirm the Leak Pattern

```bash
# Check memory usage over time in Grafana
# https://grafana.internal.cloudthinker.io/d/k8s-pods/kubernetes-pod-resources?orgId=1&var-namespace=production&var-pod=${POD_NAME}&from=now-6h&to=now

# A leak shows as a steadily increasing line that never flattens,
# eventually hitting the limit and dropping (OOM kill + restart)
```

```bash
# Check container memory metrics from cgroup (inside the pod)

# cgroup v2 (EKS 1.28+, default)
kubectl exec -n production ${POD_NAME} -- cat /sys/fs/cgroup/memory.current
kubectl exec -n production ${POD_NAME} -- cat /sys/fs/cgroup/memory.max

# cgroup v1 (legacy)
kubectl exec -n production ${POD_NAME} -- cat /sys/fs/cgroup/memory/memory.usage_in_bytes
kubectl exec -n production ${POD_NAME} -- cat /sys/fs/cgroup/memory/memory.limit_in_bytes
```

```bash
# Calculate memory usage percentage (auto-detects cgroup v1 vs v2)
kubectl exec -n production ${POD_NAME} -- sh -c '
  if [ -f /sys/fs/cgroup/memory.current ]; then
    usage=$(cat /sys/fs/cgroup/memory.current)
    limit=$(cat /sys/fs/cgroup/memory.max)
  else
    usage=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)
    limit=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
  fi
  pct=$((usage * 100 / limit))
  echo "Usage: $((usage / 1024 / 1024)) MB / $((limit / 1024 / 1024)) MB ($pct%)"
'
```

### Step 3: Identify Which Deployment is Leaking

```bash
# Memory usage by deployment
for deploy in backend worker-high worker-low executor payment-service; do
  echo "=== ${deploy} ==="
  kubectl top pods -n production -l app.kubernetes.io/name=${deploy} --no-headers 2>/dev/null
done
```

```bash
# Check OOM kill history per deployment
for deploy in backend worker-high worker-low executor payment-service; do
  restarts=$(kubectl get pods -n production -l app.kubernetes.io/name=${deploy} \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' | \
    awk '{s+=$1} END {print s}')
  echo "${deploy}: ${restarts} total restarts"
done
```

### Step 4: Python Process Memory Analysis (Inside Pod)

```bash
# Get Python process memory usage from inside the pod
kubectl exec -n production ${POD_NAME} -- python3 -c "
import os, resource
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f'Peak RSS: {rss / 1024:.1f} MB')

import gc
gc.collect()
print(f'GC tracked objects: {len(gc.get_objects())}')
print(f'GC garbage (uncollectable): {len(gc.garbage)}')

generations = gc.get_stats()
for i, gen in enumerate(generations):
    print(f'Gen {i}: collections={gen[\"collections\"]}, collected={gen[\"collected\"]}, uncollectable={gen[\"uncollectable\"]}')
"
```

```bash
# Check if tracemalloc is enabled (should be via PYTHONTRACEMALLOC env var)
kubectl exec -n production ${POD_NAME} -- python3 -c "
import tracemalloc
if tracemalloc.is_tracing():
    snapshot = tracemalloc.take_snapshot()
    stats = snapshot.statistics('lineno')
    print('Top 20 memory allocations by line:')
    for stat in stats[:20]:
        print(f'  {stat}')
else:
    print('tracemalloc is NOT enabled. Set PYTHONTRACEMALLOC=1 env var.')
"
```

### Step 5: Check for Common Leak Patterns

```bash
# Check for growing caches or registries
kubectl exec -n production ${POD_NAME} -- python3 -c "
import sys
import gc

# Find objects with the most referrers (potential leak anchors)
gc.collect()
type_counts = {}
for obj in gc.get_objects():
    t = type(obj).__name__
    type_counts[t] = type_counts.get(t, 0) + 1

sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
print('Top 30 object types by count:')
for name, count in sorted_types[:30]:
    print(f'  {name}: {count}')
"
```

```bash
# Check for unclosed database connections or sessions
kubectl exec -n production ${POD_NAME} -- python3 -c "
import gc
gc.collect()
# Look for SQLAlchemy session objects
sessions = [obj for obj in gc.get_objects() if type(obj).__name__ in ('Session', 'scoped_session', 'AsyncSession')]
print(f'SQLAlchemy sessions in memory: {len(sessions)}')

# Look for httpx/aiohttp client objects
clients = [obj for obj in gc.get_objects() if type(obj).__name__ in ('AsyncClient', 'ClientSession')]
print(f'HTTP clients in memory: {len(clients)}')
"
```

## Mitigation Steps

### Scenario A: Immediate Mitigation -- Restart Leaking Pods

When immediate relief is needed before profiling can identify the root cause.

1. Perform a rolling restart of the leaking deployment:

   ```bash
   kubectl rollout restart deployment/${LEAKING_DEPLOYMENT} -n production
   kubectl rollout status deployment/${LEAKING_DEPLOYMENT} -n production --timeout=180s
   ```

2. Verify memory usage dropped after restart:

   ```bash
   sleep 30
   kubectl top pods -n production -l app.kubernetes.io/name=${LEAKING_DEPLOYMENT}
   ```

3. Set up a temporary CronJob to restart the deployment periodically until the fix is deployed:

   ```bash
   kubectl create cronjob restart-${LEAKING_DEPLOYMENT} \
     -n production \
     --image=bitnami/kubectl:latest \
     --schedule="0 */4 * * *" \
     -- kubectl rollout restart deployment/${LEAKING_DEPLOYMENT} -n production
   ```

   > **Note**: This is a temporary band-aid. Remove the CronJob after deploying the fix.

### Scenario B: Increase Memory Limits Temporarily

If the leak is slow and the pod needs more headroom while the fix is developed.

1. Check current limits:

   ```bash
   kubectl get deployment ${LEAKING_DEPLOYMENT} -n production \
     -o jsonpath='{.spec.template.spec.containers[0].resources}' | python3 -m json.tool
   ```

2. Increase memory limit (50% increase):

   ```bash
   kubectl set resources deployment/${LEAKING_DEPLOYMENT} -n production \
     --limits=memory=${NEW_MEMORY_LIMIT} \
     --requests=memory=${NEW_MEMORY_REQUEST}
   ```

   ```bash
   # Example: increase backend from 512Mi to 768Mi
   kubectl set resources deployment/backend -n production \
     --limits=memory=768Mi \
     --requests=memory=512Mi
   ```

3. Verify the rollout:

   ```bash
   kubectl rollout status deployment/${LEAKING_DEPLOYMENT} -n production --timeout=120s
   ```

### Scenario C: Python Memory Profiling with tracemalloc

Deep profiling to identify the exact code path causing the leak. Best done on a staging environment or a single production pod with reduced traffic.

1. Enable tracemalloc on the deployment:

   ```bash
   kubectl set env deployment/${LEAKING_DEPLOYMENT} -n production PYTHONTRACEMALLOC=1
   kubectl rollout status deployment/${LEAKING_DEPLOYMENT} -n production --timeout=120s
   ```

2. Take a baseline snapshot after startup:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import tracemalloc
   tracemalloc.start()
   import json
   snapshot = tracemalloc.take_snapshot()
   stats = snapshot.statistics('traceback')
   result = []
   for stat in stats[:30]:
       result.append({
           'size_kb': stat.size / 1024,
           'count': stat.count,
           'traceback': [str(frame) for frame in stat.traceback]
       })
   print(json.dumps(result, indent=2))
   " > /tmp/tracemalloc_baseline.json
   ```

   > **NOTE**: This `tracemalloc` snippet only traces allocations within its own script execution, not the running application process. For production profiling, enable tracemalloc at application startup by setting `PYTHONTRACEMALLOC=1` environment variable, or add `tracemalloc.start()` to the app's entrypoint.

3. Wait for memory to grow (30-60 minutes), then take a comparison snapshot:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import tracemalloc
   if not tracemalloc.is_tracing():
       print('ERROR: tracemalloc not active')
       exit(1)
   snapshot = tracemalloc.take_snapshot()
   stats = snapshot.statistics('lineno')
   print('=== Current Top Memory Consumers ===')
   for stat in stats[:30]:
       print(stat)
   print()
   current, peak = tracemalloc.get_traced_memory()
   print(f'Current: {current / 1024 / 1024:.1f} MB, Peak: {peak / 1024 / 1024:.1f} MB')
   "
   ```

4. Use objgraph to find reference chains to leaking objects:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import objgraph
   import gc

   gc.collect()

   # Show object types with the most growth
   # Run this twice with a delay to see growth
   print('=== Most Common Object Types ===')
   objgraph.show_most_common_types(limit=20)

   print()
   print('=== Object Growth Since Last Call ===')
   objgraph.show_growth(limit=20)
   "
   ```

5. Trace reference chains for suspicious objects:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import objgraph
   import gc

   gc.collect()

   # Find what is holding references to a specific type
   # Replace 'dict' with the leaking type from growth analysis
   leaking_objects = objgraph.by_type('${LEAKING_TYPE}')
   if leaking_objects:
       print(f'Found {len(leaking_objects)} objects of type ${LEAKING_TYPE}')
       # Show reference chain for one instance
       objgraph.show_chain(
           objgraph.find_backref_chain(
               leaking_objects[-1],
               objgraph.is_proper_module
           ),
           filename='/tmp/leak_chain.txt'
       )
       with open('/tmp/leak_chain.txt') as f:
           print(f.read())
   "
   ```

### Scenario D: Celery Worker Memory Leak

Celery workers accumulate memory over time due to task-level leaks, global state accumulation, or uncollected C-extension objects.

1. Check per-worker memory usage:

   ```bash
   kubectl exec -n production deploy/${WORKER_DEPLOYMENT} -- celery -A app.celery inspect stats \
     | python3 -c "
   import sys, json
   data = json.load(sys.stdin)
   for worker, stats in data.items():
       rusage = stats.get('rusage', {})
       print(f'{worker}:')
       print(f'  Max RSS: {rusage.get(\"maxrss\", 0) / 1024:.1f} MB')
       print(f'  Tasks completed: {stats.get(\"total\", {})}')
   "
   ```

2. Enable Celery's `worker_max_tasks_per_child` to auto-restart workers after N tasks:

   ```bash
   kubectl set env deployment/${WORKER_DEPLOYMENT} -n production \
     CELERY_WORKER_MAX_TASKS_PER_CHILD=500
   kubectl rollout status deployment/${WORKER_DEPLOYMENT} -n production --timeout=120s
   ```

3. Enable Celery's `worker_max_memory_per_child` (in KB) to auto-restart on memory threshold:

   ```bash
   kubectl set env deployment/${WORKER_DEPLOYMENT} -n production \
     CELERY_WORKER_MAX_MEMORY_PER_CHILD=524288
   kubectl rollout status deployment/${WORKER_DEPLOYMENT} -n production --timeout=120s
   ```

4. Check if worker tasks are accumulating global state:

   ```bash
   kubectl exec -n production deploy/${WORKER_DEPLOYMENT} -- python3 -c "
   import gc
   gc.collect()

   # Check for LangChain/LLM objects that may not be released
   langchain_objs = [obj for obj in gc.get_objects()
                     if type(obj).__module__ and 'langchain' in str(type(obj).__module__)]
   print(f'LangChain objects in memory: {len(langchain_objs)}')

   # Check for accumulated Qdrant client connections
   qdrant_objs = [obj for obj in gc.get_objects()
                  if 'qdrant' in str(type(obj).__name__).lower()]
   print(f'Qdrant-related objects: {len(qdrant_objs)}')

   # Check for Redis connection objects
   redis_objs = [obj for obj in gc.get_objects()
                 if type(obj).__name__ in ('Connection', 'ConnectionPool')
                 and 'redis' in str(type(obj).__module__)]
   print(f'Redis connections/pools: {len(redis_objs)}')
   "
   ```

### Scenario E: C-Extension or Native Memory Leak

Python's garbage collector only tracks Python objects. Memory allocated by C extensions (e.g., `numpy`, `pillow`, native libraries) is invisible to `gc` and `tracemalloc`.

1. Compare Python-tracked memory vs actual RSS:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import tracemalloc
   import resource

   rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
   rss_mb = rss_kb / 1024

   if tracemalloc.is_tracing():
       traced_current, traced_peak = tracemalloc.get_traced_memory()
       traced_mb = traced_current / 1024 / 1024
       print(f'RSS (actual):        {rss_mb:.1f} MB')
       print(f'Traced (Python):     {traced_mb:.1f} MB')
       print(f'Untraced (C/native): {rss_mb - traced_mb:.1f} MB')
       if (rss_mb - traced_mb) > rss_mb * 0.5:
           print('WARNING: >50% of memory is untraced -- likely C extension leak')
   else:
       print(f'RSS: {rss_mb:.1f} MB (tracemalloc not enabled)')
   "
   ```

2. If C-extension leak is confirmed, use `memory_profiler` for line-by-line analysis:

   > **WARNING**: Installing packages in production pods may fail on read-only filesystems and violates security best practices. Prefer using a debug sidecar or building a debug image with the tools pre-installed. Only use this as a last resort with approval from the security team.

   ```bash
   # Install memory_profiler in the pod (temporary)
   kubectl exec -n production ${POD_NAME} -- pip install memory_profiler

   # Profile a specific endpoint or function
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   from memory_profiler import profile
   import importlib

   # Example: profile a specific function
   # Adjust the import path to the suspected leaking function
   mod = importlib.import_module('app.services.${SUSPECTED_MODULE}')
   func = getattr(mod, '${SUSPECTED_FUNCTION}')
   profiled_func = profile(func)
   # Call with test parameters
   # profiled_func(...)
   "
   ```

3. Use `/proc/self/smaps` for detailed memory mapping:

   ```bash
   kubectl exec -n production ${POD_NAME} -- sh -c '
     echo "=== Memory Map Summary ==="
     awk "/^[0-9a-f]/ {region=\$6} /Rss:/ {rss[\$6 ? \$6 : region]+=\$2} END {for (r in rss) print rss[r] \" kB\t\" r}" /proc/self/smaps | sort -rn | head -20
   '
   ```

### Scenario F: Force Garbage Collection and Diagnose Uncollectable Objects

Python objects involved in reference cycles with `__del__` methods cannot be collected by the GC.

1. Force garbage collection and check for uncollectable objects:

   ```bash
   kubectl exec -n production ${POD_NAME} -- python3 -c "
   import gc

   # Enable GC debug logging
   gc.set_debug(gc.DEBUG_SAVEALL)

   # Force full collection
   collected = gc.collect()
   print(f'Collected {collected} objects')
   print(f'Uncollectable garbage: {len(gc.garbage)} objects')

   if gc.garbage:
       print('Types of uncollectable objects:')
       type_counts = {}
       for obj in gc.garbage[:100]:
           t = type(obj).__name__
           type_counts[t] = type_counts.get(t, 0) + 1
       for name, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
           print(f'  {name}: {count}')

   # Reset debug
   gc.set_debug(0)
   gc.garbage.clear()
   "
   ```

## Verification

After applying mitigation (restart, limit increase, or code fix):

```bash
# Monitor memory usage over 30 minutes (should stabilize, not continuously grow)
watch -n 60 'kubectl top pods -n production -l app.kubernetes.io/name=${LEAKING_DEPLOYMENT} --no-headers'
```

```bash
# Verify no OOM kills in recent events
kubectl get events -n production --sort-by='.lastTimestamp' | grep -i "oom\|killing" | tail -5
# Expected: no recent OOM events
```

```bash
# Check restart count is stable (not increasing)
kubectl get pods -n production -l app.kubernetes.io/name=${LEAKING_DEPLOYMENT} \
  -o custom-columns='NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp'
```

```bash
# Check Grafana memory graph shows stable plateau
# https://grafana.internal.cloudthinker.io/d/k8s-pods/kubernetes-pod-resources?orgId=1&var-namespace=production&var-pod=${POD_NAME}&from=now-1h&to=now
```

```bash
# Verify Datadog monitors recovered
# https://app.datadoghq.com/monitors/manage?q=PodMemoryUsageHigh
# https://app.datadoghq.com/monitors/manage?q=OOMKilled
```

**Expected**: Memory usage stabilizes at a consistent level after warmup. No OOM kills. Restart count is static. Datadog monitors in OK state.

## Rollback

If increased memory limits cause node pressure:

```bash
# Revert memory limits to original values
kubectl set resources deployment/${LEAKING_DEPLOYMENT} -n production \
  --limits=memory=${ORIGINAL_MEMORY_LIMIT} \
  --requests=memory=${ORIGINAL_MEMORY_REQUEST}
```

If `CELERY_WORKER_MAX_TASKS_PER_CHILD` causes excessive worker restarts:

```bash
# Remove the env var to disable auto-restart
kubectl set env deployment/${WORKER_DEPLOYMENT} -n production CELERY_WORKER_MAX_TASKS_PER_CHILD-
```

If the periodic restart CronJob is no longer needed:

```bash
kubectl delete cronjob restart-${LEAKING_DEPLOYMENT} -n production
```

If `PYTHONTRACEMALLOC` was enabled for profiling, disable it (it adds ~10% memory overhead):

```bash
kubectl set env deployment/${LEAKING_DEPLOYMENT} -n production PYTHONTRACEMALLOC-
kubectl rollout status deployment/${LEAKING_DEPLOYMENT} -n production --timeout=120s
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Single pod OOM-killed, auto-recovered | @backend-team (investigate, no page) |
| Multiple pods OOM-killed within 1 hour | @backend-team-lead |
| All replicas of a deployment OOM-killed | @incident-commander |
| Memory leak in payment-service | @payment-team-lead + @incident-commander |
| Leak traced to third-party library (LangChain, Qdrant client) | @backend-team-lead (file upstream issue) |
| C-extension leak requiring native profiling | @backend-team-lead + @platform-team |
| Node-level memory pressure from leaking pods | @platform-team (see [Node Not Ready](../kubernetes/node-not-ready.md)) |

## Related Runbooks

- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [Celery Worker Queue Backlog](celery-worker-queue-backlog.md)
- [API P99 Latency SLA Breach](api-high-latency.md)
- [PostgreSQL Connection Pool Exhaustion](../database/postgres-connection-pool-exhaustion.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-25 | @backend-team | Added Scenario F (uncollectable objects with __del__) |
| 2025-11-12 | @backend-team | Added C-extension leak diagnosis (Scenario E) |
| 2025-08-30 | @backend-team | Added Celery worker-specific profiling steps |
| 2025-06-15 | @sre-team | Added cgroup v2 memory paths, updated for EKS 1.28 |
| 2025-03-20 | @backend-team | Initial version |
