# High CPU Usage on EKS Cluster

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | All (service-a, service-b, worker-a, worker-b) |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |
| **Alert** | `EC2CPUUtilization`, `KubeNodeHighCPU` |
| **Tags** | `kubernetes`, `eks`, `cpu`, `resource-limits`, `scheduling` |

## Summary

One or more EKS nodes are experiencing sustained high CPU usage (above 80%), often caused by misconfigured resource requests/limits, uneven pod distribution across nodes, or runaway workloads. This runbook walks through diagnosing the root cause and restoring healthy CPU utilization.

## Impact

- **Service degradation**: CPU throttling causes increased latency and request timeouts across all pods on the affected node.
- **Pod evictions**: If CPU pressure persists, the kubelet may evict lower-priority pods.
- **Cascading failures**: Overloaded nodes cause health check failures, triggering unnecessary pod restarts that further increase CPU load.
- **Scheduling imbalance**: New pods continue to land on already-stressed nodes if resource requests are underspecified.

## Prerequisites

- `kubectl` configured with access to the target EKS cluster
- AWS IAM credentials with `ec2:Describe*`, `cloudwatch:GetMetricData` permissions
- Access to CloudWatch or Datadog for CPU metrics
- Familiarity with Kubernetes resource management (requests, limits, QoS classes)

## Triage & Diagnosis

### Step 1: Identify high-CPU nodes

```bash
kubectl top nodes
```

```bash
kubectl get nodes -o wide
```

Compare CPU usage across nodes to detect imbalances.

### Step 2: Check CloudWatch for CPU trends

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=${INSTANCE_ID} \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average Maximum \
  --region ${AWS_REGION}
