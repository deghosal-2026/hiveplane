# PVC Pending / Storage Full

## Metadata

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Service** | kubernetes (EKS), EBS CSI |
| **Owner** | @platform-team |
| **Last Reviewed** | 2025-11-10 |
| **Alert** | `PVCPending` / `PersistentVolumeUsageHigh` |
| **Tags** | `kubernetes`, `storage`, `pvc`, `ebs`, `disk` |

## Summary

A PersistentVolumeClaim (PVC) is stuck in `Pending` state and cannot bind to a PersistentVolume (PV), or an existing PV is running out of disk space. This blocks pod scheduling for stateful workloads including PostgreSQL data directories, Qdrant vector storage, and application log volumes.

## Impact

- Pods that depend on the PVC cannot start, remaining in `Pending` or `ContainerCreating` state.
- If Qdrant storage is full, vector search operations fail and the AI-powered runbook agent cannot function.
- Database backup volumes that are full cause backup jobs to fail silently.
- Log aggregation volumes at capacity cause log loss, impacting incident investigation.
- New deployments that require storage provisioning will fail to roll out.

## Prerequisites

- `kubectl` configured for the `production` EKS cluster
- AWS CLI with `ec2:DescribeVolumes`, `ec2:ModifyVolume`, `ec2:CreateSnapshot` permissions
- Familiarity with Kubernetes storage concepts (PVC, PV, StorageClass, CSI)
- Access to Grafana dashboard: `https://grafana.internal.cloudthinker.io/d/k8s-storage/kubernetes-persistent-storage`
- The EBS CSI driver must be installed (`aws-ebs-csi-driver`)

## Triage & Diagnosis

### Step 1: Identify problematic PVCs

```bash
# List all PVCs and their status
kubectl get pvc -n production -o wide

# Find PVCs in Pending state
kubectl get pvc -n production --field-selector status.phase=Pending

# Get detailed events for a pending PVC
kubectl describe pvc ${PVC_NAME} -n production
```

### Step 2: Check PV binding status

```bash
# List PVs and their binding status
kubectl get pv -o wide | grep -E "production|Available|Released"

# Check if PV exists but is stuck in Released state
kubectl get pv -o jsonpath='{range .items[?(@.status.phase=="Released")]}{.metadata.name}{"\t"}{.spec.claimRef.name}{"\n"}{end}'
```

### Step 3: Check StorageClass configuration

```bash
# List available StorageClasses
kubectl get storageclass

# Describe the default StorageClass
kubectl describe storageclass ${STORAGE_CLASS_NAME}
```

### Step 4: Check EBS CSI driver health

```bash
# Check if EBS CSI driver pods are running
kubectl get pods -n kube-system -l app=ebs-csi-controller
kubectl get pods -n kube-system -l app=ebs-csi-node

# Check CSI driver logs for errors
kubectl logs -n kube-system -l app=ebs-csi-controller -c csi-provisioner --tail=50
```

### Step 5: Check disk usage on existing PVs

```bash
# Check disk usage inside pods using the volume
kubectl exec -n production ${POD_NAME} -- df -h

# Check disk usage across all pods with PVCs
kubectl get pods -n production -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | while read pod; do
  echo "=== $pod ==="
  kubectl exec -n production "$pod" -- df -h 2>/dev/null | grep -E "/dev/|Filesystem" || echo "  (no exec access)"
done
```

### Step 6: Check the underlying EBS volumes in AWS

```bash
# List EBS volumes tagged for the EKS cluster
aws ec2 describe-volumes \
  --filters "Name=tag:kubernetes.io/cluster/cloudthinker-production,Values=owned" \
  --query "Volumes[].[VolumeId,Size,State,AvailabilityZone,Attachments[0].InstanceId]" \
  --output table

# Check volume modification status (if resize is in progress)
aws ec2 describe-volumes-modifications \
  --filters "Name=volume-id,Values=${VOLUME_ID}" \
  --output table
```

## Mitigation Steps

### Scenario A: PVC pending due to no available PV (dynamic provisioning failure)

The StorageClass provisioner failed to create a new EBS volume.

