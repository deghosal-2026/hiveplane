# DNS Resolution Failure

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | All services (cluster-wide) |
| **Owner** | @your-team |
| **Last Reviewed** | 2025-12-15 |
| **Alert** | `DNSResolutionFailure` |
| **Tags** | `networking`, `dns`, `coredns`, `route53`, `eks` |

## Summary

DNS resolution failures within the EKS cluster or to external endpoints cause cascading service failures across all services. This runbook covers CoreDNS pod failures, Route53 health check degradation, VPC DNS misconfigurations, and pod-level DNS tuning issues.

## Impact

- **User-facing**: All API requests fail with 502/503 errors; frontend cannot reach backend services
- **Internal**: Service-to-service communication breaks; workers cannot connect to Redis/PostgreSQL; services cannot resolve ECR for image pulls
- **Revenue**: Complete platform outage; estimate revenue impact based on your traffic volume
- **SLA**: Breaches 99.95% monthly uptime SLA if unresolved beyond 5 minutes

## Prerequisites

- `kubectl` configured with EKS cluster context (`aws eks update-kubeconfig --name your-cluster-prod --region us-east-1`)
- AWS CLI v2 with IAM permissions for Route53, VPC, EC2
- Access to your monitoring platform and Grafana dashboards
- Access to `production` Kubernetes namespace
- Familiarity with CoreDNS, Route53, and VPC networking

## Triage & Diagnosis

### Step 1: Confirm DNS Failure Scope

Determine if the failure is cluster-wide, node-specific, or pod-specific.

```bash
# Test internal DNS resolution from a debug pod
kubectl run dns-debug --namespace=production --rm -it --restart=Never \
  --image=busybox:1.36 -- nslookup backend.production.svc.cluster.local

# Test external DNS resolution
kubectl run dns-debug-ext --namespace=production --rm -it --restart=Never \
  --image=busybox:1.36 -- nslookup api.your-domain.com

# Check which pods are reporting DNS errors
kubectl get events --namespace=production --field-selector reason=DNSConfigForming --sort-by='.lastTimestamp'
```

### Step 2: Check CoreDNS Pod Health

```bash
# Check CoreDNS pod status
kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide

# Check CoreDNS logs for errors
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=100 --timestamps

# Check CoreDNS resource usage
kubectl top pods -n kube-system -l k8s-app=kube-dns

# Verify CoreDNS ConfigMap
kubectl get configmap coredns -n kube-system -o yaml
```

### Step 3: Check Monitoring & Grafana

```
Monitoring Alert: "DNS Resolution Failure - Production EKS"
  -> https://<your-monitoring-url>/monitors/manage?q=DNSResolutionFailure

Grafana Dashboard: "CoreDNS / Cluster DNS"
  -> https://<your-grafana-url>/d/dns-overview/coredns-cluster-dns

Key metrics to check:
  - coredns_dns_requests_total (should be >0)
  - coredns_dns_responses_total{rcode="SERVFAIL"} (should be 0)
  - coredns_dns_responses_total{rcode="NXDOMAIN"} (elevated = misconfigured services)
  - coredns_panics_total (any value > 0 = critical)
  - coredns_dns_request_duration_seconds (p99 should be <100ms)
```

### Step 4: Check VPC DNS Settings

```bash
# Get the VPC ID for the EKS cluster
VPC_ID=$(aws eks describe-cluster --name your-cluster-prod --region us-east-1 \
  --query 'cluster.resourcesVpcConfig.vpcId' --output text)

# Verify DNS support is enabled
aws ec2 describe-vpc-attribute --vpc-id ${VPC_ID} --attribute enableDnsSupport --region us-east-1
# Expected: "Value": true

aws ec2 describe-vpc-attribute --vpc-id ${VPC_ID} --attribute enableDnsHostnames --region us-east-1
# Expected: "Value": true

# Check the DHCP options set for DNS servers
DHCP_OPTIONS_ID=$(aws ec2 describe-vpcs --vpc-ids ${VPC_ID} --region us-east-1 \
  --query 'Vpcs[0].DhcpOptionsId' --output text)

aws ec2 describe-dhcp-options --dhcp-options-ids ${DHCP_OPTIONS_ID} --region us-east-1 \
  --query 'DhcpOptions[0].DhcpConfigurations'
```

