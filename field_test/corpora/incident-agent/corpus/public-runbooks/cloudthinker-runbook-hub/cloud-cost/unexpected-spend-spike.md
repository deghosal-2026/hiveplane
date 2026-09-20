# Unexpected Cloud Spend Spike

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Service** | AWS account (all services) |
| **Owner** | @finops-team |
| **Last Reviewed** | 2025-12-01 |
| **Alert** | `AWSCostAnomalyDetected` |
| **Tags** | `cloud-cost`, `finops`, `aws`, `billing` |

## Summary

AWS Cost Anomaly Detection or a custom budget alert has flagged an unexpected increase in cloud spend. This runbook guides investigation of the cost driver, determines whether the spend is legitimate (e.g., scaling event) or wasteful (e.g., forgotten resources), and outlines remediation actions.

## Impact

- Monthly cloud budget may be exceeded, requiring finance escalation.
- Uncontrolled spend erodes margins and can trigger contractual cost-pass-through clauses with enterprise customers.
- Forgotten resources (idle EC2, orphaned EBS, unattached EIPs) waste money continuously until discovered.
- Large data transfer costs can accumulate rapidly across regions or to the internet.

## Prerequisites

- AWS CLI configured with `ce:Get*`, `ce:Describe*`, `ec2:Describe*`, `rds:Describe*`, `elasticache:Describe*` permissions
- Access to AWS Cost Explorer: `https://console.aws.amazon.com/cost-management/home#/cost-explorer`
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/aws-costs/aws-cost-overview`
- Access to the CloudThinker AWS Organization account (account ID: `${AWS_ACCOUNT_ID}`)
- Familiarity with CloudThinker resource tagging conventions (`env:production`, `team:<team-name>`, `service:<service-name>`)

## Triage & Diagnosis

### Step 1: Identify the anomaly scope

```bash
# Get cost anomaly details from AWS
aws ce get-anomalies \
  --date-interval Start=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --region us-east-1 \
  --output table
```

```bash
# Get daily cost breakdown for the last 14 days
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-14d +%Y-%m-%d 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --region us-east-1 \
  --output table
```

### Step 2: Identify top cost drivers by service

```bash
# Top 10 services by cost in the current billing period
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --query "ResultsByTime[0].Groups | sort_by(@, &Metrics.BlendedCost.Amount) | reverse(@) | [:10]" \
  --region us-east-1 \
  --output table
```

### Step 3: Break down by tag (team / service / environment)

```bash
# Cost by team tag
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=TAG,Key=team \
  --region us-east-1 \
  --output table
```

```bash
# Cost by service tag
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=TAG,Key=service \
  --region us-east-1 \
  --output table
```

### Step 4: Check for untagged resources

```bash
# Find untagged EC2 instances
aws ec2 describe-instances \
  --query "Reservations[].Instances[?!Tags || !contains(Tags[].Key, 'team')].[InstanceId,InstanceType,State.Name,LaunchTime]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

```bash
# Find untagged EBS volumes
aws ec2 describe-volumes \
  --query "Volumes[?!Tags || !contains(Tags[].Key, 'team')].[VolumeId,Size,State,CreateTime]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

### Step 5: Check for common cost leak sources

```bash
# Orphaned EBS volumes (not attached to any instance)
aws ec2 describe-volumes \
  --filters Name=status,Values=available \
  --query "Volumes[].[VolumeId,Size,VolumeType,CreateTime]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

```bash
# Unassociated Elastic IPs (each costs ~$3.60/month when unused)
aws ec2 describe-addresses \
  --query "Addresses[?!InstanceId && !NetworkInterfaceId].[PublicIp,AllocationId]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

```bash
# Old EBS snapshots (older than 90 days)
aws ec2 describe-snapshots \
  --owner-ids self \
  --query "Snapshots[?StartTime<='$(date -u -v-90d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '90 days ago' +%Y-%m-%dT%H:%M:%S)'].[SnapshotId,VolumeSize,StartTime,Description]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

```bash
# Check for non-production RDS instances that may have been left running
aws rds describe-db-instances \
  --query "DBInstances[?!contains(DBInstanceIdentifier, 'prod')].[DBInstanceIdentifier,DBInstanceClass,DBInstanceStatus,Engine]" \
  --region ${AWS_REGION:-us-east-1} \
  --output table
```

### Step 6: Check data transfer costs

```bash
# Data transfer costs by region
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"USAGE_TYPE_GROUP","Values":["EC2: Data Transfer - Internet (Out)","EC2: Data Transfer - Region to Region (Out)"]}}' \
  --group-by Type=DIMENSION,Key=REGION \
  --region us-east-1 \
  --output table
```

## Mitigation Steps

### Scenario A: Forgotten development / staging resources

Resources from a dev/staging environment were left running.

1. Identify the resources:
   ```bash
   # List all non-production EC2 instances
   aws ec2 describe-instances \
     --filters "Name=tag:env,Values=dev,staging,test" \
     --query "Reservations[].Instances[].[InstanceId,InstanceType,Tags[?Key=='Name'].Value|[0],State.Name]" \
     --region ${AWS_REGION:-us-east-1} \
     --output table
   ```

2. Terminate confirmed unused instances:
   ```bash
   aws ec2 terminate-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION:-us-east-1}
   ```

3. Delete orphaned EBS volumes:
   ```bash
   aws ec2 delete-volume --volume-id ${VOLUME_ID} --region ${AWS_REGION:-us-east-1}
   ```

4. Release unassociated Elastic IPs:
   ```bash
   aws ec2 release-address --allocation-id ${ALLOCATION_ID} --region ${AWS_REGION:-us-east-1}
   ```

