# HTTP 5xx Error Rate Spike

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | service-a, service-b, service-c |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |
| **Alert** | `HTTP5xxErrorRateHigh` |
| **Tags** | `application`, `http`, `5xx`, `availability`, `server-error` |

## Summary

A sustained increase in HTTP 5xx responses from one or more application services, indicating server-side failures. 5xx errors represent failures on the server side (vs 4xx client errors), including 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable, and 504 Gateway Timeout. This runbook covers identifying which services are failing, diagnosing root causes (bad deployment, dependency failure, resource exhaustion, traffic overload), and applying targeted mitigations.

## Impact

- **User experience degradation**: End users encounter "Something went wrong" error pages, failed API calls, and incomplete operations.
- **Data integrity risk**: Failed POST/PUT/DELETE requests may leave partial state or lost writes, requiring manual reconciliation.
- **Customer trust erosion**: Enterprise customers monitoring uptime SLAs will flag the incident, potentially triggering contractual penalties.
- **Revenue loss**: Failed payment processing, blocked user signups, and abandoned workflows directly impact revenue.
- **Cascading failures**: Downstream services retrying failed requests amplify load on already-struggling services.
- **Alert fatigue**: 5xx spikes trigger multiple monitors (availability, latency, error rate), overwhelming on-call engineers.

## Prerequisites

- `kubectl` access to `production` namespace in EKS cluster
- Access to application logs: `https://<your-monitoring-tool>/logs`
- Access to APM traces: `https://<your-monitoring-tool>/apm/traces`
- Access to Grafana dashboard: `https://<your-grafana-url>/d/5xx-errors/5xx-error-rate-overview`
- AWS CLI configured for production account (ALB/NLB checks)
- Familiarity with recent deployments and application architecture

## Triage & Diagnosis

### Step 1: Identify Which Services Are Returning 5xx

```bash
# Check 5xx error rate by service in the last 15 minutes
kubectl logs -n production -l app.kubernetes.io/component=service-a --tail=1000 --since=15m \
  | grep -E "HTTP/[0-9.]+ 5[0-9]{2}" \
  | awk '{print $NF}' \
  | sort | uniq -c | sort -rn

# Check monitoring tool logs for 5xx errors grouped by service
# https://<your-monitoring-tool>/logs?query=status%3A5xx%20service%3A%2A&from_ts=now-15m&to_ts=now&live=true
```

```bash
# Check which endpoints are returning 5xx errors
kubectl logs -n production deploy/service-a --tail=5000 --since=15m \
  | grep -E "HTTP/[0-9.]+ 5[0-9]{2}" \
  | awk '{print $(NF-1), $NF}' \
  | sort | uniq -c | sort -rn | head -20
```

```bash
# Test critical endpoints directly from within the cluster
kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/health
kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/runbooks
kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/incidents
```

### Step 2: Check Recent Deployments

```bash
# Check deployment rollout history for all services
for deploy in service-a service-b service-c worker-a worker-b; do
  echo "=== ${deploy} ==="
  kubectl rollout history deployment/${deploy} -n production --revision=0 | tail -5
done
```

```bash
# Check when the last rollout occurred
kubectl get events -n production --sort-by='.lastTimestamp' \
  | grep -E "Deployment|ReplicaSet|Scaled" \
  | head -20
```

```bash
# Check if any deployments are currently rolling out
kubectl get deployments -n production -o wide
```

### Step 3: Check Pod Health and Restart Counts

```bash
# Check pod status across all namespaces
kubectl get pods -n production -o wide --sort-by='.status.containerStatuses[0].restartCount' \
  | grep -v "Completed"
```

```bash
# Check for pods in CrashLoopBackOff or Error state
kubectl get pods -n production --field-selector=status.phase!=Running,status.phase!=Succeeded
```

```bash
# Check restart counts for critical services
kubectl get pods -n production -l app.kubernetes.io/name=service-a \
  -o custom-columns='NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,STATUS:.status.phase,AGE:.metadata.creationTimestamp'
```

