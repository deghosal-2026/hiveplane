# Debugging a Running Pod

## Detailed Pod Information

Use `kubectl describe pod` to get comprehensive details about a pod, including its state, conditions, events, container statuses, and resource allocations.

```
kubectl describe pod <pod-name>
```

## Examining Pod Logs

Retrieve logs from a running or previous container instance.

```
kubectl logs <pod-name>
```

For pods with multiple containers, specify the container:

```
kubectl logs <pod-name> -c <container-name>
```

Stream logs in real time with `-f` and include timestamps with `--timestamps`.

## Debugging with Container Exec

Execute commands inside a running container:

```
kubectl exec -it <pod-name> -- /bin/sh
```

This opens an interactive shell, allowing you to inspect files, check network connectivity, run diagnostics, and verify configuration.

## Debugging with Ephemeral Debug Containers

Add a temporary debug container to a running pod for troubleshooting without restarting:

```
kubectl debug <pod-name> -it --image=busybox
```

Ephemeral containers are not part of the original pod spec and are removed when the debug session ends. They share the pod's network namespace and volumes.

## Debugging Using a Copy of the Pod

Create a copy of the pod with modified settings for safe debugging:

```
kubectl debug <pod-name> --copy-to=<debug-pod-name> --set-image=<container>=<debug-image>
```

This is useful when you need to change environment variables, command arguments, or the container image while preserving the original pod configuration.

## Debugging via a Shell on the Node

Access the host node to debug pod issues from the node's perspective (requires node-level access):

```
ssh <node-ip>
```

Or use a privileged pod that mounts the host filesystem:

```
kubectl debug node/<node-name> -it --image=busybox
```

From the node, you can inspect container runtimes (`crictl`, `docker`), check kubelet logs, examine networking (`iptables`, `ip`), and review system resources.
