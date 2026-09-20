# ALB/NLB 502/504 Gateway Errors

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | service-a, service-b, service-c |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-02-11 |
| **Alert** | `ALB5xxErrorsHigh`, `ALBTargetResponseTime` |
| **Tags** | `networking`, `alb`, `nlb`, `load-balancer`, `502`, `504`, `aws` |

## Summary

Application Load Balancer or Network Load Balancer returning 502 (Bad Gateway) or 504 (Gateway Timeout) errors, indicating backend targets are unreachable, unhealthy, or responding too slowly. These errors prevent users from accessing your services and typically signal infrastructure or application-layer issues between the load balancer and backend pods.

## Impact

- **User-facing**: API requests fail with 502/504 errors; users see "Bad Gateway" or timeout messages; frontend cannot fetch data
- **Revenue**: Payment processing blocked if payment service targets are unhealthy; estimate revenue impact based on your traffic volume
- **SLA**: Breaches 99.9% monthly uptime SLA if unresolved beyond 10 minutes
- **Backend cascade**: Failed requests may overwhelm healthy targets, creating cascading failures across target groups

## Prerequisites

- `kubectl` configured with EKS cluster context (`aws eks update-kubeconfig --name your-cluster-prod --region us-east-1`)
- AWS CLI v2 with IAM permissions for ELBv2, EC2, CloudWatch
- Access to CloudWatch dashboards and your monitoring platform
- Access to `production` Kubernetes namespace
- Familiarity with ALB/NLB target groups, health checks, and EKS networking

## Triage & Diagnosis

### Step 1: Identify the Affected Load Balancer and Error Type

Determine which load balancer is generating errors and whether they are 502 (Bad Gateway) or 504 (Gateway Timeout).

```bash
# List all load balancers in the region
aws elbv2 describe-load-balancers --region us-east-1 \
  --query 'LoadBalancers[*].[LoadBalancerName,DNSName,State.Code]' \
  --output table

# Check CloudWatch metrics for 502 errors (last 15 minutes)
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ApplicationELB" \
  --metric-name "HTTPCode_ELB_502_Count" \
  --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
  --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum \
  --region us-east-1

# Check CloudWatch metrics for 504 errors (last 15 minutes)
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ApplicationELB" \
  --metric-name "HTTPCode_ELB_504_Count" \
  --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
  --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum \
  --region us-east-1

# Check target response time (should be <1000ms, ideally <500ms)
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ApplicationELB" \
  --metric-name "TargetResponseTime" \
  --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
  --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average,Maximum \
  --region us-east-1
```

### Step 2: Check Target Group Health

```bash
# Get the ALB ARN from the ingress
ALB_DNS=$(kubectl get ingress -n production your-app-ingress \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "ALB DNS: ${ALB_DNS}"

ALB_ARN=$(aws elbv2 describe-load-balancers --region us-east-1 \
  --query "LoadBalancers[?DNSName=='${ALB_DNS}'].LoadBalancerArn" --output text)
echo "ALB ARN: ${ALB_ARN}"

# List all target groups for this load balancer
aws elbv2 describe-target-groups --load-balancer-arn ${ALB_ARN} --region us-east-1 \
  --query 'TargetGroups[*].[TargetGroupName,HealthCheckPath,HealthCheckIntervalSeconds,HealthyThresholdCount,UnhealthyThresholdCount]' \
  --output table

# Get target group ARN (adjust query filter as needed)
TG_ARN=$(aws elbv2 describe-target-groups --load-balancer-arn ${ALB_ARN} --region us-east-1 \
  --query 'TargetGroups[?contains(TargetGroupName, `backend`)].TargetGroupArn' --output text)

# Check target health for the target group
aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1

# Count healthy vs unhealthy targets
aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1 \
  --query 'TargetHealthDescriptions[*].TargetHealth.State' | grep -c "healthy"
aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1 \
  --query 'TargetHealthDescriptions[*].TargetHealth.State' | grep -c "unhealthy"
```

### Step 3: Check Backend Pod Health

