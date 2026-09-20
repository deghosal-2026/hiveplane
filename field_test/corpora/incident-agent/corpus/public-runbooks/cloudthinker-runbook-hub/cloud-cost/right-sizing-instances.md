# Right-sizing Over-provisioned Instances

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Service** | All services (infrastructure-wide) |
| **Owner** | @finops-team |
| **Last Reviewed** | 2025-12-10 |
| **Alert** | `InstanceUnderutilized` |
| **Tags** | `cloud-cost`, `finops`, `right-sizing`, `compute-optimizer`, `eks` |

## Summary

AWS Compute Optimizer or Datadog cost monitoring has identified instances (EKS nodes, RDS, ElastiCache) that are consistently underutilized. This runbook guides the analysis, approval, and execution of right-sizing changes to reduce compute spend while maintaining performance SLAs.

## Impact

- **Cost**: Over-provisioned instances waste an estimated 20-40% of compute budget; CloudThinker's monthly compute spend is approximately $45,000
- **No immediate user impact**: This is a proactive optimization, not an incident
- **Risk if done incorrectly**: Under-provisioning can cause latency spikes, OOM kills, or pod evictions during peak traffic
- **SLA consideration**: Changes must not degrade P99 latency below the 200ms API SLA target

## Prerequisites

- AWS CLI v2 with permissions for Compute Optimizer, EC2, EKS, RDS, ElastiCache, Cost Explorer
- `kubectl` configured for production cluster
- Access to Datadog and Grafana dashboards
- Access to AWS Cost Explorer and Billing Console
- Familiarity with CloudThinker traffic patterns (peak hours: 09:00-17:00 UTC Mon-Fri)
- **Change approval**: Right-sizing changes require @platform-team-lead approval before execution
- At least 14 days of utilization data before making changes

## Triage & Diagnosis

### Step 1: Review the Compute Optimizer Recommendations

```bash
# Get Compute Optimizer recommendations for EC2 instances
aws compute-optimizer get-ec2-instance-recommendations \
  --region us-east-1 \
  --filters name=Finding,values=OVER_PROVISIONED \
  --query 'instanceRecommendations[].{
    InstanceId: instanceArn,
    InstanceName: instanceName,
    CurrentType: currentInstanceType,
    Finding: finding,
    RecommendedType: recommendationOptions[0].instanceType,
    EstimatedSavings: recommendationOptions[0].estimatedMonthlySavings.value,
    Risk: recommendationOptions[0].performanceRisk
  }' \
  --output table

# Get recommendations for EKS node groups
aws compute-optimizer get-ec2-instance-recommendations \
  --region us-east-1 \
  --filters name=Finding,values=OVER_PROVISIONED \
  --account-ids $(aws sts get-caller-identity --query Account --output text) \
  --query 'instanceRecommendations[?contains(instanceName, `eks`)]' \
  --output json
```

### Step 2: Analyze CPU and Memory Utilization (14-Day Window)

```bash
# EKS Node CPU utilization over 14 days
for NODE in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
  echo "=== ${NODE} ==="
  INSTANCE_ID=$(kubectl get node ${NODE} -o jsonpath='{.spec.providerID}' | awk -F/ '{print $NF}')

  aws cloudwatch get-metric-statistics \
    --namespace "AWS/EC2" \
    --metric-name "CPUUtilization" \
    --dimensions Name=InstanceId,Value=${INSTANCE_ID} \
    --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 3600 \
    --statistics Average Maximum \
    --region us-east-1 \
    --query 'Datapoints | sort_by(@, &Timestamp) | [-24:].[Timestamp,Average,Maximum]' \
    --output table
done

# EKS Node memory utilization from Datadog
echo "Check Datadog: https://app.datadoghq.com/metric/explorer?exp_metric=kubernetes.memory.usage_pct&exp_group=host"

# Grafana Dashboard: "EKS Node Utilization"
echo "Check Grafana: https://grafana.internal.cloudthinker.io/d/eks-nodes/eks-node-utilization"
```

### Step 3: Analyze Pod Resource Requests vs Actual Usage