```bash
# Check pod events for errors
kubectl get events -n production --sort-by='.lastTimestamp' \
  | grep -iE "error|failed|killing|backoff" \
  | head -20
```

### Step 4: Check Upstream Dependencies

```bash
# Check database connectivity and health
kubectl exec -n production deploy/service-a -- psql "${DATABASE_URL}" -c "SELECT 1;"

# Check database connection count
kubectl exec -n production deploy/service-a -- psql "${DATABASE_URL}" -c "
SELECT count(*) AS total_connections,
       count(*) FILTER (WHERE state = 'active') AS active,
       count(*) FILTER (WHERE state = 'idle') AS idle
FROM pg_stat_activity
WHERE datname = 'your_database';
"
```

```bash
# Check Redis connectivity
kubectl exec -n production deploy/service-a -- redis-cli -h ${REDIS_HOST} -p 6379 PING
kubectl exec -n production deploy/service-a -- redis-cli -h ${REDIS_HOST} -p 6379 INFO replication
```

```bash
# Check internal service dependencies
kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "Service-A: %{http_code} (%{time_total}s)\n" \
  http://service-a.production.svc.cluster.local:6333/healthz

kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "Service-B: %{http_code} (%{time_total}s)\n" \
  http://service-b.production.svc.cluster.local:8080/health

kubectl exec -n production deploy/service-a -- curl -s -o /dev/null -w "Service-C: %{http_code} (%{time_total}s)\n" \
  http://service-c.production.svc.cluster.local:8000/health
```

```bash
# Check external API dependencies (LLM providers)
kubectl exec -n production deploy/service-a -- python3 -c "
import requests
import os

# Test Anthropic API
anthropic_key = os.getenv('ANTHROPIC_API_KEY')
if anthropic_key:
    try:
        r = requests.get('https://api.anthropic.com/v1/messages',
                        headers={'x-api-key': anthropic_key, 'anthropic-version': '2023-06-01'},
                        timeout=5)
        print(f'Anthropic API: HTTP {r.status_code}')
    except Exception as e:
        print(f'Anthropic API: FAILED - {e}')

# Test AWS Bedrock connectivity
try:
    import boto3
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
    print('AWS Bedrock: Connected')
except Exception as e:
    print(f'AWS Bedrock: FAILED - {e}')
"
```

### Step 5: Check ALB/NLB Target Group Health

```bash
# List all target groups
aws elbv2 describe-target-groups \
  --region ${AWS_REGION} \
  --query 'TargetGroups[?contains(TargetGroupName, `your-app`)].TargetGroupArn' \
  --output text

# Check target health for each target group
aws elbv2 describe-target-health \
  --target-group-arn ${TARGET_GROUP_ARN} \
  --region ${AWS_REGION} \
  --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State,TargetHealth.Reason]' \
  --output table
```

```bash
# Check ALB access logs for 5xx errors (if S3 logging enabled)
aws s3 ls s3://${ALB_LOGS_BUCKET}/AWSLogs/${ACCOUNT_ID}/elasticloadbalancing/${AWS_REGION}/ \
  --recursive --human-readable | tail -5
```

### Step 6: Check Resource Pressure (CPU, Memory)

```bash
# Check CPU and memory usage for all pods
kubectl top pods -n production --sort-by=cpu | head -20
kubectl top pods -n production --sort-by=memory | head -20
```

```bash
# Check for OOMKilled pods or CPU throttling
kubectl describe pods -n production -l app.kubernetes.io/name=service-a \
  | grep -A 5 -E "State:|Reason:|Last State:|cpu|memory|Limits|Requests"
```

```bash
# Check node resource pressure
kubectl top nodes
kubectl describe nodes | grep -A 5 "Allocated resources"
```

### Step 7: Check Application Logs for Stack Traces