```

### Step 3: Check pod distribution across nodes

```bash
kubectl get pods --all-namespaces -o wide --no-headers | awk '{print $8}' | sort | uniq -c | sort -rn
```

```bash
kubectl get pods --all-namespaces -o wide --field-selector spec.nodeName=${HIGH_CPU_NODE}
```

### Step 4: Identify CPU-hungry pods

```bash
kubectl top pods --all-namespaces --sort-by=cpu --no-headers | head -20
```

```bash
kubectl top pods --all-namespaces --sort-by=cpu --field-selector spec.nodeName=${HIGH_CPU_NODE} --no-headers | head -20
```

### Step 5: Inspect resource requests and limits

```bash
kubectl get pods --all-namespaces -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,CPU_REQ:.spec.containers[0].resources.requests.cpu,CPU_LIM:.spec.containers[0].resources.limits.cpu,NODE:.spec.nodeName' | grep ${HIGH_CPU_NODE}
```

Look for pods with extremely high limits relative to requests (e.g., 10m request / 10010m limit), which allow unbounded CPU bursts.

### Step 6: Check for pod restarts indicating resource pressure

```bash
kubectl get pods --all-namespaces --field-selector spec.nodeName=${HIGH_CPU_NODE} --sort-by='.status.containerStatuses[0].restartCount' -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,STATUS:.status.phase'
```

### Step 7: Determine root cause category

| Pattern | Likely Cause |
|---------|-------------|
| Single node >80% CPU, other nodes <50% | Uneven pod distribution |
| High limits with tiny requests (e.g., 10m req / 10000m limit) | Resource misconfiguration |
| All nodes high CPU, no headroom | Cluster under-provisioned |
| One deployment consuming disproportionate CPU | Runaway workload |

## Mitigation Steps

### Scenario A: Misconfigured CPU Resource Limits

Pods with extreme CPU limits (e.g., 10010m) and minimal requests (e.g., 10m) can burst to consume entire node CPU.

1. Identify misconfigured deployments:

   ```bash
   kubectl get deployments --all-namespaces -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,CPU_REQ:.spec.template.spec.containers[0].resources.requests.cpu,CPU_LIM:.spec.template.spec.containers[0].resources.limits.cpu'
   ```

2. Fix CPU resources on the affected deployment:

   ```bash
   kubectl set resources deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE} \
     --requests=cpu=500m \
     --limits=cpu=2000m
   ```

3. Verify the rollout:

   ```bash
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE} --timeout=120s
   ```

4. Confirm CPU usage drops:

   ```bash
   kubectl top pods -n ${NAMESPACE} -l app=${APP_LABEL}
   ```

### Scenario B: Uneven Pod Distribution

The Kubernetes scheduler placed too many workloads on a single node, often because resource requests are too low to trigger balanced scheduling.

1. Cordon the overloaded node to prevent new pods:

   ```bash
   kubectl cordon ${HIGH_CPU_NODE}
   ```

2. Drain the node to force rescheduling:

   ```bash
   kubectl drain ${HIGH_CPU_NODE} \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --grace-period=60 \
     --timeout=300s
   ```

3. Uncordon after pods have redistributed:

   ```bash
   kubectl uncordon ${HIGH_CPU_NODE}
   ```

4. Verify balanced distribution:

   ```bash
   kubectl get pods --all-namespaces -o wide --no-headers | awk '{print $8}' | sort | uniq -c | sort -rn
   ```

### Scenario C: Runaway Workload

A single deployment is consuming excessive CPU due to a bug, infinite loop, or misconfiguration.

1. Identify the top CPU consumer:

   ```bash
   kubectl top pods --all-namespaces --sort-by=cpu --no-headers | head -5
   ```

2. Check recent changes to the deployment:

   ```bash
   kubectl rollout history deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}
   ```

3. If caused by a recent deployment, roll back:

   ```bash
   kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}
   ```

4. If not a recent change, restart the deployment:

   ```bash
   kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}
   ```

5. Verify CPU usage drops:

   ```bash
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE} --timeout=120s
   kubectl top pods -n ${NAMESPACE} -l app=${APP_LABEL}
   ```

### Scenario D: Cluster Under-Provisioned

All nodes are running at high CPU with no headroom.

1. Check total cluster CPU allocation:

   ```bash
   kubectl describe nodes | grep -A 5 "Allocated resources"
   ```

2. Check the node group scaling configuration:

   ```bash
   aws eks describe-nodegroup \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME} \
     --query 'nodegroup.scalingConfig' \
     --output table \
     --region ${AWS_REGION}
   ```

3. If below max, scale up the node group:

   ```bash
   aws eks update-nodegroup-config \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME} \
     --scaling-config desiredSize=${NEW_DESIRED_SIZE} \
     --region ${AWS_REGION}
   ```

4. Wait for the new node to join:

   ```bash
   kubectl get nodes -w
   ```

## Verification

After applying the fix, confirm CPU has stabilized:

```bash
kubectl top nodes
```

```bash
kubectl top pods --all-namespaces --sort-by=cpu --no-headers | head -10
```

```bash
kubectl get pods --all-namespaces -o wide --no-headers | awk '{print $8}' | sort | uniq -c | sort -rn
```

**Expected**: All nodes below 70% CPU, pod distribution roughly balanced across nodes, no pod restarts increasing.

## Rollback

If the mitigation introduced new issues:

- **Resource change rollback**: Revert to previous deployment revision:

  ```bash
  kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}
  ```

- **Uncordon a drained node** if the drain was premature:

  ```bash
  kubectl uncordon ${HIGH_CPU_NODE}
  ```

- **Scale down node group** if extra capacity is no longer needed:

  ```bash
  aws eks update-nodegroup-config \
    --cluster-name ${CLUSTER_NAME} \
    --nodegroup-name ${NODEGROUP_NAME} \
    --scaling-config desiredSize=${ORIGINAL_DESIRED_SIZE} \
    --region ${AWS_REGION}
  ```

## Escalation

| Condition | Contact |
|-----------|---------|
| All nodes >80% CPU for >15 min | @incident-commander |
| CPU spike caused by security incident | @security-team + @incident-commander |
| Node group cannot scale (at max capacity) | @your-team-lead |
| Critical service affected | @service-owner + @incident-commander |

## Related Runbooks

- [Node Not Ready](node-not-ready.md)
- [Pod CrashLoopBackOff](pod-crashloopbackoff.md)
- [HPA Max Replicas Reached](hpa-max-replicas-reached.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-11 | @your-team | Initial version based on prior CPU incident |