```bash
# Compare requested resources to actual usage for all pods
kubectl top pods -n production --sort-by=cpu | head -20

# Get resource requests and limits for all deployments
kubectl get deployments -n production -o custom-columns=\
NAME:.metadata.name,\
CPU_REQ:.spec.template.spec.containers[0].resources.requests.cpu,\
CPU_LIM:.spec.template.spec.containers[0].resources.limits.cpu,\
MEM_REQ:.spec.template.spec.containers[0].resources.requests.memory,\
MEM_LIM:.spec.template.spec.containers[0].resources.limits.memory

# Get Vertical Pod Autoscaler recommendations (if VPA is installed)
kubectl get vpa -n production -o custom-columns=\
NAME:.metadata.name,\
CPU_TARGET:.status.recommendation.containerRecommendations[0].target.cpu,\
MEM_TARGET:.status.recommendation.containerRecommendations[0].target.memory,\
CPU_UPPER:.status.recommendation.containerRecommendations[0].upperBound.cpu,\
MEM_UPPER:.status.recommendation.containerRecommendations[0].upperBound.memory
```

### Step 4: Analyze RDS Instance Utilization

```bash
# RDS CPU utilization over 14 days
aws cloudwatch get-metric-statistics \
  --namespace "AWS/RDS" \
  --metric-name "CPUUtilization" \
  --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average Maximum \
  --region us-east-1 \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-48:].[Timestamp,Average,Maximum]' \
  --output table

# RDS freeable memory
aws cloudwatch get-metric-statistics \
  --namespace "AWS/RDS" \
  --metric-name "FreeableMemory" \
  --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average Minimum \
  --region us-east-1 \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-48:].[Timestamp,Average,Minimum]' \
  --output table

# RDS connection count (ensure headroom after downsizing)
aws cloudwatch get-metric-statistics \
  --namespace "AWS/RDS" \
  --metric-name "DatabaseConnections" \
  --dimensions Name=DBInstanceIdentifier,Value=cloudthinker-prod-db \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average Maximum \
  --region us-east-1 \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-48:].[Timestamp,Average,Maximum]' \
  --output table

# Current RDS instance type
aws rds describe-db-instances \
  --db-instance-identifier cloudthinker-prod-db \
  --region us-east-1 \
  --query 'DBInstances[0].{InstanceClass:DBInstanceClass,Engine:Engine,MultiAZ:MultiAZ,Storage:AllocatedStorage}'
```

### Step 5: Analyze ElastiCache Utilization

```bash
# ElastiCache Redis CPU utilization
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ElastiCache" \
  --metric-name "EngineCPUUtilization" \
  --dimensions Name=CacheClusterId,Value=cloudthinker-prod-redis-001 \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average Maximum \
  --region us-east-1 \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-48:]' \
  --output table

# ElastiCache memory usage
aws cloudwatch get-metric-statistics \
  --namespace "AWS/ElastiCache" \
  --metric-name "DatabaseMemoryUsagePercentage" \
  --dimensions Name=CacheClusterId,Value=cloudthinker-prod-redis-001 \
  --start-time $(date -u -v-14d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average Maximum \
  --region us-east-1 \
  --query 'Datapoints | sort_by(@, &Timestamp) | [-48:]' \
  --output table

# Current ElastiCache node type
aws elasticache describe-cache-clusters \
  --cache-cluster-id cloudthinker-prod-redis-001 \
  --region us-east-1 \
  --query 'CacheClusters[0].{NodeType:CacheNodeType,Engine:Engine,NumNodes:NumCacheNodes}'
```

### Step 6: Calculate Current Spend and Projected Savings

```bash
# Get current monthly compute costs by service
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-30d +%Y-%m-%d 2>/dev/null || date -u -d '30 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics "UnblendedCost" \
  --group-by Type=DIMENSION,Key=SERVICE \
  --filter '{
    "Dimensions": {
      "Key": "SERVICE",
      "Values": [
        "Amazon Elastic Compute Cloud - Compute",
        "Amazon Relational Database Service",
        "Amazon ElastiCache"
      ]
    }
  }' \
  --region us-east-1

# Get cost breakdown by instance type
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-30d +%Y-%m-%d 2>/dev/null || date -u -d '30 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics "UnblendedCost" \
  --group-by Type=DIMENSION,Key=INSTANCE_TYPE \
  --filter '{
    "Dimensions": {
      "Key": "SERVICE",
      "Values": ["Amazon Elastic Compute Cloud - Compute"]
    }
  }' \
  --region us-east-1

# Check existing Reserved Instances and Savings Plans
aws ec2 describe-reserved-instances \
  --filters "Name=state,Values=active" \
  --region us-east-1 \
  --query 'ReservedInstances[].{Type:InstanceType,Count:InstanceCount,End:End,Scope:Scope}'

aws savingsplans describe-savings-plans \
  --region us-east-1 \
  --query 'savingsPlans[?state==`active`].{Type:savingsPlanType,Commitment:commitment,End:end,Utilization:utilizationPercentage}'
```