```bash
# Tail recent logs for exceptions and stack traces
kubectl logs -n production deploy/service-a --tail=500 --since=15m \
  | grep -iE "error|exception|traceback|failed" \
  | head -50
```

```bash
# Check for specific error patterns
kubectl logs -n production deploy/service-a --tail=1000 --since=15m \
  | grep -E "(ConnectionError|TimeoutError|DatabaseError|HTTPError|500 Internal Server Error)"
```

```bash
# Export logs for detailed analysis
kubectl logs -n production deploy/service-a --since=1h > /tmp/service-a-5xx-debug.log
kubectl logs -n production deploy/service-b --since=1h > /tmp/service-b-5xx-debug.log
```

## Mitigation Steps

### Scenario A: Bad Deployment

A recent code deployment introduced a bug causing 5xx errors.

**Indicators**: 5xx spike correlates with recent rollout, errors concentrated in newly deployed pods, logs show new exception types.

1. Identify the most recent deployment:

   ```bash
   kubectl rollout history deployment/${AFFECTED_DEPLOYMENT} -n production
   ```

2. Check if the current revision is healthy:

   ```bash
   kubectl get replicasets -n production -l app.kubernetes.io/name=${AFFECTED_DEPLOYMENT} \
     --sort-by='.metadata.creationTimestamp'
   ```

3. Rollback to the previous revision:

   ```bash
   kubectl rollout undo deployment/${AFFECTED_DEPLOYMENT} -n production
   ```

4. Monitor the rollback progress:

   ```bash
   kubectl rollout status deployment/${AFFECTED_DEPLOYMENT} -n production --timeout=180s
   ```

5. Verify 5xx rate drops after rollback:

   ```bash
   # Wait for new pods to stabilize
   sleep 30

   # Check error rate
   kubectl logs -n production deploy/${AFFECTED_DEPLOYMENT} --tail=500 --since=5m \
     | grep -E "HTTP/[0-9.]+ 5[0-9]{2}" | wc -l
   ```

### Scenario B: Dependency Failure

Database, Redis, or external API is down or unreachable.

**Indicators**: Logs show connection errors, timeouts, or "connection refused", all endpoints failing similarly.

1. **Database failure**:

   ```bash
   # Check database connectivity
   kubectl exec -n production deploy/service-a -- psql "${DATABASE_URL}" -c "SELECT version();"

   # Check for connection pool exhaustion
   kubectl exec -n production deploy/service-a -- psql "${DATABASE_URL}" -c "
   SELECT count(*) AS total, max_connections
   FROM pg_stat_activity, (SELECT setting::int AS max_connections FROM pg_settings WHERE name='max_connections') s
   GROUP BY max_connections;
   "

   # If pool exhausted, kill idle connections
   kubectl exec -n production deploy/service-a -- psql "${DATABASE_URL}" -c "
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'idle' AND query_start < now() - interval '10 minutes';
   "
   ```

2. **Redis failure**:

   ```bash
   # Check Redis health
   kubectl exec -n production deploy/service-a -- redis-cli -h ${REDIS_HOST} -p 6379 INFO server

   # Restart Redis if unresponsive
   kubectl rollout restart deployment/redis -n production
   kubectl rollout status deployment/redis -n production --timeout=120s
   ```

3. **External API failure** (Anthropic, Bedrock):

   ```bash
   # Enable circuit breaker to fail fast and return degraded responses
   kubectl set env deployment/service-a -n production \
     LLM_CIRCUIT_BREAKER_ENABLED=true \
     LLM_TIMEOUT_SECONDS=10

   kubectl rollout status deployment/service-a -n production --timeout=180s
   ```

4. **Restart dependent pods** to clear stale connections:

   ```bash
   kubectl rollout restart deployment/${AFFECTED_DEPLOYMENT} -n production
   kubectl rollout status deployment/${AFFECTED_DEPLOYMENT} -n production --timeout=180s
   ```

