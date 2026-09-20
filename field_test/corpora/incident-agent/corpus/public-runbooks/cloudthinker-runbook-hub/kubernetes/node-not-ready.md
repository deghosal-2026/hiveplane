# Kubernetes Node Not Ready

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Service** | All (service-a, service-b, worker-a, worker-b) |
| **Owner** | @your-team |
| **Last Reviewed** | 2026-01-10 |
| **Alert** | `KubeNodeNotReady` |
| **Tags** | `kubernetes`, `eks`, `node`, `infrastructure`, `aws` |

## Summary

One or more Kubernetes nodes in the EKS cluster have entered a `NotReady` state, meaning the kubelet is no longer reporting healthy status to the control plane. This runbook covers diagnosis across multiple root causes -- disk pressure, memory pressure, PID pressure, network partition, kubelet failure, and EC2 instance issues -- along with drain/cordon procedures, workload rescheduling, and ASG node group management.

## Impact

- **Workload disruption**: Pods running on the NotReady node cannot receive traffic and are not rescheduled automatically until the node is marked `Unknown` (after `pod-eviction-timeout`, default 5 minutes).
- **Capacity reduction**: Available cluster capacity is reduced. If the cluster is already near capacity, pending pods cannot be scheduled.
- **User-facing**: If API or frontend pods were on the affected node, requests and web traffic experience errors or timeouts until pods are rescheduled to healthy nodes.
- **Data processing**: Worker pods on the affected node stop processing tasks. In-flight tasks may be lost if they were not using acknowledgment-based delivery.
- **Critical service risk**: If critical service pods were on the affected node, dependent processing halts.
- **Cascading risk**: If multiple nodes go NotReady simultaneously, cluster stability is compromised.

## Prerequisites

- `kubectl` access to the EKS cluster with `cluster-admin` or equivalent RBAC
- AWS IAM credentials with `ec2:Describe*`, `autoscaling:Describe*`, `autoscaling:SetDesiredCapacity` permissions
- SSH access to worker nodes (via SSM Session Manager or bastion host)
- Access to Grafana: `https://<your-grafana-url>/d/k8s-nodes/kubernetes-node-overview`
- Access to your monitoring tool (e.g., Datadog, Prometheus) for `KubeNodeNotReady` alerts
- Familiarity with EKS managed node groups and EC2 Auto Scaling Groups

## Triage & Diagnosis

### Step 1: Identify NotReady Nodes

```bash
# List all nodes and their status
kubectl get nodes -o wide
```

```bash
# Get detailed conditions for the NotReady node
kubectl describe node ${NODE_NAME} | grep -A 20 "Conditions:"
```

```bash
# Check node conditions in structured format
kubectl get node ${NODE_NAME} -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.reason}{"\t"}{.message}{"\n"}{end}'
```

### Step 2: Determine the Root Cause from Node Conditions

The node conditions table reveals the specific pressure type:

| Condition | Status | Meaning |
|-----------|--------|---------|
| `Ready` | `False` | Kubelet unhealthy or not communicating |
| `MemoryPressure` | `True` | Node running out of memory |
| `DiskPressure` | `True` | Node running out of disk space |
| `PIDPressure` | `True` | Too many processes on the node |
| `NetworkUnavailable` | `True` | Network plugin not configured |

```bash
# Check node events for recent issues
kubectl get events --field-selector involvedObject.name=${NODE_NAME} --sort-by='.lastTimestamp' | tail -20
```

```bash
# Check kubelet status via SSM (no SSH key needed)
aws ssm start-session --target ${INSTANCE_ID} --region ${AWS_REGION}
# Then run inside the session:
# systemctl status kubelet
# journalctl -u kubelet --since "30 minutes ago" --no-pager | tail -50
```

### Step 3: Check EC2 Instance Health

```bash
# Get the EC2 instance ID for the node
INSTANCE_ID=$(kubectl get node ${NODE_NAME} -o jsonpath='{.spec.providerID}' | cut -d '/' -f5)
echo "Instance ID: ${INSTANCE_ID}"
```