## Mitigation Steps

### Scenario A: Right-size EKS Node Group (Managed Node Group)

**Conditions**: Average CPU < 30% and average memory < 40% over 14 days; peak usage < 60%.

1. Determine the target instance type:
   ```bash
   # Current node group configuration
   NODEGROUP_NAME="cloudthinker-prod-workers"
   CLUSTER_NAME="cloudthinker-prod"

   aws eks describe-nodegroup \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME} \
     --region us-east-1 \
     --query 'nodegroup.{InstanceTypes:instanceTypes,DesiredSize:scalingConfig.desiredSize,MinSize:scalingConfig.minSize,MaxSize:scalingConfig.maxSize}'

   # Common right-sizing paths:
   # m5.2xlarge (8 vCPU, 32 GiB) -> m5.xlarge (4 vCPU, 16 GiB)   ~50% savings
   # m5.xlarge  (4 vCPU, 16 GiB) -> m5.large  (2 vCPU, 8 GiB)    ~50% savings
   # c5.2xlarge (8 vCPU, 16 GiB) -> c5.xlarge (4 vCPU, 8 GiB)    ~50% savings

   # Graviton migration paths (additional 20% savings):
   # m5.xlarge  (4 vCPU, 16 GiB) -> m7g.xlarge (4 vCPU, 16 GiB)  ~20% savings
   # c5.xlarge  (4 vCPU, 8 GiB)  -> c7g.xlarge (4 vCPU, 8 GiB)   ~20% savings
   ```

2. Verify pod scheduling will work on smaller nodes:
   ```bash
   # Check the largest pod resource request in the namespace
   kubectl get pods -n production -o json | jq '
     .items[] | {
       name: .metadata.name,
       cpu_request: .spec.containers[0].resources.requests.cpu,
       mem_request: .spec.containers[0].resources.requests.memory
     }' | jq -s 'sort_by(.cpu_request) | reverse | .[0:5]'

   # Ensure the target instance type can fit the largest pod
   # Account for system reservations (~10% CPU, ~500Mi memory for kubelet/kube-proxy)
   ```

3. Create a new node group with the target instance type:
   ```bash
   # Create new node group (blue-green strategy for zero downtime)
   aws eks create-nodegroup \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME}-rightsized \
     --node-role ${NODE_ROLE_ARN} \
     --subnets ${SUBNET_IDS} \
     --instance-types ${TARGET_INSTANCE_TYPE} \
     --scaling-config minSize=${MIN_SIZE},maxSize=${MAX_SIZE},desiredSize=${DESIRED_SIZE} \
     --labels "nodegroup=${NODEGROUP_NAME}-rightsized" \
     --tags "Environment=production,ManagedBy=finops,RightsizedFrom=${CURRENT_INSTANCE_TYPE}" \
     --region us-east-1

   # Wait for the new node group to be active
   aws eks wait nodegroup-active \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME}-rightsized \
     --region us-east-1
   ```

4. Cordon and drain old nodes:
   ```bash
   # Get nodes from the old node group
   OLD_NODES=$(kubectl get nodes -l eks.amazonaws.com/nodegroup=${NODEGROUP_NAME} \
     -o jsonpath='{.items[*].metadata.name}')

   # Cordon old nodes (prevent new pods from scheduling)
   for NODE in ${OLD_NODES}; do
     kubectl cordon ${NODE}
     echo "Cordoned ${NODE}"
   done

   # Drain old nodes one at a time (graceful pod migration)
   for NODE in ${OLD_NODES}; do
     echo "Draining ${NODE}..."
     kubectl drain ${NODE} \
       --ignore-daemonsets \
       --delete-emptydir-data \
       --grace-period=120 \
       --timeout=300s
     echo "Drained ${NODE}"

     # Verify pods rescheduled successfully before draining next node
     kubectl get pods -n production --field-selector=status.phase!=Running,status.phase!=Succeeded | \
       grep -v "Completed" | grep -v "NAME"
     sleep 30
   done
   ```