```bash
# Check pod status for the backend service
kubectl get pods -n production -l app=backend -o wide

# Check pod resource usage (CPU, memory)
kubectl top pods -n production -l app=backend

# Check recent pod events (restarts, OOM kills, evictions)
kubectl get events -n production --field-selector involvedObject.kind=Pod \
  --sort-by='.lastTimestamp' | grep backend | tail -20

# Check if any pods are in CrashLoopBackOff or Pending
kubectl get pods -n production -l app=backend \
  --field-selector status.phase!=Running

# Check pod readiness and liveness probe status
kubectl describe pods -n production -l app=backend | grep -A 5 "Liveness\|Readiness"
```

### Step 4: Check Backend Application Logs

```bash
# Get recent logs from backend pods (last 100 lines)
kubectl logs -n production -l app=backend --tail=100 --timestamps

# Search for errors in backend logs
kubectl logs -n production -l app=backend --tail=500 | grep -E "error|Error|ERROR|exception|Exception|EXCEPTION|timeout|Timeout|TIMEOUT"

# Check for health check endpoint failures
kubectl logs -n production -l app=backend --tail=500 | grep -E "/health|/healthz|/readiness|/liveness"

# Follow logs in real-time to observe errors as they occur
kubectl logs -n production -l app=backend --follow --prefix
```

### Step 5: Verify Security Groups and Network Configuration

```bash
# Get the ALB security group ID
ALB_SG_ID=$(aws elbv2 describe-load-balancers --load-balancer-arns ${ALB_ARN} --region us-east-1 \
  --query 'LoadBalancers[0].SecurityGroups[0]' --output text)

# Describe ALB security group rules
aws ec2 describe-security-groups --group-ids ${ALB_SG_ID} --region us-east-1 \
  --query 'SecurityGroups[0].IpPermissionsEgress'

# Get the target instance security group (EKS node security group)
NODE_SG_ID=$(kubectl get nodes -o jsonpath='{.items[0].spec.providerID}' | cut -d'/' -f5)
NODE_SG_ID=$(aws ec2 describe-instances --instance-ids ${NODE_SG_ID} --region us-east-1 \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)

# Verify ALB SG allows outbound traffic to target SG on NodePort range (30000-32767)
aws ec2 describe-security-groups --group-ids ${NODE_SG_ID} --region us-east-1 \
  --query 'SecurityGroups[0].IpPermissions[?contains(IpRanges[0].CidrIp, `10.0.0.0`)]'

# Check VPC routing for ALB subnets
VPC_ID=$(aws elbv2 describe-load-balancers --load-balancer-arns ${ALB_ARN} --region us-east-1 \
  --query 'LoadBalancers[0].VpcId' --output text)

SUBNET_IDS=$(aws elbv2 describe-load-balancers --load-balancer-arns ${ALB_ARN} --region us-east-1 \
  --query 'LoadBalancers[0].AvailabilityZones[*].SubnetId' --output text)

for SUBNET_ID in ${SUBNET_IDS}; do
  echo "=== Subnet: ${SUBNET_ID} ==="
  aws ec2 describe-route-tables --region us-east-1 \
    --filters "Name=association.subnet-id,Values=${SUBNET_ID}" \
    --query 'RouteTables[0].Routes'
done
```

### Step 6: Check ALB Access Logs for Error Patterns

```bash
# Enable ALB access logs if not already enabled
aws elbv2 modify-load-balancer-attributes --load-balancer-arn ${ALB_ARN} --region us-east-1 \
  --attributes Key=access_logs.s3.enabled,Value=true Key=access_logs.s3.bucket,Value=${S3_BUCKET_NAME}

# Download recent access logs from S3
aws s3 cp s3://${S3_BUCKET_NAME}/AWSLogs/${AWS_ACCOUNT_ID}/elasticloadbalancing/${REGION}/ . \
  --recursive --exclude "*" --include "*$(date +%Y/%m/%d)*"

# Analyze access logs for 502/504 errors
zcat *.log.gz | grep -E "502|504" | head -20

# Count errors by target IP
zcat *.log.gz | grep -E "502|504" | awk '{print $4}' | sort | uniq -c | sort -rn

# Check response times for slow requests
zcat *.log.gz | awk '{if ($9 > 5) print $0}' | head -20
```

## Mitigation Steps

### Scenario A: Unhealthy Targets (Failing Health Checks)

