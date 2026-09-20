# Deployment Rollback

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | service-a, service-b, worker-a, worker-b |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |
| **Alert** | `DeploymentFailed`, `HTTP5xxPostDeploy` |
| **Tags** | `kubernetes`, `deployment`, `rollback`, `ci-cd` |

## Summary

A recent deployment introduced critical issues such as application errors, crashes, or degraded performance that require reverting to the last known good version to restore service stability.

## Impact

- **Service degradation or outage**: Users experience errors, timeouts, or complete service unavailability.
- **Data inconsistency**: Database migrations or schema changes may cause compatibility issues.
- **Cascading failures**: Downstream services may fail due to contract changes or breaking API updates.
- **SLA breach**: Customer-facing errors trigger SLA violation timers for enterprise accounts.
- **Revenue loss**: Payment flows, authentication, or critical business logic failures block user actions.

## Prerequisites

- `kubectl` configured with access to the `production` EKS cluster
- Access to APM/monitoring tool: `https://<your-monitoring-tool>/apm/services`
- Access to Grafana dashboard: `https://<your-grafana-url>/d/deployments/deployment-health`
- Access to ArgoCD or CI/CD pipeline to verify deployment history
- Database migration access (if database changes involved)
- Familiarity with the service's rollout strategy (blue-green, canary, rolling)

## Triage & Diagnosis

### Step 1: Identify when the issue started

```bash
# Check deployment rollout history
kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production

# View details of the current revision
kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production --revision=${CURRENT_REVISION}

# View details of the previous revision
kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production --revision=$((${CURRENT_REVISION} - 1))
```

### Step 2: Check rollout status

```bash
# Check if the deployment is still progressing or has completed
kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=10s

# Check deployment events
kubectl describe deployment/${DEPLOYMENT_NAME} -n production | tail -30
```

### Step 3: Check pod health after deployment

```bash
# Check pod status and restart counts
kubectl get pods -n production -l app=${SERVICE_NAME} \
  -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.startTime'

# Check for CrashLoopBackOff or ImagePullBackOff
kubectl get pods -n production -l app=${SERVICE_NAME} | grep -iE 'crashloop|error|imagepull'
```

### Step 4: Compare current vs previous deployment

```bash
# Get current image tag
kubectl get deployment/${DEPLOYMENT_NAME} -n production -o jsonpath='{.spec.template.spec.containers[0].image}'

# Get previous image tag from rollout history
kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production --revision=$((${CURRENT_REVISION} - 1)) | grep -i image
```

### Step 5: Check application logs for new errors

```bash
# Check recent logs from the new pods
kubectl logs -n production -l app=${SERVICE_NAME} --tail=200 --since=10m | grep -iE 'error|exception|fatal|panic'

# Check logs from a specific failing pod
kubectl logs ${POD_NAME} -n production --tail=500
```

### Step 6: Check error rate and latency metrics

```bash
# Check HTTP error rates in your monitoring tool
# https://<your-monitoring-tool>/apm/services/${SERVICE_NAME}?env=production&start=now-15m

# Check API health endpoint
kubectl exec -n production deploy/${DEPLOYMENT_NAME} -- curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

### Step 7: Identify what changed

```bash
# Check if ConfigMaps changed
kubectl describe deployment/${DEPLOYMENT_NAME} -n production | grep -A 10 'Environment Variables'

# Check if Secrets changed
kubectl get events -n production --field-selector involvedObject.name=${DEPLOYMENT_NAME} --sort-by='.lastTimestamp' | tail -20