5. Delete the old node group:
   ```bash
   # Verify all workloads are running on new nodes
   kubectl get pods -n production -o wide | grep -v "Completed"

   # Delete old node group
   aws eks delete-nodegroup \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name ${NODEGROUP_NAME} \
     --region us-east-1

   echo "Old node group deletion initiated"
   ```

### Scenario B: Right-size RDS Instance

**Conditions**: Average CPU < 25% and freeable memory > 60% of total over 14 days; connection count well below max_connections limit.

1. Identify the target RDS instance class:
   ```bash
   # Current instance details
   aws rds describe-db-instances \
     --db-instance-identifier cloudthinker-prod-db \
     --region us-east-1 \
     --query 'DBInstances[0].{
       Class:DBInstanceClass,
       Engine:Engine,
       EngineVersion:EngineVersion,
       MultiAZ:MultiAZ,
       StorageType:StorageType,
       IOPS:Iops,
       MaxConnections:Endpoint.Port
     }'

   # Common right-sizing paths for PostgreSQL:
   # db.r5.2xlarge (8 vCPU, 64 GiB) -> db.r5.xlarge (4 vCPU, 32 GiB)   ~50% savings
   # db.r5.xlarge  (4 vCPU, 32 GiB) -> db.r5.large  (2 vCPU, 16 GiB)   ~50% savings

   # Graviton migration paths:
   # db.r5.xlarge  (4 vCPU, 32 GiB) -> db.r7g.xlarge (4 vCPU, 32 GiB)  ~20% savings
   # db.r5.large   (2 vCPU, 16 GiB) -> db.r7g.large  (2 vCPU, 16 GiB)  ~20% savings

   # Verify max_connections for target instance
   # db.r5.large:  max_connections ~700
   # db.r5.xlarge: max_connections ~1600
   # Ensure peak connection count has sufficient headroom
   ```

2. Schedule the modification during maintenance window:
   ```bash
   # Check current maintenance window
   aws rds describe-db-instances \
     --db-instance-identifier cloudthinker-prod-db \
     --region us-east-1 \
     --query 'DBInstances[0].PreferredMaintenanceWindow'

   # Modify the instance class (applied during next maintenance window)
   aws rds modify-db-instance \
     --db-instance-identifier cloudthinker-prod-db \
     --db-instance-class ${TARGET_RDS_CLASS} \
     --apply-immediately false \
     --region us-east-1

   echo "RDS modification scheduled for next maintenance window"
   echo "To apply immediately (with brief downtime), use --apply-immediately"
   ```

3. If applying immediately (requires approval for production):
   ```bash
   # CAUTION: This causes a brief outage (30-60 seconds for Multi-AZ, longer for Single-AZ)
   # Notify @backend-team and @platform-team before executing

   aws rds modify-db-instance \
     --db-instance-identifier cloudthinker-prod-db \
     --db-instance-class ${TARGET_RDS_CLASS} \
     --apply-immediately \
     --region us-east-1

   # Monitor the modification
   watch -n 10 'aws rds describe-db-instances \
     --db-instance-identifier cloudthinker-prod-db \
     --region us-east-1 \
     --query "DBInstances[0].{Status:DBInstanceStatus,Class:DBInstanceClass,PendingModifications:PendingModifiedValues}"'
   ```

### Scenario C: Right-size ElastiCache Redis

**Conditions**: Average CPU < 20% and memory usage < 40% over 14 days.