**Symptoms**: Target health checks failing; `describe-target-health` shows `unhealthy` or `draining` state; pods may be crashing, out of memory, or health endpoint returning non-200 status codes.

1. Identify why targets are failing health checks:

   ```bash
   # Check target health description for failure reasons
   aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1 \
     --query 'TargetHealthDescriptions[?TargetHealth.State==`unhealthy`]'

   # Common reasons:
   # - Target.ResponseCodeMismatch: health endpoint returning non-200 code
   # - Target.Timeout: health check timing out (default 5s)
   # - Target.FailedHealthChecks: consecutive health check failures exceeded threshold
   ```

2. Check if pods are healthy but health endpoint is failing:

   ```bash
   # Test the health endpoint directly from within the cluster
   kubectl run curl-test --namespace=production --rm -it --restart=Never \
     --image=curlimages/curl:latest -- curl -v http://backend.production.svc.cluster.local/health

   # Check if the health endpoint handler has errors
   kubectl logs -n production -l app=backend --tail=200 | grep -E "/health|/healthz"
   ```

3. If pods are crashing or OOMKilled, increase resources:

   ```bash
   # Check pod resource limits and requests
   kubectl describe deployment backend -n production | grep -A 5 "Limits\|Requests"

   # Increase memory limits if OOMKilled
   kubectl patch deployment backend -n production --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "containers": [{
             "name": "backend",
             "resources": {
               "limits": {
                 "memory": "2Gi",
                 "cpu": "1000m"
               },
               "requests": {
                 "memory": "1Gi",
                 "cpu": "500m"
               }
             }
           }]
         }
       }
     }
   }'
   ```

4. Restart unhealthy pods:

   ```bash
   # Force restart the deployment
   kubectl rollout restart deployment/backend -n production

   # Monitor rollout status
   kubectl rollout status deployment/backend -n production --timeout=300s

   # Verify pods come up healthy
   kubectl get pods -n production -l app=backend -w
   ```

5. Adjust health check settings if checks are too aggressive:

   ```bash
   # Increase health check interval and timeout
   aws elbv2 modify-target-group --target-group-arn ${TG_ARN} --region us-east-1 \
     --health-check-interval-seconds 30 \
     --health-check-timeout-seconds 10 \
     --healthy-threshold-count 2 \
     --unhealthy-threshold-count 3
   ```

### Scenario B: Target Deregistration During Deployment

**Symptoms**: 502 errors spike during deployments; targets in `draining` state; connections terminated before graceful shutdown completes.

1. Check current deployment settings:

   ```bash
   # Check terminationGracePeriodSeconds
   kubectl get deployment backend -n production -o jsonpath='{.spec.template.spec.terminationGracePeriodSeconds}'

   # Check deregistration delay on target group
   aws elbv2 describe-target-group-attributes --target-group-arn ${TG_ARN} --region us-east-1 \
     --query 'Attributes[?Key==`deregistration_delay.timeout_seconds`]'
   ```

2. Increase `terminationGracePeriodSeconds` to allow graceful shutdown:

   ```bash
   # Patch deployment to increase termination grace period
   kubectl patch deployment backend -n production --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "terminationGracePeriodSeconds": 60
         }
       }
     }
   }'
   ```

3. Increase ALB deregistration delay:

   ```bash
   # Increase deregistration delay to 60 seconds
   aws elbv2 modify-target-group-attributes --target-group-arn ${TG_ARN} --region us-east-1 \
     --attributes Key=deregistration_delay.timeout_seconds,Value=60
   ```

4. Ensure application handles SIGTERM gracefully:

   ```bash
   # Verify the application has SIGTERM handler
   # Check application code for signal handling
   kubectl exec -n production deploy/backend -- ps aux

   # Add preStop hook to the deployment if needed
   kubectl patch deployment backend -n production --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "containers": [{
             "name": "backend",
             "lifecycle": {
               "preStop": {
                 "exec": {
                   "command": ["/bin/sh", "-c", "sleep 15"]
                 }
               }
             }
           }]
         }
       }
     }
   }'
   ```

### Scenario C: Backend Timeout (504 Gateway Timeout)

**Symptoms**: 504 errors; target response time exceeds ALB idle timeout (60s default); slow database queries, external API calls, or CPU-bound operations.