### Scenario C: Resource Exhaustion

Pods are OOMKilled, CPU throttled, or disk full.

**Indicators**: Pods restarting frequently, `kubectl top` shows high resource usage, logs show "out of memory" or timeouts.

1. Check for OOMKilled pods:

   ```bash
   kubectl get pods -n production -o json \
     | jq -r '.items[] | select(.status.containerStatuses[]?.lastState.terminated.reason == "OOMKilled") | .metadata.name'
   ```

2. Check current resource limits:

   ```bash
   kubectl get deployment ${AFFECTED_DEPLOYMENT} -n production \
     -o jsonpath='{.spec.template.spec.containers[0].resources}' | python3 -m json.tool
   ```

3. Increase memory limits temporarily:

   ```bash
   # Example: increase from 512Mi to 1Gi
   kubectl set resources deployment/${AFFECTED_DEPLOYMENT} -n production \
     --limits=memory=1Gi \
     --requests=memory=768Mi

   kubectl rollout status deployment/${AFFECTED_DEPLOYMENT} -n production --timeout=180s
   ```

4. If CPU throttled, increase CPU limits:

   ```bash
   kubectl set resources deployment/${AFFECTED_DEPLOYMENT} -n production \
     --limits=cpu=2000m \
     --requests=cpu=1000m

   kubectl rollout status deployment/${AFFECTED_DEPLOYMENT} -n production --timeout=180s
   ```

5. Scale up replicas to distribute load:

   ```bash
   kubectl scale deployment/${AFFECTED_DEPLOYMENT} -n production --replicas=${DESIRED_REPLICAS}
   kubectl rollout status deployment/${AFFECTED_DEPLOYMENT} -n production --timeout=180s
   ```

### Scenario D: Traffic Spike / Overload

Sudden increase in request rate exceeds application capacity.

**Indicators**: All pods healthy but all returning 5xx, request queue backlog, CPU/memory usage elevated but below limits, recent traffic spike visible in metrics.

1. Check current request rate:

   ```bash
   # Check Grafana for request rate
   # https://<your-grafana-url>/d/api-metrics/api-request-rate?from=now-1h&to=now

   # Check ALB request count
   aws cloudwatch get-metric-statistics \
     --namespace AWS/ApplicationELB \
     --metric-name RequestCount \
     --dimensions Name=LoadBalancer,Value=${ALB_NAME} \
     --start-time $(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 300 \
     --statistics Sum \
     --region ${AWS_REGION}
   ```

2. Scale up replicas immediately:

   ```bash
   # Check current HPA status
   kubectl get hpa ${AFFECTED_DEPLOYMENT} -n production

   # Manually scale beyond HPA max if needed
   kubectl scale deployment/${AFFECTED_DEPLOYMENT} -n production --replicas=${DESIRED_REPLICAS}
   ```

3. Increase HPA max replicas:

   ```bash
   kubectl patch hpa ${AFFECTED_DEPLOYMENT} -n production \
     -p '{"spec":{"maxReplicas":'${NEW_MAX}'}}'
   ```

4. Enable rate limiting at ALB level (if not already enabled):

   ```bash
   # Create WAF rate limit rule (requires AWS WAF configured)
   # Consult with security team before applying

   # Temporary: Add rate limiting via application config
   kubectl set env deployment/service-a -n production \
     RATE_LIMIT_ENABLED=true \
     RATE_LIMIT_PER_MINUTE=1000

   kubectl rollout status deployment/service-a -n production --timeout=180s
   ```

5. Add connection pooling or request queuing:

   ```bash
   # Increase uvicorn worker count
   kubectl set env deployment/service-a -n production \
     UVICORN_WORKERS=${INCREASED_WORKER_COUNT}

   kubectl rollout status deployment/service-a -n production --timeout=180s
   ```

## Verification

