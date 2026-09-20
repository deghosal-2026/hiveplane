# OOMKilled Pod Recovery

| **Metadata**       | **Value**                                      |
|--------------------|------------------------------------------------|
| **Severity**       | High                                           |
| **Tags**           | `kubernetes`, `pods`, `oom`, `memory`          |
| **Alert**          | `KubePodOOMKilled`                             |
| **Owner**          | @your-team                                     |
| **Last Reviewed**  | 2026-02-11                                     |

---

## Summary

This runbook addresses pods terminated by the Linux kernel Out-of-Memory (OOM) killer, indicated by exit code 137 and termination reason `OOMKilled`. This occurs when a container exceeds its memory limit or the node runs out of memory. Recovery requires identifying the root cause—whether it's undersized memory limits, application memory leaks, or runtime misconfiguration—and applying the appropriate mitigation.

---

## Impact

- **Service Availability**: Affected pods restart continuously, causing service disruptions
- **Performance Degradation**: Frequent pod restarts impact response times and throughput
- **Data Loss Risk**: In-flight requests and transactions may be lost during abrupt termination
- **Cascading Failures**: OOMKilled pods can trigger autoscaling events or increase load on healthy replicas

---

## Prerequisites

- `kubectl` CLI installed and configured with cluster access
- AWS CLI configured (for EKS clusters)
- Access to cluster monitoring tools (Prometheus, CloudWatch, etc.)
- Permissions to update deployment resources
- Understanding of application memory requirements

---

## Triage & Diagnosis

### Step 1: Identify OOMKilled Pods

List all pods and filter for restart/termination issues:

```bash
# List pods with restarts
kubectl get pods -n ${NAMESPACE} --field-selector=status.phase!=Running

# Check all pods for restart count
kubectl get pods -n ${NAMESPACE} -o wide

# Filter pods with OOMKilled status
kubectl get pods -n ${NAMESPACE} -o json | jq '.items[] | select(.status.containerStatuses[]?.lastState.terminated.reason == "OOMKilled") | {name: .metadata.name, restarts: .status.containerStatuses[0].restartCount}'
```

### Step 2: Verify OOMKilled Exit Code

Describe the pod to confirm exit code 137 and termination reason:

```bash
# Describe specific pod
kubectl describe pod ${POD_NAME} -n ${NAMESPACE}

# Look for:
# - Last State: Terminated
# - Reason: OOMKilled
# - Exit Code: 137

# Get container status details
kubectl get pod ${POD_NAME} -n ${NAMESPACE} -o jsonpath='{.status.containerStatuses[*].lastState.terminated}' | jq
```

### Step 3: Check Current Memory Requests/Limits

Review configured memory resources:

```bash
# Get deployment memory configuration
kubectl get deployment ${DEPLOYMENT_NAME} -n ${NAMESPACE} -o jsonpath='{.spec.template.spec.containers[*].resources}' | jq

# Get pod memory configuration
kubectl get pod ${POD_NAME} -n ${NAMESPACE} -o jsonpath='{.spec.containers[*].resources}' | jq

# List all deployments with memory limits
kubectl get deployments -n ${NAMESPACE} -o custom-columns=NAME:.metadata.name,MEMORY_REQUEST:.spec.template.spec.containers[0].resources.requests.memory,MEMORY_LIMIT:.spec.template.spec.containers[0].resources.limits.memory
```

### Step 4: Analyze Memory Usage Trends

Check current and historical memory usage:

```bash
# Check current pod memory usage
kubectl top pod ${POD_NAME} -n ${NAMESPACE}

# Check all pods in namespace
kubectl top pods -n ${NAMESPACE} --sort-by=memory

# Get metrics from metrics-server API (if available)
kubectl get --raw /apis/metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/pods/${POD_NAME} | jq '.containers[].usage.memory'
```

For historical data (Prometheus/CloudWatch):