1. Identify the target node type:
   ```bash
   # Current configuration
   aws elasticache describe-replication-groups \
     --replication-group-id cloudthinker-prod-redis \
     --region us-east-1 \
     --query 'ReplicationGroups[0].{
       NodeType:CacheNodeType,
       NumNodeGroups:NodeGroups|length(@),
       MemberClusters:MemberClusters
     }'

   # Common right-sizing paths:
   # cache.r5.xlarge  (4 vCPU, 26.32 GiB) -> cache.r5.large  (2 vCPU, 13.07 GiB)  ~50% savings
   # cache.r5.large   (2 vCPU, 13.07 GiB) -> cache.r6g.large (2 vCPU, 13.07 GiB)  ~20% savings (Graviton)

   # Check current memory usage to ensure data fits in smaller node
   USED_MEMORY=$(aws cloudwatch get-metric-statistics \
     --namespace "AWS/ElastiCache" \
     --metric-name "BytesUsedForCache" \
     --dimensions Name=CacheClusterId,Value=cloudthinker-prod-redis-001 \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 300 \
     --statistics Maximum \
     --region us-east-1 \
     --query 'Datapoints | sort_by(@, &Timestamp) | [-1].Maximum' --output text)

   echo "Current cache memory usage: $(echo "scale=2; ${USED_MEMORY} / 1073741824" | bc) GiB"
   ```

2. Modify the replication group:
   ```bash
   # Scale down the cache node type
   aws elasticache modify-replication-group \
     --replication-group-id cloudthinker-prod-redis \
     --cache-node-type ${TARGET_CACHE_NODE_TYPE} \
     --apply-immediately \
     --region us-east-1

   # Monitor the modification (takes 10-30 minutes)
   watch -n 30 'aws elasticache describe-replication-groups \
     --replication-group-id cloudthinker-prod-redis \
     --region us-east-1 \
     --query "ReplicationGroups[0].{Status:Status,NodeType:CacheNodeType}"'
   ```

### Scenario D: Graviton Migration (Cross-Architecture)

**Conditions**: Workloads are compatible with ARM64; no native x86 dependencies; container images support multi-arch.

1. Verify container image compatibility:
   ```bash
   # Check if current images support ARM64
   for DEPLOY in backend frontend worker-high worker-low executor payment-service; do
     IMAGE=$(kubectl get deployment ${DEPLOY} -n production -o jsonpath='{.spec.template.spec.containers[0].image}')
     echo "=== ${DEPLOY}: ${IMAGE} ==="
     docker manifest inspect ${IMAGE} 2>/dev/null | jq '.manifests[].platform' || echo "Cannot inspect manifest"
   done

   # If images are ECR-based, check for multi-arch manifests
   aws ecr describe-images \
     --repository-name cloudthinker/backend \
     --region us-east-1 \
     --query 'imageDetails[0].{Tags:imageTags,Arch:imageManifestMediaType,Size:imageSizeInBytes}'
   ```

2. Build and push ARM64 images (if not already multi-arch):
   ```bash
   # Enable Docker buildx for multi-arch builds
   docker buildx create --name multiarch --use

   # Build and push multi-arch image
   docker buildx build \
     --platform linux/amd64,linux/arm64 \
     --tag ${ECR_REGISTRY}/cloudthinker/backend:${IMAGE_TAG} \
     --push \
     ./backend/

   # Repeat for all services
   ```

3. Create Graviton node group and migrate workloads:
   ```bash
   # Create ARM64 node group
   aws eks create-nodegroup \
     --cluster-name ${CLUSTER_NAME} \
     --nodegroup-name cloudthinker-prod-graviton \
     --node-role ${NODE_ROLE_ARN} \
     --subnets ${SUBNET_IDS} \
     --instance-types m7g.xlarge \
     --ami-type AL2_ARM_64 \
     --scaling-config minSize=${MIN_SIZE},maxSize=${MAX_SIZE},desiredSize=${DESIRED_SIZE} \
     --labels "arch=arm64,nodegroup=graviton" \
     --region us-east-1

   # Add node affinity to deployments to schedule on Graviton nodes
   for DEPLOY in backend frontend worker-high worker-low executor payment-service; do
     kubectl patch deployment ${DEPLOY} -n production --type=strategic -p '{
       "spec": {
         "template": {
           "spec": {
             "affinity": {
               "nodeAffinity": {
                 "preferredDuringSchedulingIgnoredDuringExecution": [{
                   "weight": 100,
                   "preference": {
                     "matchExpressions": [{
                       "key": "kubernetes.io/arch",
                       "operator": "In",
                       "values": ["arm64"]
                     }]
                   }
                 }]
               }
             }
           }
         }
       }
     }'
   done

   # Trigger rolling restart to reschedule on Graviton nodes
   for DEPLOY in backend frontend worker-high worker-low executor payment-service; do
     kubectl rollout restart deployment/${DEPLOY} -n production
   done
   ```

