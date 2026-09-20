# HPA Max Replicas Reached

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Service** | backend, worker-high, worker-low, executor |
| **Owner** | @platform-team |
| **Last Reviewed** | 2026-01-22 |
| **Alert** | `HPAMaxReplicasReached` |
| **Tags** | `kubernetes`, `hpa`, `autoscaling`, `capacity` |

## Summary

A Horizontal Pod Autoscaler (HPA) in the `production` namespace has scaled a deployment to its maximum replica count and the target metric is still above the threshold. This means the service cannot scale further to handle the current load, which may lead to degraded performance or request failures.

## Impact

- **Latency increase**: The service cannot add more replicas to absorb traffic, causing response times to increase.
- **Request failures**: If load continues to grow, existing pods may become overwhelmed, returning 5xx errors or timing out.
- **Queue backlog**: For worker services (worker-high, worker-low), hitting max replicas means the Celery task queue will start backing up.
- **Cost implication**: Raising max replicas increases compute costs. This should be a deliberate decision.

## Prerequisites

- `kubectl` configured with access to the `production` EKS cluster
- Grafana dashboard: [HPA & Autoscaling](https://grafana.internal.cloudthinker.io/d/k8s-hpa/kubernetes-hpa-autoscaling?var-namespace=production)
- Datadog monitor: **HPAMaxReplicasReached**
- Knowledge of CloudThinker's HPA configuration defaults

## Triage & Diagnosis

### Step 1: Identify which HPA is maxed out

```bash
kubectl get hpa -n production
```

Look for HPAs where `REPLICAS` equals `MAXPODS` and `TARGETS` shows the current metric is above the target.

### Step 2: Get detailed HPA status

```bash
kubectl describe hpa ${HPA_NAME} -n production
```

Key fields to check:
- **Current Metrics** vs **Target**: How far above target is the current value?
- **Min/Max Replicas**: Current configured limits
- **Conditions**: Look for `ScalingLimited` condition with reason `TooManyReplicas`
- **Events**: Recent scaling decisions

### Step 3: Check current pod resource utilization

```bash
kubectl top pods -n production -l app=${SERVICE_NAME} --sort-by=cpu
```

```bash
kubectl top pods -n production -l app=${SERVICE_NAME} --sort-by=memory
```

### Step 4: Check if this is a traffic spike or sustained growth

Review the Grafana dashboard for the past 24 hours:

- [Request Rate - Backend](https://grafana.internal.cloudthinker.io/d/app-metrics/application-metrics?var-service=backend&var-timerange=24h)
- [Celery Queue Depth](https://grafana.internal.cloudthinker.io/d/celery-queues/celery-queue-metrics?var-timerange=24h)

Check Datadog APM for request volume trends:

```
service:backend env:production | stats count by @http.method, @http.route | top 20
```

### Step 5: Check node capacity

Verify there is room on the cluster to schedule more pods if we raise the limit:

```bash
kubectl top nodes
```

```bash
kubectl describe nodes | grep -A 5 "Allocated resources"
```

### Step 6: Review current HPA configuration

```bash
kubectl get hpa ${HPA_NAME} -n production -o yaml
```

CloudThinker default HPA configurations:

| Service | Min Replicas | Max Replicas | CPU Target | Memory Target |
|---------|-------------|-------------|------------|---------------|
| backend | 3 | 15 | 70% | - |
| worker-high | 2 | 10 | 80% | - |
| worker-low | 1 | 5 | 80% | - |
| executor | 2 | 8 | 60% | - |
| frontend | 2 | 10 | 70% | - |

## Mitigation Steps

### Scenario A: Temporary Traffic Spike (Raise Max Replicas)

If the load spike is temporary (marketing event, batch job, etc.) and expected to subside:

1. Raise the max replicas temporarily:

   ```bash
   kubectl patch hpa ${HPA_NAME} -n production \
     --type merge -p "{\"spec\":{\"maxReplicas\":${NEW_MAX_REPLICAS}}}"
   ```

   Recommended temporary increases:

   | Service | Default Max | Temporary Max |
   |---------|------------|---------------|
   | backend | 15 | 25 |
   | worker-high | 10 | 20 |
   | worker-low | 5 | 10 |
   | executor | 8 | 12 |

2. Verify the HPA is scaling up:

   ```bash
   kubectl get hpa ${HPA_NAME} -n production -w
   ```

3. Set a calendar reminder to revert the max replicas after the event:

   ```bash
   kubectl patch hpa ${HPA_NAME} -n production \
     --type merge -p "{\"spec\":{\"maxReplicas\":${ORIGINAL_MAX_REPLICAS}}}"
   ```

### Scenario B: Sustained Growth (Permanent Capacity Increase)

If traffic has grown organically and the current max is no longer sufficient:

1. Review the growth trend in Grafana over the past 30 days.

2. Calculate the new max replicas. A good rule of thumb is current max + 50%, rounded up:

   ```bash
   kubectl get hpa ${HPA_NAME} -n production -o jsonpath='{.spec.maxReplicas}'
   ```

3. Update the HPA in the Helm values or Kubernetes manifest (not just `kubectl patch`), so the change persists across deployments:

   ```yaml
   # values.yaml for the service
   autoscaling:
     enabled: true
     minReplicas: ${MIN_REPLICAS}
     maxReplicas: ${NEW_MAX_REPLICAS}
     targetCPUUtilizationPercentage: 70
   ```

4. Apply via the deployment pipeline or directly:

   ```bash
   kubectl patch hpa ${HPA_NAME} -n production \
     --type merge -p "{\"spec\":{\"maxReplicas\":${NEW_MAX_REPLICAS}}}"
   ```

5. Verify EKS node group has capacity. If nodes are also near capacity, request a node group scale-up:

   ```bash
   aws eks describe-nodegroup --cluster-name cloudthinker-production \
     --nodegroup-name ${NODEGROUP_NAME} \
     --query 'nodegroup.scalingConfig' --region us-east-1
   ```

   ```bash
   aws eks update-nodegroup-config --cluster-name cloudthinker-production \
     --nodegroup-name ${NODEGROUP_NAME} \
     --scaling-config minSize=${MIN_NODES},maxSize=${NEW_MAX_NODES},desiredSize=${DESIRED_NODES} \
     --region us-east-1
   ```

### Scenario C: Inefficient Resource Usage (Optimize Before Scaling)

If pods are hitting CPU/memory targets but the application is not efficiently handling load:

1. Check if there are slow queries or external dependency issues:

   ```bash
   kubectl logs -n production -l app=${SERVICE_NAME} --tail=200 | grep -i "slow\|timeout\|error"
   ```

2. Review Datadog APM for the service:

   - Look for endpoints with high P99 latency
   - Check for database connection pool exhaustion
   - Check Redis latency

3. If the root cause is a downstream bottleneck, fix that instead of scaling up. See related runbooks below.

4. Check if resource requests/limits are appropriately set:

   ```bash
   kubectl get deployment ${DEPLOYMENT_NAME} -n production \
     -o jsonpath='{.spec.template.spec.containers[0].resources}' | jq .
   ```

   If CPU requests are too high relative to actual usage, pods may not be packing efficiently on nodes. Consider right-sizing.

## Verification

After adjusting the HPA:

```bash
kubectl get hpa ${HPA_NAME} -n production
```

Confirm:
- `REPLICAS` is below `MAXPODS` (new limit)
- `TARGETS` metric is at or below the target percentage

Monitor for 15 minutes to ensure stable:

```bash
kubectl get hpa ${HPA_NAME} -n production -w
```

Check that the service is healthy:

```bash
curl -s https://api.cloudthinker.io/health | jq .
```

Verify the Datadog monitor **HPAMaxReplicasReached** has returned to OK.

## Rollback

If raising max replicas caused issues (e.g., node resource pressure, costs too high):

1. Revert the HPA max replicas:

   ```bash
   kubectl patch hpa ${HPA_NAME} -n production \
     --type merge -p "{\"spec\":{\"maxReplicas\":${ORIGINAL_MAX_REPLICAS}}}"
   ```

2. If node group was scaled up, revert:

   ```bash
   aws eks update-nodegroup-config --cluster-name cloudthinker-production \
     --nodegroup-name ${NODEGROUP_NAME} \
     --scaling-config minSize=${ORIGINAL_MIN_NODES},maxSize=${ORIGINAL_MAX_NODES},desiredSize=${ORIGINAL_DESIRED_NODES} \
     --region us-east-1
   ```

3. If the service needs to shed load, consider enabling rate limiting at the ingress level:

   ```bash
   kubectl annotate ingress ${INGRESS_NAME} -n production \
     nginx.ingress.kubernetes.io/limit-rps="100" \
     nginx.ingress.kubernetes.io/limit-burst-multiplier="5"
   ```

## Escalation

| Condition | Contact |
|-----------|---------|
| HPA maxed for more than 30 minutes | @platform-team-lead |
| Node group also at capacity | @platform-team-lead + @cloud-infrastructure |
| Service returning 5xx errors due to overload | @incident-commander |
| Worker queue backlog exceeding 10,000 tasks | @backend-team + @incident-commander |
| Cost increase exceeds $5,000/month projected | @finops-team |

## Related Runbooks

- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)
- [Idle Resource Cleanup](../cloud-cost/idle-resource-cleanup.md)
- [Right-sizing Over-provisioned Instances](../cloud-cost/right-sizing-instances.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-22 | @platform-team | Added node group scaling instructions |
| 2025-12-10 | @platform-team | Added default HPA configuration table |
| 2025-09-15 | @platform-team | Initial version |