```bash
# Example Prometheus query (adjust for your setup)
# container_memory_usage_bytes{pod="${POD_NAME}", namespace="${NAMESPACE}"}

# CloudWatch Insights query (EKS)
aws logs insights start-query \
  --log-group-name /aws/containerinsights/${CLUSTER_NAME}/performance \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --query-string 'fields @timestamp, PodName, pod_memory_utilization | filter PodName = "${POD_NAME}" | sort @timestamp desc'
```

### Step 5: Check Node Memory Pressure

Verify if the node itself is under memory pressure:

```bash
# Check node conditions
kubectl describe node ${NODE_NAME} | grep -A 5 "Conditions:"

# Look for MemoryPressure: True

# Check node memory allocatable vs. capacity
kubectl describe node ${NODE_NAME} | grep -A 5 "Allocatable:"

# Get top memory-consuming pods on node
kubectl get pods --all-namespaces --field-selector spec.nodeName=${NODE_NAME} -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,MEMORY_LIMIT:.spec.containers[*].resources.limits.memory
```

### Step 6: Check Kernel OOM Events

Access node to check dmesg/kernel logs:

```bash
# For EKS with SSM access
aws ssm start-session --target ${INSTANCE_ID}

# Once on node, check kernel logs
sudo dmesg -T | grep -i "out of memory\|oom\|killed process"

# Check for OOM killer invocations
sudo journalctl -k | grep -i oom

# Get detailed OOM event info
sudo grep -i oom /var/log/messages
```

---

## Mitigation Steps

### Scenario A: Memory Limit Too Low

**Indicators**:
- Pod consistently hits memory limit shortly after startup
- Memory usage plateaus at or near limit
- Application behavior is normal otherwise

**Resolution**:

1. **Determine appropriate memory limits** based on application type:

   | **Service Type**        | **Typical Request** | **Typical Limit** | **Notes**                          |
   |-------------------------|---------------------|-------------------|------------------------------------|
   | Backend API (FastAPI)   | 256Mi               | 512Mi             | Low memory footprint               |
   | Backend with caching    | 512Mi               | 1Gi               | Redis/in-memory cache              |
   | Worker (Celery)         | 512Mi-1Gi           | 1Gi-2Gi           | Job processing, batch operations   |
   | Frontend (Next.js)      | 256Mi               | 512Mi             | Static serving, SSR                |
   | Database proxy/sidecar  | 128Mi               | 256Mi              | Connection pooling                 |
   | ML/AI workload          | 2Gi-4Gi             | 4Gi-8Gi           | Model loading, inference           |
   | Java/JVM application    | 1Gi                 | 2Gi               | Heap + metaspace + overhead        |

2. **Increase memory limits** via kubectl:

   ```bash
   # Increase memory limit for deployment
   kubectl set resources deployment ${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     --limits=memory=1Gi \
     --requests=memory=512Mi

   # Verify the change
   kubectl get deployment ${DEPLOYMENT_NAME} -n ${NAMESPACE} -o jsonpath='{.spec.template.spec.containers[*].resources}' | jq

   # Monitor rollout
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}
   ```

3. **Alternative: Patch deployment YAML**:

   ```bash
   kubectl patch deployment ${DEPLOYMENT_NAME} -n ${NAMESPACE} --type='json' -p='[
     {
       "op": "replace",
       "path": "/spec/template/spec/containers/0/resources/limits/memory",
       "value": "1Gi"
     },
     {
       "op": "replace",
       "path": "/spec/template/spec/containers/0/resources/requests/memory",
       "value": "512Mi"
     }
   ]'
   ```

4. **For critical production changes**, update via GitOps/IaC:

   ```bash
   # Edit deployment manifest
   vim manifests/${DEPLOYMENT_NAME}.yaml

   # Update resources section:
   #   resources:
   #     requests:
   #       memory: "512Mi"
   #     limits:
   #       memory: "1Gi"

   # Apply via kubectl or commit to GitOps repo
   kubectl apply -f manifests/${DEPLOYMENT_NAME}.yaml
   ```

