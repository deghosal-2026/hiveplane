# Idle Resource Cleanup

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Service** | AWS infrastructure (EC2, EBS, ELB, EIP) |
| **Owner** | @finops-team |
| **Last Reviewed** | 2026-01-20 |
| **Alert** | `IdleResourceDetected` |
| **Tags** | `cloud-cost`, `aws`, `finops`, `cleanup`, `optimization` |

## Summary

Idle AWS resources (unused EC2 instances, unattached EBS volumes, empty load balancers, unassociated Elastic IPs) are generating unnecessary costs. This runbook provides procedures to identify, validate, and safely remove these resources.

## Impact

- **Cost**: Idle resources in CloudThinker's AWS account have historically accounted for $2,000-5,000/month in waste.
- **No user impact**: Removing genuinely idle resources has zero impact on production services.
- **Security**: Idle resources with outdated AMIs or configurations can be a security risk.

## Prerequisites

- AWS CLI configured with the `cloudthinker-production` profile
- IAM permissions: `ec2:Describe*`, `ec2:TerminateInstances`, `ec2:DeleteVolume`, `elasticloadbalancing:Describe*`, `elasticloadbalancing:DeleteLoadBalancer`, `ec2:ReleaseAddress`
- Access to AWS Cost Explorer
- Grafana dashboard: [AWS Cost Overview](https://grafana.internal.cloudthinker.io/d/aws-cost/aws-cost-overview)
- Datadog monitor: **IdleResourceDetected**

## Triage & Diagnosis

### Step 1: Review the Datadog alert

The **IdleResourceDetected** alert fires weekly from a scheduled Lambda that scans for idle resources. Check the alert details for the list of flagged resources.

### Step 2: Identify idle EC2 instances

Instances with average CPU utilization below 5% over the past 14 days:

```bash
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId,InstanceType,LaunchTime,Tags[?Key==`Name`].Value|[0],Tags[?Key==`Environment`].Value|[0]]' \
  --output table --region us-east-1
```

Cross-reference with CloudWatch metrics:

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=${INSTANCE_ID} \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Average \
  --region us-east-1 \
  --output table
```

### Step 3: Identify unattached EBS volumes

Volumes in `available` state (not attached to any instance):

```bash
aws ec2 describe-volumes \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].[VolumeId,Size,VolumeType,CreateTime,Tags[?Key==`Name`].Value|[0]]' \
  --output table --region us-east-1
```

Estimate monthly cost of unattached volumes:

```bash
aws ec2 describe-volumes \
  --filters "Name=status,Values=available" \
  --query 'Volumes[*].Size' --output text --region us-east-1 | \
  tr '\t' '\n' | awk '{sum+=$1} END {printf "Total unattached: %d GB (~$%.2f/month at $0.10/GB)\n", sum, sum*0.10}'
```

### Step 4: Identify idle Elastic Load Balancers

ALBs/NLBs with zero healthy targets:

```bash
for arn in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[*].LoadBalancerArn' --output text --region us-east-1); do
  tg_arns=$(aws elbv2 describe-target-groups --load-balancer-arn ${arn} --query 'TargetGroups[*].TargetGroupArn' --output text --region us-east-1)
  healthy=0
  for tg in ${tg_arns}; do
    count=$(aws elbv2 describe-target-health --target-group-arn ${tg} --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text --region us-east-1)
    healthy=$((healthy + count))
  done
  if [ "${healthy}" -eq 0 ]; then
    name=$(aws elbv2 describe-load-balancers --load-balancer-arns ${arn} --query 'LoadBalancers[0].LoadBalancerName' --output text --region us-east-1)
    echo "IDLE LB: ${name} (${arn})"
  fi
done
```

### Step 5: Identify unassociated Elastic IPs

```bash
aws ec2 describe-addresses \
  --query 'Addresses[?AssociationId==`null`].[PublicIp,AllocationId,Tags[?Key==`Name`].Value|[0]]' \
  --output table --region us-east-1