1. Check the provisioner events:
   ```bash
   kubectl get events -n production --field-selector reason=ProvisioningFailed --sort-by='.lastTimestamp'
   ```

2. Verify the EBS CSI driver has proper IAM permissions:
   ```bash
   # Check the service account annotation
   kubectl get sa ebs-csi-controller-sa -n kube-system -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'

   # Verify the IAM role has the required policies
   aws iam get-role-policy --role-name ${EBS_CSI_ROLE_NAME} --policy-name ${POLICY_NAME}
   ```

3. Check if the AZ has capacity (rare but possible):
   ```bash
   # Check which AZ the requesting node is in
   kubectl get node ${NODE_NAME} -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}'
   ```

4. If the CSI driver is unhealthy, restart it:
   ```bash
   kubectl rollout restart deployment/ebs-csi-controller -n kube-system
   kubectl rollout status deployment/ebs-csi-controller -n kube-system --timeout=120s
   ```

### Scenario B: PV stuck in Released state

A previously bound PV was not properly reclaimed.

1. Check the reclaim policy:
   ```bash
   kubectl get pv ${PV_NAME} -o jsonpath='{.spec.persistentVolumeReclaimPolicy}'
   ```

2. Remove the stale claim reference to make the PV available:
   ```bash
   # WARNING: This makes the PV available for binding to a new PVC. Data remains on the volume.
   kubectl patch pv ${PV_NAME} -p '{"spec":{"claimRef": null}}'
   ```

3. Verify the PV transitions to Available:
   ```bash
   kubectl get pv ${PV_NAME}
   ```

### Scenario C: Existing volume is full -- expand the EBS volume

The PV is bound and in use, but the filesystem is at or near 100%.

1. Check current PVC size and usage:
   ```bash
   kubectl get pvc ${PVC_NAME} -n production -o jsonpath='{.spec.resources.requests.storage}'
   kubectl exec -n production ${POD_NAME} -- df -h ${MOUNT_PATH}
   ```

2. Verify the StorageClass allows expansion:
   ```bash
   kubectl get storageclass ${STORAGE_CLASS_NAME} -o jsonpath='{.allowVolumeExpansion}'
   ```

3. Expand the PVC (Kubernetes handles the EBS resize):
   ```bash
   kubectl patch pvc ${PVC_NAME} -n production -p '{"spec":{"resources":{"requests":{"storage":"'${NEW_SIZE}'"}}}}'
   ```

4. Monitor the resize operation:
   ```bash
   # Check PVC conditions
   kubectl get pvc ${PVC_NAME} -n production -o jsonpath='{.status.conditions[*].message}'

   # Check the EBS volume modification status
   aws ec2 describe-volumes-modifications \
     --filters "Name=volume-id,Values=${VOLUME_ID}" \
     --query "VolumesModifications[].[VolumeId,ModificationState,TargetSize,Progress]" \
     --output table
   ```

5. If filesystem resize requires a pod restart:
   ```bash
   # Some CSI drivers require the pod to be restarted for fs resize
   kubectl delete pod ${POD_NAME} -n production
   # The pod will be recreated by its controller (Deployment/StatefulSet)
   ```

### Scenario D: Emergency disk space cleanup

Volume is critically full and needs immediate space recovery.

1. Identify large files inside the pod:
   ```bash
   kubectl exec -n production ${POD_NAME} -- du -sh ${MOUNT_PATH}/* | sort -rh | head -20
   ```

2. Clean up known safe targets:
   ```bash
   # Remove old log files (if logs are mounted on the PV)
   kubectl exec -n production ${POD_NAME} -- find ${MOUNT_PATH}/logs -name "*.log" -mtime +7 -delete

   # Remove temp files
   kubectl exec -n production ${POD_NAME} -- find ${MOUNT_PATH}/tmp -type f -mtime +1 -delete

   # Truncate a large log file without deleting it (preserves file handle)
   kubectl exec -n production ${POD_NAME} -- truncate -s 0 ${MOUNT_PATH}/logs/app.log
   ```