```bash
# Check EC2 instance status
aws ec2 describe-instance-status \
  --instance-ids ${INSTANCE_ID} \
  --region ${AWS_REGION} \
  --output table
```

```bash
# Check system and instance status checks
aws ec2 describe-instance-status \
  --instance-ids ${INSTANCE_ID} \
  --query 'InstanceStatuses[0].[SystemStatus.Status, InstanceStatus.Status]' \
  --output text \
  --region ${AWS_REGION}
```

### Step 4: Check Workloads on the Affected Node

```bash
# List all pods on the NotReady node
kubectl get pods --all-namespaces --field-selector spec.nodeName=${NODE_NAME} -o wide
```

```bash
# Check for critical application pods on the node
kubectl get pods -n ${NAMESPACE} --field-selector spec.nodeName=${NODE_NAME} -o wide
```

```bash
# Count pods by status on the affected node
kubectl get pods --all-namespaces --field-selector spec.nodeName=${NODE_NAME} \
  -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' | sort | uniq -c | sort -rn
```

### Step 5: Check Cluster-Wide Health

```bash
# Are other nodes also showing issues?
kubectl get nodes --no-headers | awk '{print $2}' | sort | uniq -c
```

```bash
# Check pending pods that cannot be scheduled
kubectl get pods --all-namespaces --field-selector status.phase=Pending -o wide
```

```bash
# Check node resource allocation
kubectl top nodes
```

```bash
# Check the node group / ASG status
aws eks describe-nodegroup \
  --cluster-name ${CLUSTER_NAME} \
  --nodegroup-name ${NODEGROUP_NAME} \
  --query 'nodegroup.{status:status,desiredSize:scalingConfig.desiredSize,currentSize:scalingConfig.desiredSize,minSize:scalingConfig.minSize,maxSize:scalingConfig.maxSize}' \
  --output table \
  --region ${AWS_REGION}
```

## Mitigation Steps

### Scenario A: Disk Pressure

The node's root filesystem or container runtime storage is full, preventing kubelet from functioning.

1. Confirm disk pressure:

   ```bash
   # Via SSM session on the node
   aws ssm start-session --target ${INSTANCE_ID} --region ${AWS_REGION}
   ```

   ```bash
   # Inside SSM session:
   df -h /
   df -h /var/lib/docker
   df -h /var/lib/containerd
   du -sh /var/log/* | sort -rh | head -10
   du -sh /var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/* | sort -rh | head -5
   ```

2. Clean up disk space:

   ```bash
   # Inside SSM session:
   # Remove old container images
   crictl rmi --prune

   # Clean up dead containers
   crictl rm $(crictl ps -a --state exited -q)

   # Truncate large log files
   find /var/log -name "*.log" -size +100M -exec truncate -s 0 {} \;

   # Remove old journal logs
   journalctl --vacuum-size=500M
   ```

3. If disk cleanup is insufficient, drain and replace the node:

   ```bash
   # Cordon the node to prevent new scheduling
   kubectl cordon ${NODE_NAME}

   # Drain the node (evict all pods)
   kubectl drain ${NODE_NAME} \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --grace-period=60 \
     --timeout=300s
   ```

4. Terminate the instance and let the ASG replace it:

   ```bash
   aws ec2 terminate-instances \
     --instance-ids ${INSTANCE_ID} \
     --region ${AWS_REGION}
   ```

### Scenario B: Memory Pressure

The node is running out of allocatable memory, triggering OOM kills and kubelet instability.

1. Check memory usage on the node:

   ```bash
   # Via SSM session:
   free -h
   cat /proc/meminfo | head -10
   ```

   ```bash
   # Check which pods are consuming the most memory on this node
   kubectl top pods --all-namespaces --sort-by=memory --no-headers | head -20
   ```

