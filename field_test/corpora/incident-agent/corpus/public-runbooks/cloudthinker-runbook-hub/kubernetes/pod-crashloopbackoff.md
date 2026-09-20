# Pod CrashLoopBackOff

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Service** | backend, worker-high, worker-low, executor, payment-service |
| **Owner** | @platform-team |
| **Last Reviewed** | 2026-01-15 |
| **Alert** | `KubePodCrashLooping` |
| **Tags** | `kubernetes`, `pods`, `crashloop`, `restart` |

## Summary

A pod in the `production` namespace is stuck in a CrashLoopBackOff state, meaning Kubernetes is repeatedly restarting it after failures. This runbook walks through diagnosing the root cause and resolving the most common failure scenarios.

## Impact

- **Service degradation**: If enough replicas are crash-looping, the service may become partially or fully unavailable.
- **Cascading failures**: Upstream services depending on the affected pod may start timing out or returning errors.
- **Alert fatigue**: Persistent crash loops generate continuous alerts in Datadog.

## Prerequisites

- `kubectl` configured with access to the `production` EKS cluster
- Datadog access to view container logs and APM traces
- Grafana dashboard: [Kubernetes Pod Health](https://grafana.internal.cloudthinker.io/d/k8s-pod-health/kubernetes-pod-health)
- AWS console access for ECR image verification (if needed)

## Triage & Diagnosis

### Step 1: Identify the crashing pod(s)

```bash
kubectl get pods -n production -o wide | grep -iE 'crashloopbackoff|error|imagepullbackoff'
```

Or list all pods with restart counts above zero:

```bash
kubectl get pods -n production --sort-by='.status.containerStatuses[0].restartCount' \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.startTime'
```

### Step 2: Describe the pod for events and exit codes

```bash
kubectl describe pod ${POD_NAME} -n production
```

Look for:
- **Last State**: Check `Reason` (e.g., `OOMKilled`, `Error`) and `Exit Code`
- **Events**: Look for `Failed`, `BackOff`, `FailedScheduling`, `FailedMount` events
- **Containers.State**: Check `Waiting` reason

### Step 3: Check pod logs

Current (likely partial) logs:

```bash
kubectl logs ${POD_NAME} -n production --tail=100
```

Previous (crashed) container logs:

```bash
kubectl logs ${POD_NAME} -n production --previous --tail=200
```

If multi-container pod, specify the container:

```bash
kubectl logs ${POD_NAME} -n production -c ${CONTAINER_NAME} --previous --tail=200
```

### Step 4: Check cluster events

```bash
kubectl get events -n production --sort-by='.lastTimestamp' --field-selector involvedObject.name=${POD_NAME}
```

### Step 5: Check Datadog for patterns

- Open the Datadog monitor: **KubePodCrashLooping**
- Check the [Kubernetes Overview Dashboard](https://app.datadoghq.com/dashboard/cloudthinker-k8s-overview) for cluster-wide issues
- Review container logs in Datadog: `kube_namespace:production pod_name:${POD_NAME}`

### Step 6: Determine the root cause category

| Exit Code / Reason | Likely Cause |
|---------------------|-------------|
| `OOMKilled` (Exit 137) | Memory limit exceeded |
| Exit 1 | Application error (check logs) |
| Exit 2 | Shell/command misuse |
| `CreateContainerConfigError` | Missing ConfigMap or Secret |
| `ImagePullBackOff` | Image not found or auth failure |
| `CrashLoopBackOff` + no logs | Entrypoint or command misconfigured |

## Mitigation Steps

### Scenario A: OOMKilled (Exit Code 137)

The container exceeded its memory limit and was killed by the kernel.

1. Confirm OOMKill:

   ```bash
   kubectl get pod ${POD_NAME} -n production -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'
   ```

2. Check current memory limits:

   ```bash
   kubectl get pod ${POD_NAME} -n production -o jsonpath='{.spec.containers[0].resources}'
   ```

3. Review memory usage in Grafana: [Container Memory Usage](https://grafana.internal.cloudthinker.io/d/k8s-resources/kubernetes-container-resources?var-namespace=production&var-pod=${POD_NAME})

4. If the memory limit is too low, update the deployment:

   ```bash
   kubectl set resources deployment/${DEPLOYMENT_NAME} -n production \
     -c ${CONTAINER_NAME} \
     --limits=memory=${NEW_MEMORY_LIMIT} \
     --requests=memory=${NEW_MEMORY_REQUEST}
   ```

   Typical values for CloudThinker services:

   | Service | Memory Request | Memory Limit |
   |---------|---------------|--------------|
   | backend | 512Mi | 1Gi |
   | worker-high | 1Gi | 2Gi |
   | worker-low | 512Mi | 1Gi |
   | executor | 256Mi | 512Mi |
   | payment-service | 256Mi | 512Mi |

5. If memory usage is genuinely spiking, this may indicate a memory leak. Escalate to the owning team and see [Application Memory Leak](../application/memory-leak-diagnosis.md).

### Scenario B: Application Error (Exit Code 1)

The application is crashing due to a code or configuration error.

1. Read the previous container logs carefully:

   ```bash
   kubectl logs ${POD_NAME} -n production --previous --tail=500
   ```

2. Check if a recent deployment caused the issue:

   ```bash
   kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production
   ```

3. If the crash started after a deployment, roll back:

   ```bash
   kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production
   ```

4. Verify the rollback:

   ```bash
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=120s
   ```

### Scenario C: Missing ConfigMap or Secret

The pod cannot start because a referenced ConfigMap or Secret does not exist.

1. Check which secrets/configmaps are referenced:

   ```bash
   kubectl get pod ${POD_NAME} -n production -o jsonpath='{.spec.containers[0].env[*].valueFrom}' | jq .
   ```

2. List available secrets and configmaps:

   ```bash
   kubectl get secrets -n production
   kubectl get configmaps -n production
   ```

3. If a secret is missing, check if it was accidentally deleted:

   ```bash
   kubectl get events -n production --field-selector reason=FailedMount
   ```

4. Recreate the secret from AWS Secrets Manager if needed:

   ```bash
   SECRET_VALUE=$(aws secretsmanager get-secret-value --secret-id cloudthinker/production/${SECRET_NAME} --query 'SecretString' --output text)
   kubectl create secret generic ${SECRET_NAME} -n production \
     --from-literal=key="${SECRET_VALUE}" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

### Scenario D: Image Pull Failure

The container image cannot be pulled from ECR.

1. Check the image being used:

   ```bash
   kubectl get pod ${POD_NAME} -n production -o jsonpath='{.spec.containers[0].image}'
   ```

2. Verify the image exists in ECR:

   ```bash
   aws ecr describe-images --repository-name cloudthinker/${SERVICE_NAME} \
     --image-ids imageTag=${IMAGE_TAG} --region us-east-1
   ```

3. If the image tag does not exist, update the deployment to a valid tag:

   ```bash
   kubectl set image deployment/${DEPLOYMENT_NAME} -n production \
     ${CONTAINER_NAME}=${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/cloudthinker/${SERVICE_NAME}:${VALID_TAG}
   ```

4. If it is an authentication issue, refresh the ECR pull secret:

   ```bash
   kubectl delete secret ecr-registry -n production 2>/dev/null; \
   kubectl create secret docker-registry ecr-registry -n production \
     --docker-server=${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com \
     --docker-username=AWS \
     --docker-password=$(aws ecr get-login-password --region us-east-1)
   ```

## Verification

After applying the fix, confirm the pod is running and stable:

```bash
kubectl get pod ${POD_NAME} -n production -w
```

Wait for the pod to reach `Running` status with `READY 1/1` and verify restart count is not increasing:

```bash
kubectl get pods -n production -l app=${SERVICE_NAME} \
  -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.startTime'
```

Check the Datadog monitor **KubePodCrashLooping** has returned to OK state.

## Rollback

If the mitigation introduced new issues:

- **Resource change rollback**: Revert the deployment to the previous revision:

  ```bash
  kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production
  ```

- **Secret recreation rollback**: If a recreated secret has incorrect values, delete and recreate with correct values, then restart the deployment:

  ```bash
  kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n production
  ```

## Escalation

| Condition | Contact |
|-----------|---------|
| Pod still crash-looping after 15 min | @platform-team-lead |
| Multiple services affected simultaneously | @incident-commander |
| Root cause is a memory leak | @backend-team (see memory leak runbook) |
| Issue related to payment-service | @payments-team + @incident-commander |

## Related Runbooks

- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)
- [Application Memory Leak](../application/memory-leak-diagnosis.md)
- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)
- [PVC Pending / Storage Full](../kubernetes/pvc-pending-storage-full.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-15 | @platform-team | Added ECR pull secret refresh steps |
| 2025-11-02 | @platform-team | Added memory limit reference table for services |
| 2025-08-20 | @platform-team | Initial version |