1. Identify slow endpoints from CloudWatch or access logs:

   ```bash
   # Check average and maximum target response time
   aws cloudwatch get-metric-statistics \
     --namespace "AWS/ApplicationELB" \
     --metric-name "TargetResponseTime" \
     --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 300 \
     --statistics Average,Maximum \
     --region us-east-1

   # Analyze access logs for slow requests (>10s)
   zcat /tmp/alb-logs/*.log.gz | awk '{if ($9 > 10) print $0}' | head -20
   ```

2. Optimize slow endpoints in the application:

   ```bash
   # Check application logs for slow queries or operations
   kubectl logs -n production -l app=backend --tail=500 | grep -E "slow|timeout|took [0-9]+s"

   # Profile the application using APM tools (e.g., Datadog APM, New Relic, etc.)
   # Identify and optimize slow database queries, add indexes, cache results
   ```

3. Increase ALB idle timeout if optimization is not immediately possible:

   ```bash
   # Increase ALB idle timeout to 120 seconds
   aws elbv2 modify-load-balancer-attributes --load-balancer-arn ${ALB_ARN} --region us-east-1 \
     --attributes Key=idle_timeout.timeout_seconds,Value=120

   # Verify the change
   aws elbv2 describe-load-balancer-attributes --load-balancer-arn ${ALB_ARN} --region us-east-1 \
     --query 'Attributes[?Key==`idle_timeout.timeout_seconds`]'
   ```

4. Add request timeout handling in the application:

   ```bash
   # Ensure application times out before ALB timeout
   # Example: Set application request timeout to 50s if ALB timeout is 60s
   # This allows the application to return a proper error instead of ALB timing out
   ```

### Scenario D: Security Group or Network Misconfiguration

**Symptoms**: ALB cannot reach targets; connection refused or connection timeout errors; security group rules blocking traffic; subnet routing issues.

1. Verify security group rules:

   ```bash
   # ALB security group must allow egress to target security group
   # Target security group must allow ingress from ALB security group

   # Check ALB security group egress rules
   aws ec2 describe-security-groups --group-ids ${ALB_SG_ID} --region us-east-1 \
     --query 'SecurityGroups[0].IpPermissionsEgress'

   # Check target (node) security group ingress rules
   aws ec2 describe-security-groups --group-ids ${NODE_SG_ID} --region us-east-1 \
     --query 'SecurityGroups[0].IpPermissions'
   ```

2. Add missing security group rule if needed:

   ```bash
   # Allow ALB security group to reach targets on NodePort range (30000-32767)
   aws ec2 authorize-security-group-ingress --group-id ${NODE_SG_ID} --region us-east-1 \
     --protocol tcp --port 30000-32767 --source-group ${ALB_SG_ID}

   # Or use CIDR if targets are in specific subnets
   aws ec2 authorize-security-group-ingress --group-id ${NODE_SG_ID} --region us-east-1 \
     --protocol tcp --port 30000-32767 --cidr 10.0.0.0/16
   ```

3. Verify subnet routing:

   ```bash
   # Check route tables for ALB subnets
   for SUBNET_ID in ${SUBNET_IDS}; do
     echo "=== Subnet: ${SUBNET_ID} ==="
     ROUTE_TABLE_ID=$(aws ec2 describe-route-tables --region us-east-1 \
       --filters "Name=association.subnet-id,Values=${SUBNET_ID}" \
       --query 'RouteTables[0].RouteTableId' --output text)

     aws ec2 describe-route-tables --route-table-ids ${ROUTE_TABLE_ID} --region us-east-1 \
       --query 'RouteTables[0].Routes'
   done

   # Ensure routes exist for target subnets (local VPC route)
   ```

4. Test connectivity from ALB to targets:

   ```bash
   # Use VPC Reachability Analyzer to verify network path
   # This requires creating an analysis path in the AWS console or via CLI
   ```

### Scenario E: All Targets Overloaded (High CPU/Memory)

**Symptoms**: All targets healthy but response times degraded; CPU/memory usage near limits; request queue backlog; 502/504 errors during traffic spikes.