### Scenario E: Optimize Pod Resource Requests (Kubernetes-Level Right-Sizing)

**Conditions**: Pods are requesting significantly more CPU/memory than they use; causes node over-provisioning.

1. Analyze actual vs requested resources:
   ```bash
   # Get resource usage vs requests for the last hour
   kubectl top pods -n production --containers | while read LINE; do
     POD=$(echo ${LINE} | awk '{print $1}')
     CONTAINER=$(echo ${LINE} | awk '{print $2}')
     ACTUAL_CPU=$(echo ${LINE} | awk '{print $3}')
     ACTUAL_MEM=$(echo ${LINE} | awk '{print $4}')

     REQUESTED_CPU=$(kubectl get pod ${POD} -n production -o jsonpath="{.spec.containers[?(@.name==\"${CONTAINER}\")].resources.requests.cpu}" 2>/dev/null)
     REQUESTED_MEM=$(kubectl get pod ${POD} -n production -o jsonpath="{.spec.containers[?(@.name==\"${CONTAINER}\")].resources.requests.memory}" 2>/dev/null)

     echo "${POD} | ${CONTAINER} | CPU: ${ACTUAL_CPU}/${REQUESTED_CPU} | MEM: ${ACTUAL_MEM}/${REQUESTED_MEM}"
   done
   ```

2. Update resource requests based on observed usage:
   ```bash
   # Use the P95 of actual usage + 20% buffer as the new request
   # Example: backend uses 200m CPU on average, P95 = 400m -> request 500m

   kubectl patch deployment backend -n production --type=strategic -p '{
     "spec": {
       "template": {
         "spec": {
           "containers": [{
             "name": "backend",
             "resources": {
               "requests": {
                 "cpu": "'${NEW_CPU_REQUEST}'",
                 "memory": "'${NEW_MEM_REQUEST}'"
               },
               "limits": {
                 "cpu": "'${NEW_CPU_LIMIT}'",
                 "memory": "'${NEW_MEM_LIMIT}'"
               }
             }
           }]
         }
       }
     }
   }'
   ```

3. Install Vertical Pod Autoscaler for continuous right-sizing:
   ```bash
   # Install VPA if not already present
   # See: https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler#installation
   git clone https://github.com/kubernetes/autoscaler.git /tmp/k8s-autoscaler
   /tmp/k8s-autoscaler/vertical-pod-autoscaler/hack/vpa-up.sh

   # Create VPA objects in recommendation mode (does not auto-apply)
   for DEPLOY in backend frontend worker-high worker-low executor payment-service; do
     kubectl apply -f - <<EOF
   apiVersion: autoscaling.k8s.io/v1
   kind: VerticalPodAutoscaler
   metadata:
     name: ${DEPLOY}-vpa
     namespace: production
   spec:
     targetRef:
       apiVersion: apps/v1
       kind: Deployment
       name: ${DEPLOY}
     updatePolicy:
       updateMode: "Off"
   EOF
   done
   ```

## Verification

After applying any right-sizing changes, monitor for at least 48 hours including a peak traffic period:

```bash
# Verify pods are running and healthy on new infrastructure
kubectl get pods -n production -o wide
kubectl get events -n production --sort-by='.lastTimestamp' | grep -E "(OOMKilled|Evicted|FailedScheduling)" | tail -10

# Verify no performance degradation
# Grafana: API Latency Dashboard
echo "Check Grafana: https://grafana.internal.cloudthinker.io/d/api-latency/api-latency-overview"
echo "Verify P99 latency < 200ms SLA target"

# Datadog: Service health
echo "Check Datadog: https://app.datadoghq.com/apm/services/backend/operations/http.request"

# Verify CPU/memory utilization on new instances is within healthy range (40-70%)
kubectl top nodes
kubectl top pods -n production --sort-by=cpu | head -10

# Verify cost savings in Cost Explorer (visible after 24-48 hours)
aws ce get-cost-forecast \
  --time-period Start=$(date -u +%Y-%m-%d),End=$(date -u -v+30d +%Y-%m-%d 2>/dev/null || date -u -d '+30 days' +%Y-%m-%d) \
  --granularity MONTHLY \
  --metric "UNBLENDED_COST" \
  --filter '{
    "Dimensions": {
      "Key": "SERVICE",
      "Values": ["Amazon Elastic Compute Cloud - Compute"]
    }
  }' \
  --region us-east-1

# Compare forecasted vs previous month spend
echo "Compare with previous month:"
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-30d +%Y-%m-%d 2>/dev/null || date -u -d '30 days ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics "UnblendedCost" \
  --filter '{
    "Dimensions": {
      "Key": "SERVICE",
      "Values": ["Amazon Elastic Compute Cloud - Compute"]
    }
  }' \
  --region us-east-1
```