2. Identify memory-hungry pods on the node:

   ```bash
   kubectl get pods -n ${NAMESPACE} --field-selector spec.nodeName=${NODE_NAME} \
     -o custom-columns='NAME:.metadata.name,MEM_REQUEST:.spec.containers[0].resources.requests.memory,MEM_LIMIT:.spec.containers[0].resources.limits.memory'
   ```

3. Evict the largest non-critical pods:

   ```bash
   # Delete the most memory-hungry non-critical pod to relieve pressure
   kubectl delete pod ${HIGH_MEMORY_POD} -n ${NAMESPACE} --grace-period=30
   ```

4. If the node does not recover, drain and replace:

   ```bash
   kubectl cordon ${NODE_NAME}
   kubectl drain ${NODE_NAME} \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --grace-period=60 \
     --timeout=300s

   aws ec2 terminate-instances \
     --instance-ids ${INSTANCE_ID} \
     --region ${AWS_REGION}
   ```

### Scenario C: PID Pressure

The node has too many processes, often caused by fork bombs, excessive thread creation, or a pod spawning unbounded child processes.

1. Check PID usage on the node:

   ```bash
   # Via SSM session:
   # Total PIDs vs limit
   echo "Current PIDs: $(ls /proc | grep -c '^[0-9]')"
   cat /proc/sys/kernel/pid_max

   # Top PID consumers by cgroup (identifies pod)
   # cgroup v1
   for cg in /sys/fs/cgroup/pids/kubepods/burstable/pod*/; do
     pids=$(cat "${cg}pids.current" 2>/dev/null)
     [ -n "$pids" ] && echo "${pids} ${cg}"
   done | sort -rn | head -10
   # cgroup v2 (EKS 1.28+)
   for cg in /sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod*.slice/; do
     pids=$(cat "${cg}pids.current" 2>/dev/null)
     [ -n "$pids" ] && echo "${pids} ${cg}"
   done | sort -rn | head -10
   ```

2. Identify the offending pod:

   ```bash
   # Map cgroup path to pod
   kubectl get pods -n ${NAMESPACE} --field-selector spec.nodeName=${NODE_NAME} \
     -o custom-columns='NAME:.metadata.name,UID:.metadata.uid' | grep ${POD_UID_PREFIX}
   ```

3. Kill the offending pod:

   ```bash
   kubectl delete pod ${OFFENDING_POD} -n ${NAMESPACE} --grace-period=0 --force
   ```

4. Add PID limits to prevent recurrence:

   ```bash
   # Verify PodPidsLimit is set in kubelet config
   # Via SSM session:
   cat /etc/kubernetes/kubelet/kubelet-config.json | grep -i pid
   # Should show: "podPidsLimit": 4096
   ```

### Scenario D: Kubelet Failure

The kubelet process has crashed, is stuck, or cannot communicate with the API server.

1. Check kubelet status:

   ```bash
   # Via SSM session:
   systemctl status kubelet
   journalctl -u kubelet --since "15 minutes ago" --no-pager | tail -100
   ```

2. Common kubelet errors and fixes:

   ```bash
   # If kubelet is in CrashLoopBackOff:
   systemctl restart kubelet
   sleep 30
   systemctl status kubelet
   ```

   ```bash
   # If kubelet cannot reach the API server (certificate issues):
   journalctl -u kubelet --since "5 minutes ago" | grep -i "certificate\|tls\|x509"

   # If certificate expired, rotate:
   # This is managed by EKS -- check EKS cluster status
   aws eks describe-cluster \
     --name ${CLUSTER_NAME} \
     --query 'cluster.{status:status,certificateAuthority:certificateAuthority.data}' \
     --region ${AWS_REGION}
   ```

   ```bash
   # If kubelet is OOM killed:
   dmesg | grep -i "oom\|killed" | tail -20
   # Increase kubelet memory reservation in node group launch template
   ```