### Scenario B: Memory Leak in Application

**Indicators**:
- Memory usage grows steadily over time
- Memory does not stabilize after warmup period
- OOMKills occur after hours/days of uptime
- Memory usage increases with request count but doesn't decrease

**Resolution**:

1. **Capture diagnostic data** before restart:

   ```bash
   # For JVM applications - get heap dump
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- jcmd 1 GC.heap_dump /tmp/heap_dump.hprof

   # Copy heap dump locally
   kubectl cp ${NAMESPACE}/${POD_NAME}:/tmp/heap_dump.hprof ./heap_dump_$(date +%Y%m%d_%H%M%S).hprof

   # For Python applications - get memory profiler output
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- python -m memory_profiler ${SCRIPT_PATH}

   # Get process memory map
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- cat /proc/1/status | grep -i mem
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- cat /proc/1/smaps
   ```

2. **Immediate mitigation - restart deployment**:

   ```bash
   # Rolling restart
   kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}

   # Monitor restart
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}

   # Verify pods are healthy
   kubectl get pods -n ${NAMESPACE} -l app=${APP_LABEL}
   ```

3. **Temporarily increase memory** to buy time for investigation:

   ```bash
   kubectl set resources deployment ${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     --limits=memory=2Gi \
     --requests=memory=1Gi
   ```

4. **Escalate to development team**:

   - Provide heap dump or memory profiler data
   - Share memory growth graphs from monitoring
   - Include application logs leading up to OOMKill
   - Document reproduction steps if known

5. **Monitor for recurrence**:

   ```bash
   # Watch pod memory usage
   watch kubectl top pod -n ${NAMESPACE} -l app=${APP_LABEL}

   # Set up alert for memory threshold (example with kubectl)
   kubectl get pod ${POD_NAME} -n ${NAMESPACE} --watch -o json | jq '.status.containerStatuses[].usage.memory'
   ```

### Scenario C: JVM/Runtime Heap Misconfiguration