### Scenario B: Auto-scaling event drove up costs

HPA or cluster autoscaler added nodes/pods in response to legitimate load.

1. Check recent scaling events:
   ```bash
   kubectl get events -n production --field-selector reason=SuccessfulRescale --sort-by='.lastTimestamp' | tail -20

   # Check cluster autoscaler activity
   kubectl logs -n kube-system -l app=cluster-autoscaler --tail=100 | grep -E "scale-up|scale-down"
   ```

2. Check current node count vs. baseline:
   ```bash
   kubectl get nodes -o wide
   aws ec2 describe-instances \
     --filters "Name=tag:eks:cluster-name,Values=cloudthinker-prod" "Name=instance-state-name,Values=running" \
     --query "Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime]" \
     --output table
   ```

3. If load has subsided, force cluster autoscaler to scale down:
   ```bash
   # Cordon and drain excess nodes
   kubectl cordon ${NODE_NAME}
   kubectl drain ${NODE_NAME} --ignore-daemonsets --delete-emptydir-data --timeout=120s
   ```

### Scenario C: Data transfer cost spike

Cross-region or internet-bound data transfer exceeded expectations.

1. Identify the source:
   ```bash
   # Check NAT Gateway data processing (often the culprit)
   aws ec2 describe-nat-gateways \
     --query "NatGateways[].[NatGatewayId,SubnetId,State]" --region ${AWS_REGION:-us-east-1} --output table

   # Check CloudWatch for NAT Gateway bytes
   aws cloudwatch get-metric-statistics \
     --namespace AWS/NATGateway \
     --metric-name BytesOutToDestination \
     --dimensions Name=NatGatewayId,Value=${NAT_GW_ID} \
     --start-time $(date -u -v-7d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 86400 \
     --statistics Sum \
     --region ${AWS_REGION:-us-east-1} \
     --output table
   ```

2. Consider VPC endpoints for high-traffic AWS services:
   ```bash
   # Check existing VPC endpoints
   aws ec2 describe-vpc-endpoints \
     --query "VpcEndpoints[].[ServiceName,VpcEndpointId,State]" --region ${AWS_REGION:-us-east-1} --output table
   ```

### Scenario D: RDS or ElastiCache cost increase

Database instances were scaled up or a new instance was launched.

1. Check recent RDS modifications:
   ```bash
   aws rds describe-events \
     --source-type db-instance \
     --duration 10080 \
     --region ${AWS_REGION:-us-east-1} \
     --output table
   ```

2. Check if a read replica was created:
   ```bash
   aws rds describe-db-instances \
     --query "DBInstances[].[DBInstanceIdentifier,DBInstanceClass,ReadReplicaSourceDBInstanceIdentifier]" \
     --region ${AWS_REGION:-us-east-1} \
     --output table
   ```

## Verification

```bash
# Confirm the identified resources have been cleaned up
aws ec2 describe-instances --filters "Name=instance-state-name,Values=running" \
  --query "length(Reservations[].Instances[])" \
  --region ${AWS_REGION:-us-east-1}

# Verify current daily run-rate
aws ce get-cost-forecast \
  --time-period Start=$(date +%Y-%m-%d),End=$(date -u -v+30d +%Y-%m-%d 2>/dev/null || date -u -d '+30 days' +%Y-%m-%d) \
  --granularity MONTHLY \
  --metric BLENDED_COST \
  --region us-east-1 \
  --output table

# Check Grafana cost dashboard for trend confirmation
# https://grafana.internal.cloudthinker.io/d/aws-costs/aws-cost-overview
```

Expected: Daily run-rate returning to baseline, no orphaned resources remaining, cost anomaly alert clearing within 24-48 hours.

## Rollback

Cost optimization actions are generally non-reversible (terminated instances, deleted volumes). Before taking destructive actions:

1. Verify the resource is truly unused by checking CloudWatch metrics for the last 14 days
2. Create an AMI snapshot before terminating instances if in doubt:
   ```bash
   aws ec2 create-image --instance-id ${INSTANCE_ID} --name "backup-before-cleanup-$(date +%Y%m%d)" --no-reboot --region ${AWS_REGION:-us-east-1}
   ```

3. Tag volumes for deletion review before removing:
   ```bash
   aws ec2 create-tags --resources ${VOLUME_ID} --tags Key=ScheduledForDeletion,Value=$(date +%Y-%m-%d) --region ${AWS_REGION:-us-east-1}
   ```

## Escalation

| Condition | Contact |
|-----------|---------|
| Spend spike > 20% of monthly budget | @finops-team-lead |
| Spend spike > 50% of monthly budget | @finops-team-lead + @cfo |
| Untagged resources from unknown origin | @platform-team + @security-team |
| Suspected crypto mining or compromised credentials | @security-team + @incident-commander |
| Budget needs to be adjusted for legitimate scaling | @finops-team-lead + @vp-engineering |
| AWS billing discrepancy suspected | Open AWS Support case (Billing) |

## Related Runbooks

- [Idle Resource Cleanup](idle-resource-cleanup.md)
- [Right-sizing Over-provisioned Instances](right-sizing-instances.md)
- [API P99 Latency SLA Breach](../application/api-high-latency.md)
- [PVC Pending / Storage Full](../kubernetes/pvc-pending-storage-full.md)
- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-01 | @rgupta | Added NAT Gateway data transfer analysis |
| 2025-09-10 | @jchen | Added untagged resource detection steps |
| 2025-06-25 | @finops-bot | Initial version |