```bash
# Check 5xx error rate is back to baseline (<0.1% of total requests)
kubectl logs -n production deploy/service-a --tail=1000 --since=5m \
  | grep -E "HTTP/[0-9.]+ [0-9]{3}" \
  | awk '{
      total++;
      if ($NF ~ /^5[0-9]{2}$/) errors++
    }
    END {
      if (total > 0) {
        pct = (errors / total) * 100;
        printf "5xx rate: %.2f%% (%d/%d)\n", pct, errors, total
      }
    }'
```

```bash
# Verify all pods are healthy and running
kubectl get pods -n production -l app.kubernetes.io/name=${AFFECTED_DEPLOYMENT} \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.conditions[?(@.type=="Ready")].status,RESTARTS:.status.containerStatuses[0].restartCount'
```

```bash
# Check endpoint health from within cluster
kubectl exec -n production deploy/service-a -- sh -c '
  for endpoint in /api/v1/health /api/v1/runbooks /api/v1/incidents; do
    code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000${endpoint})
    echo "${endpoint}: HTTP ${code}"
  done
'
```

```bash
# Verify P99 latency is normal (<500ms for standard endpoints)
# Check Grafana dashboard
# https://<your-grafana-url>/d/api-latency/api-latency-overview?from=now-15m&to=now
```

```bash
# Confirm monitoring alert has cleared
# https://<your-monitoring-tool>/monitors/manage?q=HTTP5xxErrorRateHigh
```

**Expected**: 5xx error rate below 0.1%, all pods healthy and ready, latency within normal range, no recent restarts, monitoring alert in OK state.

## Rollback

If deployment rollback made things worse:

```bash
# Roll forward to the latest revision
kubectl rollout undo deployment/${AFFECTED_DEPLOYMENT} -n production --to-revision=0
```

If scaling changes caused node pressure or cluster instability:

```bash
# Scale back to original replica count
kubectl scale deployment/${AFFECTED_DEPLOYMENT} -n production --replicas=${ORIGINAL_REPLICAS}

# Revert HPA max replicas
kubectl patch hpa ${AFFECTED_DEPLOYMENT} -n production \
  -p '{"spec":{"maxReplicas":'${ORIGINAL_MAX}'}}'
```

If resource limit increases caused cost issues or node pressure:

```bash
# Revert to original resource limits
kubectl set resources deployment/${AFFECTED_DEPLOYMENT} -n production \
  --limits=memory=${ORIGINAL_MEMORY_LIMIT},cpu=${ORIGINAL_CPU_LIMIT} \
  --requests=memory=${ORIGINAL_MEMORY_REQUEST},cpu=${ORIGINAL_CPU_REQUEST}
```

If circuit breaker or rate limiting caused user complaints:

```bash
# Disable circuit breaker
kubectl set env deployment/service-a -n production \
  LLM_CIRCUIT_BREAKER_ENABLED- \
  RATE_LIMIT_ENABLED-

kubectl rollout status deployment/service-a -n production --timeout=180s
```

## Escalation

| Condition | Contact |
|-----------|---------|
| 5xx rate >5% for more than 5 minutes | @your-escalation-contact + @incident-commander |
| All replicas of critical service failing | @incident-commander (declare P0 incident) |
| Database-related 5xx errors | @dba-team + @your-team |
| Critical service 5xx errors | @service-owner + @incident-commander |
| External API provider outage | @dependent-service-team + check provider status pages |
| Security-related errors (auth failures) | @security-team |
| Not resolved within 30 minutes | @vp-engineering |
| Customer-facing impact confirmed | @customer-success + @incident-commander |

## Related Runbooks

- [API P99 Latency SLA Breach](api-high-latency.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [Application Memory Leak Diagnosis](memory-leak-diagnosis.md)
- [PostgreSQL Connection Pool Exhaustion](../database/postgres-connection-pool-exhaustion.md)
- [Redis Memory Pressure / OOM](../database/redis-memory-pressure.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-11 | @your-team | Initial version |