**Indicators**:
- JVM/runtime heap size not aligned with container memory limit
- Container has 1Gi limit but JVM `-Xmx` set to 1Gi (doesn't account for non-heap overhead)
- Python multiprocessing spawning too many worker processes
- Node.js `--max-old-space-size` misconfigured

**Resolution**:

1. **For JVM applications** - adjust heap to 75% of container limit:

   ```bash
   # Example: Container limit = 2Gi, set heap to ~1536Mi
   kubectl set env deployment/${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     JAVA_OPTS="-Xms1024m -Xmx1536m -XX:MaxMetaspaceSize=256m"

   # Alternative: Use percentage-based sizing (Java 11+)
   kubectl set env deployment/${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     JAVA_OPTS="-XX:InitialRAMPercentage=50.0 -XX:MaxRAMPercentage=75.0"
   ```

2. **For Python applications** - limit worker processes:

   ```bash
   # Celery worker example - limit concurrency
   kubectl set env deployment/${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     CELERY_WORKER_CONCURRENCY="4" \
     CELERY_WORKER_MAX_TASKS_PER_CHILD="1000"

   # Gunicorn example - workers formula: (2 x CPU) + 1
   kubectl set env deployment/${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     GUNICORN_WORKERS="5" \
     GUNICORN_THREADS="2"
   ```

3. **For Node.js applications** - set heap size:

   ```bash
   # Set max heap to 80% of container limit (1Gi limit → 800Mi heap)
   kubectl set env deployment/${DEPLOYMENT_NAME} \
     -n ${NAMESPACE} \
     NODE_OPTIONS="--max-old-space-size=800"
   ```

4. **Verify configuration** after deployment:

   ```bash
   # Check environment variables
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- env | grep -E "JAVA_OPTS|NODE_OPTIONS|CELERY"

   # For JVM - verify heap settings
   kubectl exec ${POD_NAME} -n ${NAMESPACE} -- java -XX:+PrintFlagsFinal -version | grep -E "HeapSize|MetaspaceSize"
   ```

---

## Verification

After applying mitigation, verify the fix:

```bash
# 1. Check pod is running and healthy
kubectl get pods -n ${NAMESPACE} -l app=${APP_LABEL}

# Ensure:
# - STATUS = Running
# - RESTARTS = 0 (or not increasing)

# 2. Monitor pod for at least 10 minutes
watch kubectl top pod -n ${NAMESPACE} -l app=${APP_LABEL}

# Verify:
# - Memory usage is stable or growing slowly
# - Memory stays well below limit (< 80%)

# 3. Check for any new OOMKill events
kubectl get events -n ${NAMESPACE} --field-selector reason=OOMKilling --sort-by='.lastTimestamp'

# Should return no recent events

# 4. Verify application logs are healthy
kubectl logs -n ${NAMESPACE} ${POD_NAME} --tail=100

# Look for:
# - No error messages
# - Normal application startup
# - Successful request processing

# 5. Run smoke tests
kubectl exec ${POD_NAME} -n ${NAMESPACE} -- curl -f http://localhost:${PORT}/health

# 6. For long-term verification (24-48 hours)
# Set up monitoring alert for:
# - Pod restart count increase
# - Memory utilization > 90% for > 5 minutes
# - OOMKill events
```

---

## Rollback

If the mitigation causes issues, rollback the changes:

```bash
# 1. Rollback deployment to previous version
kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}

# 2. Check rollout status
kubectl rollout status deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}

# 3. Verify rollback
kubectl get deployment ${DEPLOYMENT_NAME} -n ${NAMESPACE} -o jsonpath='{.spec.template.spec.containers[*].resources}' | jq

# 4. If resource changes were applied via kubectl set, restore original values
kubectl set resources deployment ${DEPLOYMENT_NAME} \
  -n ${NAMESPACE} \
  --limits=memory=${ORIGINAL_LIMIT} \
  --requests=memory=${ORIGINAL_REQUEST}

# 5. Monitor pods after rollback
kubectl get pods -n ${NAMESPACE} -l app=${APP_LABEL} --watch
```

**Rollback considerations**:
- If OOMKills resume after rollback, the original limit was indeed too low
- Investigate alternative solutions (optimize application, vertical pod autoscaling)
- Consider temporary memory increase while root cause is addressed

---

## Escalation

Escalate to the next level if:

- **Memory leak confirmed** → Development team for code fix
  - Provide heap dump, memory profiler data, reproduction steps
  - Share monitoring graphs showing memory growth over time

- **Node-level memory pressure** → Infrastructure/SRE team
  - Nodes consistently at > 90% memory utilization
  - Multiple unrelated pods experiencing OOMKills
  - May need cluster scaling or node type upgrade

- **Persistent OOMKills after limit increase** → Application architecture team
  - Memory requirements exceed reasonable container limits (> 8Gi)
  - Need to redesign for horizontal scaling or external caching
  - Consider memory-optimized instance types or pods

- **Critical production service down** → Incident response team
  - Activate incident response protocol
  - Consider immediate rollback or failover to backup region
  - Engage on-call developer and SRE teams

**Escalation checklist**:
- [ ] Documented all diagnostic steps taken
- [ ] Collected relevant logs and metrics
- [ ] Identified affected services and user impact
- [ ] Prepared timeline of events
- [ ] Noted any temporary mitigations applied

---

## Related Runbooks

- [Pod CrashLoopBackOff Recovery](pod-crashloopbackoff.md) - Pod restart loop troubleshooting
- [Node Not Ready](node-not-ready.md) - Node-level resource pressure
- [High CPU Usage on EKS](high-cpu-usage-eks.md) - Related resource constraint issues
- [Horizontal Pod Autoscaler Issues](hpa-not-scaling.md) - Scaling based on resource metrics

---

## Changelog

| **Date**   | **Author**       | **Changes**                    |
|------------|------------------|--------------------------------|
| 2026-02-11 | @your-team       | Initial version                |