3. Verify space was recovered:
   ```bash
   kubectl exec -n production ${POD_NAME} -- df -h ${MOUNT_PATH}
   ```

### Scenario E: AZ mismatch between PV and node

EBS volumes are AZ-bound. If a pod is scheduled on a node in a different AZ, the PVC cannot bind.

1. Check the PV's AZ:
   ```bash
   kubectl get pv ${PV_NAME} -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[?(@.key=="topology.kubernetes.io/zone")].values[0]}'
   ```

2. Check available nodes by AZ:
   ```bash
   kubectl get nodes -o custom-columns=NAME:.metadata.name,ZONE:.metadata.labels.topology\\.kubernetes\\.io/zone,STATUS:.status.conditions[-1].type
   ```

3. Add a node affinity or topology constraint to the workload to ensure it schedules in the correct AZ, or migrate the volume:
   ```bash
   # Create a snapshot of the volume
   aws ec2 create-snapshot --volume-id ${VOLUME_ID} --description "AZ migration for ${PVC_NAME}"

   # Create a new volume in the correct AZ from the snapshot
   aws ec2 create-volume \
     --snapshot-id ${SNAPSHOT_ID} \
     --availability-zone ${TARGET_AZ} \
     --volume-type gp3 \
     --size ${SIZE_GB} \
     --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=${PVC_NAME}-migrated}]"
   ```

## Verification

```bash
# Confirm PVC is Bound
kubectl get pvc ${PVC_NAME} -n production

# Confirm pods using the PVC are Running
kubectl get pods -n production -l app=${APP_LABEL}

# Confirm disk usage is healthy (below 80%)
kubectl exec -n production ${POD_NAME} -- df -h ${MOUNT_PATH}

# Check Datadog monitors have cleared
# https://app.datadoghq.com/monitors/manage?q=PVCPending%20OR%20PersistentVolumeUsageHigh
```

Expected: PVC in `Bound` state, associated pods in `Running` state, disk usage below 80%.

## Rollback

If a PVC expansion caused issues:

- EBS volume expansion cannot be reversed. If the filesystem is corrupted, restore from snapshot:
  ```bash
  # List recent snapshots
  aws ec2 describe-snapshots --owner-ids self \
    --filters "Name=volume-id,Values=${VOLUME_ID}" \
    --query "Snapshots | sort_by(@, &StartTime) | [-3:].[SnapshotId,StartTime,VolumeSize]" \
    --output table
  ```

If a PV claim reference was patched incorrectly:

```bash
# Re-bind the PV to its original PVC
kubectl patch pv ${PV_NAME} -p '{"spec":{"claimRef":{"name":"'${PVC_NAME}'","namespace":"production"}}}'
```

If emergency cleanup deleted needed files, restore from backup:

```bash
# Check the most recent Velero backup (if configured)
velero backup get --selector app=${APP_LABEL}
velero restore create --from-backup ${BACKUP_NAME}
```

## Escalation

| Condition | Contact |
|-----------|---------|
| PVC pending > 15 min, blocking deployment | @platform-team-lead |
| Qdrant or PostgreSQL volume full | @platform-team-lead + @backend-team-lead |
| EBS CSI driver not functioning | @platform-team (cluster admin) |
| AWS EBS service issue suspected | Open AWS Support case (Severity: High) |
| Data loss from volume issue | @vp-engineering + @incident-commander |

## Related Runbooks

- [PostgreSQL High CPU Usage](../database/postgres-high-cpu.md)
- [Redis Memory Pressure / OOM](../database/redis-memory-pressure.md)
- [Celery Worker Queue Backlog](../application/celery-worker-queue-backlog.md)
- [Unexpected Cloud Spend Spike](../cloud-cost/unexpected-spend-spike.md)
- [Kubernetes Node Not Ready](node-not-ready.md)
- [Pod CrashLoopBackOff](pod-crashloopbackoff.md)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-11-10 | @kzhang | Added AZ mismatch scenario |
| 2025-08-22 | @mpark | Added emergency cleanup steps |
| 2025-05-15 | @platform-bot | Initial version |