### Step 5: Check Route53 Health Checks

```bash
# List health checks for your domains
aws route53 list-health-checks --region us-east-1 \
  --query 'HealthChecks[?contains(HealthCheckConfig.FullyQualifiedDomainName, `your-domain.com`)]'

# Get health check status
aws route53 get-health-check-status --health-check-id ${HEALTH_CHECK_ID} --region us-east-1

# Check hosted zone records
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones --query 'HostedZones[?Name==`your-domain.com.`].Id' --output text)

aws route53 list-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} --region us-east-1 \
  --query 'ResourceRecordSets[?Name==`api.your-domain.com.`]'
```

### Step 6: Check Node-Level DNS

```bash
# Identify the node running problematic pods
NODE_NAME=$(kubectl get pods -n production -l app=backend -o jsonpath='{.items[0].spec.nodeName}')

# Check resolv.conf on the node (via SSM)
aws ssm start-session --target $(aws ec2 describe-instances \
  --filters "Name=private-dns-name,Values=${NODE_NAME}" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text --region us-east-1)

# Once connected to the node:
cat /etc/resolv.conf
systemd-resolve --status
nslookup backend.production.svc.cluster.local
```

## Mitigation Steps

### Scenario A: CoreDNS Pods Crashed or OOMKilled

**Symptoms**: CoreDNS pods in CrashLoopBackOff or OOMKilled state; all DNS resolution fails cluster-wide.

1. Check the current state and restart reason:
   ```bash
   kubectl describe pods -n kube-system -l k8s-app=kube-dns | grep -A 5 "Last State"
   ```

2. If OOMKilled, increase memory limits:
   ```bash
   # Patch the CoreDNS deployment to increase resources
   kubectl patch deployment coredns -n kube-system --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "containers": [{
             "name": "coredns",
             "resources": {
               "limits": {
                 "memory": "512Mi",
                 "cpu": "500m"
               },
               "requests": {
                 "memory": "256Mi",
                 "cpu": "100m"
               }
             }
           }]
         }
       }
     }
   }'
   ```

3. Force restart CoreDNS pods:
   ```bash
   kubectl rollout restart deployment/coredns -n kube-system
   kubectl rollout status deployment/coredns -n kube-system --timeout=120s
   ```

4. If pods do not come back, check for node resource pressure:
   ```bash
   kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory,CONDITIONS:.status.conditions[-1].type
   ```

5. Scale CoreDNS if cluster has grown:
   ```bash
   # Check current replica count vs. node count
   NODE_COUNT=$(kubectl get nodes --no-headers | wc -l)
   echo "Nodes: ${NODE_COUNT}, recommended CoreDNS replicas: $(( (NODE_COUNT / 10) + 2 ))"

   # Scale CoreDNS (minimum 2, add 1 per 10 nodes)
   kubectl scale deployment/coredns -n kube-system --replicas=${DESIRED_REPLICAS}
   ```

### Scenario B: CoreDNS ConfigMap Corruption

**Symptoms**: CoreDNS pods running but returning SERVFAIL for all queries; recent ConfigMap changes in audit logs.

1. Check the current CoreDNS Corefile:
   ```bash
   kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}'
   ```

2. Verify the expected configuration:
   ```bash
   # The Corefile should look like this for EKS:
   cat <<'EXPECTED'
   .:53 {
       errors
       health {
           lameduck 5s
       }
       ready
       kubernetes cluster.local in-addr.arpa ip6.arpa {
           pods insecure
           fallthrough in-addr.arpa ip6.arpa
           ttl 30
       }
       prometheus :9153
       forward . /etc/resolv.conf {
           max_concurrent 1000
       }
       cache 30
       loop
       reload
       loadbalance
   }
   EXPECTED
   ```