3. If kubelet cannot be recovered, drain and replace:

   ```bash
   # Since the node is NotReady, drain may hang. Use --force:
   kubectl drain ${NODE_NAME} \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --force \
     --grace-period=30 \
     --timeout=120s

   aws ec2 terminate-instances \
     --instance-ids ${INSTANCE_ID} \
     --region ${AWS_REGION}
   ```

### Scenario E: Network Partition (Node Cannot Reach API Server)

The node is running but cannot communicate with the Kubernetes API server due to network issues.

1. Check network connectivity from the node:

   ```bash
   # Via SSM session:
   # Check API server connectivity
   curl -sk https://${API_SERVER_ENDPOINT}/healthz
   # Should return "ok"

   # Check DNS resolution
   nslookup ${API_SERVER_ENDPOINT}

   # Check VPC network
   ip addr show
   ip route show
   ```

2. Check security groups and NACLs:

   ```bash
   # Get the node's security groups
   aws ec2 describe-instances \
     --instance-ids ${INSTANCE_ID} \
     --query 'Reservations[0].Instances[0].SecurityGroups' \
     --output table \
     --region ${AWS_REGION}
   ```

   ```bash
   # Check if security group rules allow traffic to the EKS API server (port 443)
   aws ec2 describe-security-groups \
     --group-ids ${NODE_SECURITY_GROUP_ID} \
     --query 'SecurityGroups[0].IpPermissionsEgress' \
     --output table \
     --region ${AWS_REGION}
   ```

3. Check the EKS cluster API server:

   ```bash
   # Verify EKS API server is healthy
   aws eks describe-cluster \
     --name ${CLUSTER_NAME} \
     --query 'cluster.status' \
     --output text \
     --region ${AWS_REGION}
   ```

4. If network issue is isolated to the node, replace it:

   ```bash
   kubectl cordon ${NODE_NAME}
   kubectl drain ${NODE_NAME} --ignore-daemonsets --delete-emptydir-data --force --timeout=120s
   aws ec2 terminate-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}
   ```

### Scenario F: EC2 Instance Failure (Hardware/Host Issue)

AWS reports instance or system status check failures.

1. Check instance status:

   ```bash
   aws ec2 describe-instance-status \
     --instance-ids ${INSTANCE_ID} \
     --query 'InstanceStatuses[0].{InstanceStatus:InstanceStatus.Status,SystemStatus:SystemStatus.Status,Events:Events}' \
     --output table \
     --region ${AWS_REGION}
   ```

2. If system status check failed (AWS hardware issue):

   > **WARNING**: If using EKS managed node groups, do not manually stop/start EC2 instances. Instead, use `aws eks update-nodegroup-config` or terminate the instance and let the ASG replace it.

   ```bash
   # Stop and start the instance (migrates to new hardware)
   # NOTE: This only works for EBS-backed instances and self-managed node groups
   aws ec2 stop-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}
   aws ec2 wait instance-stopped --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}
   aws ec2 start-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}
   ```

3. If stop/start does not resolve, terminate and let ASG replace:

   ```bash
   kubectl cordon ${NODE_NAME}
   kubectl drain ${NODE_NAME} --ignore-daemonsets --delete-emptydir-data --force --timeout=120s
   aws ec2 terminate-instances --instance-ids ${INSTANCE_ID} --region ${AWS_REGION}
   ```

### Post-Mitigation: Verify ASG Replacement

After terminating a node, ensure the ASG launches a replacement.

**Before terminating**, look up the ASG name (this lookup will fail after the instance is terminated):

```bash
# Look up the ASG BEFORE terminating the instance
ASG_NAME=$(aws autoscaling describe-auto-scaling-instances \
  --instance-ids ${INSTANCE_ID} \
  --query 'AutoScalingInstances[0].AutoScalingGroupName' \
  --output text \
  --region ${AWS_REGION})
echo "ASG: ${ASG_NAME}"
```

After termination, verify the ASG replaces the node:

```bash
# Check ASG desired vs actual count
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names ${ASG_NAME} \
  --query 'AutoScalingGroups[0].{Desired:DesiredCapacity,Min:MinSize,Max:MaxSize,Instances:Instances[*].{Id:InstanceId,State:LifecycleState}}' \
  --output table \
  --region ${AWS_REGION}
```

```bash
# If desired capacity dropped, manually set it back
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name ${ASG_NAME} \
  --desired-capacity ${DESIRED_NODE_COUNT} \
  --region ${AWS_REGION}
```

```bash
# Watch for the new node to join the cluster
watch -n 10 'kubectl get nodes -o wide'
```

## Verification

```bash
# All nodes should be Ready
kubectl get nodes -o wide
# Expected: all nodes showing "Ready" status
```

```bash
# No pods in Pending state
kubectl get pods -n ${NAMESPACE} --field-selector status.phase=Pending
# Expected: No resources found
```

```bash
# All application deployments healthy
kubectl get deployments -n ${NAMESPACE}
# Expected: all deployments showing READY = DESIRED
```

```bash
# Check that evicted pods have been rescheduled
kubectl get pods -n ${NAMESPACE} -o wide | grep -E 'service-a|service-b|worker-a|worker-b'
```

```bash
# Verify node resource availability
kubectl top nodes
```

```bash
# Verify monitoring alert recovered
# Check your monitoring tool (e.g., Datadog, Prometheus) for KubeNodeNotReady alert status
```

**Expected**: All nodes in `Ready` state, all deployments at desired replica count, no pending pods, monitoring alerts in OK state.

## Rollback

If a node was cordoned but the issue was resolved without replacement:

```bash
# Uncordon the node to allow scheduling again
kubectl uncordon ${NODE_NAME}
```

If HPA or deployments were scaled down during mitigation:

```bash
# Restore deployment replicas
kubectl scale deployment/service-a -n ${NAMESPACE} --replicas=${ORIGINAL_SERVICE_A_REPLICAS}
kubectl scale deployment/worker-a -n ${NAMESPACE} --replicas=${ORIGINAL_WORKER_A_REPLICAS}
kubectl scale deployment/worker-b -n ${NAMESPACE} --replicas=${ORIGINAL_WORKER_B_REPLICAS}
```

If ASG desired capacity was manually changed:

```bash
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name ${ASG_NAME} \
  --desired-capacity ${ORIGINAL_DESIRED_CAPACITY} \
  --region ${AWS_REGION}
```

## Escalation

| Condition | Contact |
|-----------|---------|
| Single node NotReady > 10 min | @your-team-lead |
| Multiple nodes NotReady simultaneously | @incident-commander |
| All nodes in a node group NotReady | @incident-commander + @cloud-support |
| EKS API server unreachable | @your-team-lead + @cloud-support (Severity 1) |
| Critical service pods affected | @service-owner + @incident-commander |
| ASG not replacing terminated nodes | @your-team-lead |
| Suspected cloud infrastructure issue | @cloud-support (Severity 2) |

## Related Runbooks

- [Pod CrashLoopBackOff](pod-crashloopbackoff.md)
- [HPA Max Replicas Reached](hpa-max-replicas-reached.md)
- [PVC Pending / Storage Full](pvc-pending-storage-full.md)
- [Application Memory Leak](../application/memory-leak-diagnosis.md)
- [DNS Resolution Failure](../networking/dns-resolution-failure.md)
- [PostgreSQL Connection Pool Exhaustion](../database/postgres-connection-pool-exhaustion.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-01-10 | @your-team | Added Scenario F (EC2 instance failure) and SSM-based diagnostics |
| 2025-09-18 | @your-team | Added PID pressure scenario and cgroup-based diagnostics |
| 2025-06-25 | @your-team | Updated drain commands for EKS 1.28+ compatibility |
| 2025-04-02 | @your-team | Added ASG replacement verification steps |
| 2025-01-20 | @your-team | Initial version |