```

Each unassociated EIP costs $3.65/month ($0.005/hour).

## Mitigation Steps

### Scenario A: Terminate Idle EC2 Instances

1. Confirm the instance is genuinely idle by checking with the owning team (tagged via `Owner` tag):

   ```bash
   aws ec2 describe-tags --filters "Name=resource-id,Values=${INSTANCE_ID}" --output table --region us-east-1
   ```

2. Create an AMI backup before terminating (safety net):

   ```bash
   aws ec2 create-image --instance-id ${INSTANCE_ID} \
     --name "backup-before-cleanup-$(date +%Y%m%d)-${INSTANCE_ID}" \
     --no-reboot --region us-east-1
   ```

3. Terminate the instance:

   ```bash
   aws ec2 terminate-instances --instance-ids ${INSTANCE_ID} --region us-east-1
   ```

4. Record the termination in the FinOps tracker spreadsheet.

### Scenario B: Delete Unattached EBS Volumes

1. Create a snapshot before deleting (safety net):

   ```bash
   aws ec2 create-snapshot --volume-id ${VOLUME_ID} \
     --description "backup-before-cleanup-$(date +%Y%m%d)" \
     --tag-specifications "ResourceType=snapshot,Tags=[{Key=Purpose,Value=pre-cleanup-backup}]" \
     --region us-east-1
   ```

2. Wait for the snapshot to complete:

   ```bash
   aws ec2 wait snapshot-completed --snapshot-ids ${SNAPSHOT_ID} --region us-east-1
   ```

3. Delete the unattached volume:

   ```bash
   aws ec2 delete-volume --volume-id ${VOLUME_ID} --region us-east-1
   ```

### Scenario C: Delete Idle Load Balancers

1. Confirm no DNS records point to the load balancer:

   ```bash
   aws route53 list-resource-record-sets --hosted-zone-id ${HOSTED_ZONE_ID} \
     --query "ResourceRecordSets[?AliasTarget.DNSName=='${LB_DNS_NAME}']" \
     --output table --region us-east-1
   ```

2. Delete associated target groups first:

   ```bash
   aws elbv2 delete-target-group --target-group-arn ${TARGET_GROUP_ARN} --region us-east-1
   ```

3. Delete the load balancer:

   ```bash
   aws elbv2 delete-load-balancer --load-balancer-arn ${LB_ARN} --region us-east-1
   ```

### Scenario D: Release Unassociated Elastic IPs

1. Confirm the EIP is not referenced in any security group rules or application configs.

2. Release the Elastic IP:

   ```bash
   aws ec2 release-address --allocation-id ${ALLOCATION_ID} --region us-east-1
   ```

## Verification

After cleanup, verify no production services were affected:

```bash
kubectl get pods -n production -o wide | grep -v Running
```

Check the CloudThinker health endpoint:

```bash
curl -s https://api.cloudthinker.io/health | jq .
```

Review the AWS Cost Explorer to confirm cost reduction (visible within 24-48 hours):

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --region us-east-1
```

## Rollback

If a terminated resource was actually needed:

- **EC2**: Launch a new instance from the AMI backup created before termination:

  ```bash
  aws ec2 run-instances --image-id ${BACKUP_AMI_ID} \
    --instance-type ${INSTANCE_TYPE} \
    --subnet-id ${SUBNET_ID} \
    --security-group-ids ${SG_ID} \
    --region us-east-1
  ```

- **EBS**: Create a volume from the pre-deletion snapshot:

  ```bash
  aws ec2 create-volume --snapshot-id ${SNAPSHOT_ID} \
    --availability-zone ${AZ} \
    --volume-type gp3 \
    --region us-east-1
  ```

- **EIP**: Allocate a new Elastic IP (the original public IP cannot be recovered):

  ```bash
  aws ec2 allocate-address --domain vpc --region us-east-1
  ```

## Escalation

| Condition | Contact |
|-----------|---------|
| Unsure if resource is idle | @platform-team (check owning team via tags) |
| Resource belongs to payment-service | @payments-team before any action |
| Total idle cost exceeds $10,000/month | @vp-engineering + @finops-team-lead |
| Cleanup accidentally removed active resource | @incident-commander |

## Related Runbooks

- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)
- [Right-sizing Over-provisioned Instances](../cloud-cost/right-sizing-instances.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-20 | @finops-team | Added cost estimation commands for EBS volumes |
| 2025-11-15 | @finops-team | Added AMI backup step before EC2 termination |
| 2025-09-01 | @finops-team | Initial version |