3. Restore the CoreDNS ConfigMap if corrupted:
   ```bash
   kubectl apply -f - <<'EOF'
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: coredns
     namespace: kube-system
   data:
     Corefile: |
       .:53 {
           errors
           health {
               lameduck 5s
           }
           ready
           kubernetes cluster.local in-addr.arpa ip6.arpa {
               pods insecure
               fallthrough in-addr.arpa ip6.arpa
               ttl 30
           }
           prometheus :9153
           forward . /etc/resolv.conf {
               max_concurrent 1000
           }
           cache 30
           loop
           reload
           loadbalance
       }
   EOF
   ```

4. Restart CoreDNS to pick up the new config:
   ```bash
   kubectl rollout restart deployment/coredns -n kube-system
   ```

### Scenario C: ndots Misconfiguration Causing Slow/Failed External Resolution

**Symptoms**: Internal DNS works fine; external DNS (e.g., `api.stripe.com`, `hooks.slack.com`) resolves slowly or fails intermittently. Excessive NXDOMAIN responses in CoreDNS logs.

1. Check the ndots setting in affected pods:
   ```bash
   kubectl exec -n production deploy/backend -- cat /etc/resolv.conf
   # Look for: options ndots:5
   # With ndots:5, any name with <5 dots gets search suffixes appended first
   # e.g., "api.stripe.com" tries: api.stripe.com.production.svc.cluster.local first
   ```

2. Verify the excessive NXDOMAIN rate:
   ```bash
   kubectl logs -n kube-system -l k8s-app=kube-dns --tail=500 | grep -c "NXDOMAIN"
   ```

3. Fix by adding a dnsConfig to affected deployments:
   ```bash
   # Patch backend deployment to optimize DNS resolution
   kubectl patch deployment backend -n production --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "dnsConfig": {
             "options": [
               {"name": "ndots", "value": "2"},
               {"name": "attempts", "value": "3"},
               {"name": "timeout", "value": "2"}
             ]
           }
         }
       }
     }
   }'

   # Repeat for other services that call external endpoints
   for SERVICE in service-b service-c worker-high worker-low; do
     kubectl patch deployment ${SERVICE} -n production --type=strategic -p '{
       "spec": {
         "template": {
           "spec": {
             "dnsConfig": {
               "options": [
                 {"name": "ndots", "value": "2"},
                 {"name": "attempts", "value": "3"},
                 {"name": "timeout", "value": "2"}
               ]
             }
           }
         }
       }
     }'
   done
   ```

4. For immediate relief, use FQDNs (trailing dot) in application configs:
   ```bash
   # In environment variables or config, use trailing dot for external hosts:
   # STRIPE_API_HOST=api.stripe.com.     (note the trailing dot)
   # SLACK_WEBHOOK_HOST=hooks.slack.com.
   ```

### Scenario D: VPC DNS Resolver Throttling (AmazonProvidedDNS Limit)

**Symptoms**: Intermittent DNS failures across all nodes; CloudWatch metrics show `Route53Resolver` packet drops; affects both internal and external DNS but not consistently.

1. Check Route53 Resolver query logs:
   ```bash
   # Check if resolver query logging is enabled
   aws route53resolver list-resolver-query-log-configs --region us-east-1

   # Check CloudWatch for DNS throttling
   aws cloudwatch get-metric-statistics \
     --namespace "AWS/Route53Resolver" \
     --metric-name "InboundQueryVolume" \
     --dimensions Name=EndpointId,Value=${RESOLVER_ENDPOINT_ID} \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Sum \
     --region us-east-1
   ```

2. The VPC DNS resolver has a hard limit of 1024 packets per second per ENI. Check current ENI count:
   ```bash
   aws ec2 describe-network-interfaces \
     --filters "Name=vpc-id,Values=${VPC_ID}" "Name=description,Values=*Route53*" \
     --query 'NetworkInterfaces[].NetworkInterfaceId' \
     --region us-east-1
   ```

