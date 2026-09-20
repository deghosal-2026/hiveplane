# Debugging Kubernetes Pods

## Check Pod State and Events

Use `kubectl describe pod <name>` to view detailed pod state, conditions, and recent events.

```
kubectl describe pod <pod-name>
```

## Pod Stuck in Pending

A pod stuck in `Pending` means the scheduler cannot place it on a node. Common causes:

- **Insufficient resources**: The cluster lacks enough CPU, memory, or ephemeral storage to satisfy the pod's requests.
- **Unschedulable node**: No node matches the pod's node selector, affinity rules, or tolerations.
- **PersistentVolumeClaim issues**: The PVC required by the pod is not bound or unavailable.

Check scheduler events with `kubectl describe pod` and examine node resources with `kubectl describe node`.

## Pod Stuck in Waiting

A pod stuck in `Waiting` indicates the container runtime cannot start the container. Common causes:

- **Image pull issues**: Wrong image name, missing tag, registry authentication failures, or rate limiting.
- **Volume mount problems**: ConfigMaps, Secrets, or PersistentVolumeClaims not available or misconfigured.
- **Init container failures**: An init container has not completed successfully.

Check the specific reason in `kubectl describe pod` under container status.

## Pod Stuck in Terminating

A pod stuck in `Terminating` indicates the pod cannot be gracefully shut down. Common causes:

- **Finalizer issues**: A finalizer is blocking deletion. Remove the finalizer from the pod spec or resolve the underlying resource.
- **Node problems**: The node running the pod is unreachable or has failed.
- **Process not responding**: The container's main process ignores SIGTERM and does not exit within the grace period.

Force delete a stuck pod with:

```
kubectl delete pod <pod-name> --force --grace-period=0
```

## Pod Crashing

A pod that repeatedly restarts indicates a crash loop. Debugging steps:

- **View logs from the previous (crashed) container**:
  ```
  kubectl logs <pod-name> --previous
  ```
- **Check liveness probes**: A failing liveness probe causes the kubelet to restart the container. Verify the probe path, port, and initial delay.
- **Resource limits**: Memory or CPU limits that are too low can cause OOM kills. Check `kubectl describe pod` for OOMKilled status.
- **Application errors**: Examine logs after the crash for stack traces or error messages.

## Full Pod Spec

Retrieve the full YAML representation of a pod:

```
kubectl get pod <pod-name> -o yaml
```

This exposes the complete pod definition, including status, conditions, container specs, volumes, and annotations.

## Ephemeral Debug Containers

Debug a running pod by adding an ephemeral container without restarting the pod:

```
kubectl debug <pod-name> -it --image=busybox
```

Ephemeral containers share the same pod network, IPC, and process namespace (if enabled), allowing you to inspect the pod environment.

## Copying a Pod for Debugging

Clone an existing pod with a modified configuration for debugging:

```
kubectl debug <pod-name> --copy-to=<new-name> --set-image=...
```

The copied pod replicates the original spec but allows you to override images, commands, or other fields to diagnose issues without affecting the production pod.