Expected: All pods Running with 0 OOM/eviction events; P99 latency within SLA; node utilization in 40-70% range; cost forecast shows reduction.

## Rollback

If right-sizing causes performance degradation or pod scheduling failures:

### Rollback EKS Node Group

```bash
# Re-create the original node group
aws eks create-nodegroup \
  --cluster-name ${CLUSTER_NAME} \
  --nodegroup-name ${NODEGROUP_NAME}-rollback \
  --node-role ${NODE_ROLE_ARN} \
  --subnets ${SUBNET_IDS} \
  --instance-types ${ORIGINAL_INSTANCE_TYPE} \
  --scaling-config minSize=${MIN_SIZE},maxSize=${MAX_SIZE},desiredSize=${DESIRED_SIZE} \
  --region us-east-1

aws eks wait nodegroup-active \
  --cluster-name ${CLUSTER_NAME} \
  --nodegroup-name ${NODEGROUP_NAME}-rollback \
  --region us-east-1

# Cordon and drain the rightsized nodes
for NODE in $(kubectl get nodes -l eks.amazonaws.com/nodegroup=${NODEGROUP_NAME}-rightsized -o jsonpath='{.items[*].metadata.name}'); do
  kubectl cordon ${NODE}
  kubectl drain ${NODE} --ignore-daemonsets --delete-emptydir-data --grace-period=120 --timeout=300s
done

# Delete the rightsized node group
aws eks delete-nodegroup \
  --cluster-name ${CLUSTER_NAME} \
  --nodegroup-name ${NODEGROUP_NAME}-rightsized \
  --region us-east-1
```

### Rollback RDS

```bash
# Revert RDS instance class
aws rds modify-db-instance \
  --db-instance-identifier cloudthinker-prod-db \
  --db-instance-class ${ORIGINAL_RDS_CLASS} \
  --apply-immediately \
  --region us-east-1
```

### Rollback ElastiCache

```bash
# Revert ElastiCache node type
aws elasticache modify-replication-group \
  --replication-group-id cloudthinker-prod-redis \
  --cache-node-type ${ORIGINAL_CACHE_NODE_TYPE} \
  --apply-immediately \
  --region us-east-1
```

### Rollback Pod Resource Requests

```bash
# Revert deployment resource requests
kubectl rollout undo deployment/${DEPLOYMENT_NAME} -n production
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Performance degradation after right-sizing | @platform-team-lead |
| Pod scheduling failures (FailedScheduling) | @platform-team |
| RDS downtime during modification | @backend-team-lead + @incident-commander |
| Reserved Instance/Savings Plan conflict | @finops-team-lead |
| Cost increase instead of decrease | @finops-team-lead |
| Graviton compatibility issues | @platform-team + @backend-team |
| Customer-facing impact from changes | @incident-commander |

## Related Runbooks

- [Idle Resource Cleanup](../cloud-cost/idle-resource-cleanup.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)
- [HPA Max Replicas Reached](../kubernetes/hpa-max-replicas-reached.md)
- [Kubernetes Node Not Ready](../kubernetes/node-not-ready.md)
- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)
- [Application Memory Leak](../application/memory-leak-diagnosis.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-10 | @finops-team | Added Graviton migration scenario |
| 2025-10-05 | @finops-team | Added VPA installation guidance |
| 2025-07-20 | @finops-lead | Added ElastiCache right-sizing |
| 2025-04-15 | @finops-team | Added pod resource optimization scenario |
| 2025-01-30 | @finops-team | Initial version |