3. Enable NodeLocal DNSCache to reduce pressure on CoreDNS and VPC resolver:
   ```bash
   # Deploy NodeLocal DNSCache DaemonSet
   # NOTE: The URL below points to the `master` branch, which may change without notice.
   # For production use, pin to a specific release tag, e.g.:
   # https://raw.githubusercontent.com/kubernetes/kubernetes/v1.31.0/cluster/addons/dns/nodelocaldns/nodelocaldns.yaml
   kubectl apply -f https://raw.githubusercontent.com/kubernetes/kubernetes/v1.31.0/cluster/addons/dns/nodelocaldns/nodelocaldns.yaml

   # Verify it is running
   kubectl get daemonset node-local-dns -n kube-system
   ```

4. If throttling is acute, increase CoreDNS cache TTL temporarily:
   ```bash
   # Patch the CoreDNS ConfigMap to increase cache TTL
   # NOTE: This is an interactive command. For a scriptable alternative, use kubectl apply
   # with the full ConfigMap (see Scenario B, step 3) and set "cache 300" instead of "cache 30".
   kubectl edit configmap coredns -n kube-system
   # Change: cache 30
   # To:     cache 300
   # Then restart CoreDNS
   kubectl rollout restart deployment/coredns -n kube-system
   ```

### Scenario E: Route53 Hosted Zone or Record Misconfiguration

**Symptoms**: External users cannot reach `api.your-domain.com` or `app.your-domain.com`; internal cluster DNS works fine; Route53 health checks show unhealthy.

1. Verify the ALB target is healthy:
   ```bash
   # Get the ALB DNS name from the ingress
   ALB_DNS=$(kubectl get ingress -n production your-app-ingress \
     -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
   echo "ALB DNS: ${ALB_DNS}"

   # Resolve the ALB directly
   nslookup ${ALB_DNS}

   # Get the ALB ARN
   ALB_ARN=$(aws elbv2 describe-load-balancers --region us-east-1 \
     --query "LoadBalancers[?DNSName=='${ALB_DNS}'].LoadBalancerArn" --output text)

   # Check target group health
   TG_ARN=$(aws elbv2 describe-target-groups --load-balancer-arn ${ALB_ARN} --region us-east-1 \
     --query 'TargetGroups[0].TargetGroupArn' --output text)

   aws elbv2 describe-target-health --target-group-arn ${TG_ARN} --region us-east-1
   ```

2. Verify Route53 alias record points to the correct ALB:
   ```bash
   aws route53 list-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} --region us-east-1 \
     --query "ResourceRecordSets[?Name=='api.your-domain.com.']"
   ```

3. If the record is wrong, update it:
   ```bash
   aws route53 change-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} --region us-east-1 \
     --change-batch '{
       "Changes": [{
         "Action": "UPSERT",
         "ResourceRecordSet": {
           "Name": "api.your-domain.com",
           "Type": "A",
           "AliasTarget": {
             "HostedZoneId": "'${ALB_HOSTED_ZONE_ID}'",
             "DNSName": "'${ALB_DNS}'",
             "EvaluateTargetHealth": true
           }
         }
       }]
     }'
   ```

4. Force health check re-evaluation:
   ```bash
   aws route53 update-health-check --health-check-id ${HEALTH_CHECK_ID} --region us-east-1 \
     --reset-elements FullyQualifiedDomainName
   ```

### Scenario F: DNS Cache Poisoning Suspected

**Symptoms**: DNS queries return unexpected IP addresses; SSL certificate mismatches on known-good domains; users reporting redirect to unknown sites.

1. **IMMEDIATELY escalate to @security-team** - this may be an active attack.

2. Capture forensic data before making changes:
   ```bash
   # Record current DNS responses from multiple sources
   for RESOLVER in 8.8.8.8 1.1.1.1 169.254.169.253; do
     echo "=== Resolver: ${RESOLVER} ==="
     dig @${RESOLVER} api.your-domain.com A +short
     dig @${RESOLVER} api.your-domain.com AAAA +short
     dig @${RESOLVER} api.your-domain.com NS +short
   done > /tmp/dns-forensics-$(date +%Y%m%d%H%M%S).txt

   # Check DNSSEC validation
   dig api.your-domain.com +dnssec +multi

   # Capture CoreDNS cache state
   kubectl exec -n kube-system $(kubectl get pods -n kube-system -l k8s-app=kube-dns \
     -o jsonpath='{.items[0].metadata.name}') -- kill -SIGUSR1 1
   kubectl logs -n kube-system -l k8s-app=kube-dns --tail=200 > /tmp/coredns-cache-dump.txt
   ```