# Compare deployment manifests
kubectl get deployment/${DEPLOYMENT_NAME} -n production -o yaml > current-deployment.yaml
```

Compare with the previous version from your GitOps repository or infrastructure-as-code repo.

## Mitigation Steps

### Scenario A: Simple rollback (image change only)

The deployment changed only the container image, and the new version has bugs or crashes.

1. Roll back to the previous revision:

   ```bash
   kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production
   ```

2. Monitor the rollback progress:

   ```bash
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=180s
   ```

3. Verify pods are running with the old image:

   ```bash
   kubectl get pods -n production -l app=${SERVICE_NAME} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
   ```

4. Check pod health:

   ```bash
   kubectl get pods -n production -l app=${SERVICE_NAME} \
     -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount'
   ```

### Scenario B: ConfigMap or Secret change caused failure

The deployment introduced new environment variables or configuration that broke the application.

1. Identify the ConfigMap or Secret that changed:

   ```bash
   # List recent ConfigMap changes
   kubectl get events -n production --field-selector involvedObject.kind=ConfigMap --sort-by='.lastTimestamp' | tail -10

   # List recent Secret changes
   kubectl get events -n production --field-selector involvedObject.kind=Secret --sort-by='.lastTimestamp' | tail -10
   ```

2. View the current ConfigMap:

   ```bash
   kubectl get configmap ${CONFIGMAP_NAME} -n production -o yaml > current-configmap.yaml
   ```

3. Restore the previous ConfigMap from Git or backup:

   ```bash
   kubectl apply -f previous-configmap.yaml
   ```

4. Restart the deployment to pick up the reverted config:

   ```bash
   kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n production
   ```

5. Monitor rollout:

   ```bash
   kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=180s
   ```

### Scenario C: Database migration incompatibility

The deployment included a database migration that is incompatible with the old code version.

**WARNING: This scenario is HIGH RISK. Database rollbacks can cause data loss.**

1. **STOP**: Do not proceed without consulting the database team.

2. Check if migrations ran:

   ```bash
   kubectl exec -n production deploy/${DEPLOYMENT_NAME} -- psql "${DATABASE_URL}" -c "
   SELECT version_num, installed_on
   FROM alembic_version
   ORDER BY installed_on DESC
   LIMIT 5;
   "
   ```

3. Identify the migration that needs to be reverted:

   ```bash
   # Check migration history
   kubectl exec -n production deploy/${DEPLOYMENT_NAME} -- uv run alembic history | head -20
   ```

4. If the migration is backward-compatible (safe to roll back):

   ```bash
   # Downgrade to previous migration
   kubectl exec -n production deploy/${DEPLOYMENT_NAME} -- uv run alembic downgrade -1
   ```

5. Roll back the application code:

   ```bash
   kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production
   ```

6. If the migration is NOT backward-compatible, escalate immediately to @your-dba-team and @your-escalation-contact. Do NOT roll back without coordination.

### Scenario D: Canary or progressive rollout failure

A canary deployment is failing, and traffic needs to be routed back to the stable version.

1. Check canary deployment status (if using Flagger or similar):

   ```bash
   kubectl get canary ${CANARY_NAME} -n production
   ```

2. Abort the canary rollout:

   ```bash
   kubectl patch canary ${CANARY_NAME} -n production --type=merge -p '{"spec":{"skipAnalysis":true}}'
   ```

3. Route all traffic back to the primary deployment:

   ```bash
   kubectl patch virtualservice ${SERVICE_NAME} -n production --type=merge -p '{"spec":{"http":[{"route":[{"destination":{"host":"'${SERVICE_NAME}'","subset":"stable"},"weight":100}]}]}}'
   ```

4. Delete the canary pods:

   ```bash
   kubectl delete pods -n production -l app=${SERVICE_NAME},version=canary
   ```

5. Verify traffic is routed to stable:

   ```bash
   kubectl get virtualservice ${SERVICE_NAME} -n production -o yaml | grep -A 5 weight
   ```

## Verification

After the rollback is complete, verify service health:

```bash
# Confirm rollout is complete
kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=120s

# All pods should be Running with READY 1/1
kubectl get pods -n production -l app=${SERVICE_NAME} \
  -o custom-columns='NAME:.metadata.name,READY:.status.containerStatuses[0].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount'

# Check error rate is back to baseline
# https://<your-grafana-url>/d/api-errors/api-error-rates?var-service=${SERVICE_NAME}&from=now-30m

# Verify no pods are restarting
watch -n 2 'kubectl get pods -n production -l app=${SERVICE_NAME}'
```

Expected:
- All pods in `Running` state with `READY 1/1`
- Restart count is stable (not increasing)
- Error rate has returned to pre-deployment levels (<0.1% for critical services)
- Monitoring alerts have cleared (no active alerts for HTTP5xxPostDeploy)

## Rollback

If the rollback itself introduces new issues (e.g., the previous version also has a bug):

```bash
# Roll forward to a specific known-good revision
kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production --to-revision=${KNOWN_GOOD_REVISION}

# Verify the target revision
kubectl rollout history deployment/${DEPLOYMENT_NAME} -n production --revision=${KNOWN_GOOD_REVISION}

# Monitor rollout
kubectl rollout status deployment/${DEPLOYMENT_NAME} -n production --timeout=180s
```

If database migrations cannot be safely reverted, consider a **roll-forward** strategy: deploy a hotfix that includes both the migration and a bug fix for the application code.

## Escalation

| Condition | Contact |
|-----------|---------|
| Rollback not resolving errors within 10 minutes | @your-escalation-contact + @your-incident-commander |
| Database migration involved | @your-dba-team + @your-escalation-contact |
| Customer-facing service down for >5 minutes | @your-incident-commander + @your-customer-success-team |
| Payment service affected | @your-payments-team + @your-incident-commander |
| Suspected data corruption | @your-engineering-lead + @your-dba-team |
| Not resolved within 20 minutes | @your-engineering-lead |

## Related Runbooks

- [5xx Error Rate Spike](../application/api-high-latency.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [API High Latency](../application/api-high-latency.md)
- [PostgreSQL Connection Pool Exhaustion](../database/postgres-connection-pool-exhaustion.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-11 | @your-team | Initial version |