1. Check target resource utilization:

   ```bash
   # Check pod CPU and memory usage
   kubectl top pods -n production -l app=backend

   # Check node resource pressure
   kubectl top nodes

   # Check HPA status if autoscaling is enabled
   kubectl get hpa -n production
   kubectl describe hpa backend-hpa -n production
   ```

2. Scale up deployment replicas immediately:

   ```bash
   # Increase replica count
   kubectl scale deployment backend -n production --replicas=10

   # Monitor new pods coming online
   kubectl get pods -n production -l app=backend -w

   # Verify new targets register with ALB
   aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1 \
     --query 'TargetHealthDescriptions[?TargetHealth.State==`healthy`]' | jq 'length'
   ```

3. Increase HPA max replicas if at limit:

   ```bash
   # Check current HPA configuration
   kubectl get hpa backend-hpa -n production -o yaml

   # Increase max replicas
   kubectl patch hpa backend-hpa -n production --type=strategic -p '{
     "spec": {
       "maxReplicas": 20
     }
   }'
   ```

4. Add more nodes to the cluster if node capacity is exhausted:

   ```bash
   # Check if pods are pending due to insufficient resources
   kubectl get pods -n production --field-selector status.phase=Pending

   # If using Cluster Autoscaler, check its status
   kubectl logs -n kube-system -l app=cluster-autoscaler --tail=100

   # Manually scale node group if needed
   NODE_GROUP_NAME=$(aws eks list-nodegroups --cluster-name your-cluster-prod --region us-east-1 \
     --query 'nodegroups[0]' --output text)

   aws eks update-nodegroup-config --cluster-name your-cluster-prod --region us-east-1 \
     --nodegroup-name ${NODE_GROUP_NAME} \
     --scaling-config minSize=3,maxSize=10,desiredSize=6
   ```

## Verification

After applying any mitigation, verify the issue is resolved:

```bash
# Check that 5xx error count has dropped to zero
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ApplicationELB" \
  --metric-name "HTTPCode_ELB_5XX_Count" \
  --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum \
  --region us-east-1

# Verify all targets are healthy
aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1 \
  --query 'TargetHealthDescriptions[*].TargetHealth.State'

# Expected: All targets in "healthy" state

# Check target response time is normal (<500ms average)
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ApplicationELB" \
  --metric-name "TargetResponseTime" \
  --dimensions Name=LoadBalancer,Value=${LOAD_BALANCER_ARN_SUFFIX} \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average \
  --region us-east-1

# Test end-to-end connectivity
curl -I https://api.your-domain.com/health

# Expected: HTTP 200 OK

# Verify monitoring alerts have recovered
# -> https://<your-monitoring-url>/monitors/manage?q=ALB5xxErrorsHigh
```

## Rollback

If mitigation steps cause additional issues:

```bash
# Rollback deployment to previous version
kubectl rollout undo deployment/backend -n production

# Revert security group rule changes
aws ec2 revoke-security-group-ingress --group-id ${NODE_SG_ID} --region us-east-1 \
  --protocol tcp --port 30000-32767 --source-group ${ALB_SG_ID}

# Revert ALB timeout changes
aws elbv2 modify-load-balancer-attributes --load-balancer-arn ${ALB_ARN} --region us-east-1 \
  --attributes Key=idle_timeout.timeout_seconds,Value=60

# Revert target group attributes
aws elbv2 modify-target-group-attributes --target-group-arn ${TG_ARN} --region us-east-1 \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30

# Scale down if over-provisioned
kubectl scale deployment backend -n production --replicas=3

# Revert HPA changes
kubectl patch hpa backend-hpa -n production --type=strategic -p '{
  "spec": {
    "maxReplicas": 10
  }
}'
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Not resolved in 15 minutes | @your-team-lead |
| Customer impact confirmed | @incident-commander |
| All targets unhealthy across multiple target groups | @vp-engineering |
| Suspected AWS service issue | AWS Support (Severity 1) |

## Related Runbooks

- [DNS Resolution Failure](../networking/dns-resolution-failure.md)
- [SSL/TLS Certificate Expiry](../networking/ssl-certificate-expiry.md)
- [API High Latency](../application/api-high-latency.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)
- [Node Not Ready](../kubernetes/node-not-ready.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-11 | @your-team | Initial version |