3. Flush DNS caches at all levels:
   ```bash
   # Restart CoreDNS to flush cluster DNS cache
   kubectl rollout restart deployment/coredns -n kube-system

   # Restart affected application pods to flush local caches
   kubectl rollout restart deployment/backend -n production
   kubectl rollout restart deployment/frontend -n production
   kubectl rollout restart deployment/service-c -n production
   ```

4. Enable DNSSEC on Route53 if not already enabled:
   ```bash
   aws route53 enable-hosted-zone-dnssec --hosted-zone-id ${HOSTED_ZONE_ID} --region us-east-1
   ```

## Verification

After applying any mitigation, verify DNS is working correctly:

```bash
# Test internal service resolution
kubectl run dns-verify --namespace=production --rm -it --restart=Never \
  --image=busybox:1.36 -- sh -c '
    echo "=== Internal DNS ==="
    nslookup backend.production.svc.cluster.local
    nslookup redis-master.production.svc.cluster.local
    nslookup postgres.production.svc.cluster.local
    echo "=== External DNS ==="
    nslookup api.stripe.com
    nslookup hooks.slack.com
    nslookup api.your-domain.com
    echo "=== Resolution Time ==="
    time nslookup backend.production.svc.cluster.local
  '

# Verify CoreDNS metrics show no errors
kubectl exec -n kube-system $(kubectl get pods -n kube-system -l k8s-app=kube-dns \
  -o jsonpath='{.items[0].metadata.name}') -- wget -qO- http://localhost:9153/metrics | \
  grep -E "(coredns_dns_responses_total|coredns_panics_total)"

# Verify application pods are healthy
kubectl get pods -n production -o wide | grep -E "(service-a|service-b|service-c|worker)"

# Check monitoring alert has recovered
# -> https://<your-monitoring-url>/monitors/manage?q=DNSResolutionFailure
```

Expected: All nslookup commands return valid IPs; SERVFAIL count is 0; all application pods are Running.

## Rollback

If DNS configuration changes cause further issues:

```bash
# Rollback CoreDNS deployment to previous version
kubectl rollout undo deployment/coredns -n kube-system
# WARNING: `kubectl rollout undo` reverts the Deployment spec but does NOT revert
# ConfigMap changes. If you modified the CoreDNS ConfigMap, you must also manually
# restore the previous ConfigMap (see Scenario B, step 3 for the default Corefile).

# If ndots changes caused issues, remove the dnsConfig patch
kubectl patch deployment backend -n production --type=json -p '[
  {"op": "remove", "path": "/spec/template/spec/dnsConfig"}
]'

# If Route53 record was changed incorrectly, revert via change batch
aws route53 change-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} --region us-east-1 \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "api.your-domain.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "'${PREVIOUS_ALB_HOSTED_ZONE_ID}'",
          "DNSName": "'${PREVIOUS_ALB_DNS}'",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'

# Rollback NodeLocal DNSCache if it caused issues
kubectl delete daemonset node-local-dns -n kube-system
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Not resolved in 10 minutes | @your-team-lead |
| Customer-facing impact confirmed | @incident-commander |
| DNS cache poisoning suspected | @security-team (immediate) |
| Route53 service issue suspected | AWS Support (Severity 1) |
| Multiple AZs affected | @vp-engineering |

## Related Runbooks

- [SSL/TLS Certificate Expiry](../networking/ssl-certificate-expiry.md)
- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [Pod CrashLoopBackOff](../kubernetes/pod-crashloopbackoff.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-15 | @your-team | Added Scenario F: DNS cache poisoning |
| 2025-09-20 | @your-team | Added NodeLocal DNSCache guidance |
| 2025-06-10 | @your-team | Added ndots optimization (Scenario C) |
| 2025-03-01 | @your-team | Initial version |
