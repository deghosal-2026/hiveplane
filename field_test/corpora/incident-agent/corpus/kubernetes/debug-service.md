# Debugging Kubernetes Services

## Services Missing Endpoints

A Service without endpoints cannot route traffic to pods. This is most often caused by label selector mismatches.

Check the Service selector:

```
kubectl describe service <service-name>
```

Compare it against the labels on your pods:

```
kubectl get pods --show-labels
```

## Verify Endpoints

List the current endpoints for a Service to confirm pods are correctly registered:

```
kubectl get endpoints <service-name>
```

If endpoints are empty, ensure:
- Pods exist that match the Service's label selector
- Pods are in the `Running` state
- The `targetPort` in the Service matches the `containerPort` in the pod spec

## Service DNS Resolution

Within a cluster, Services are resolvable via DNS. Debug DNS issues with:

```
kubectl run dns-test --image=busybox -it --rm -- nslookup <service-name>
```

Common DNS problems:
- CoreDNS or kube-dns is not running
- Pod DNS policy is set to `None` or `Default` without proper nameserver configuration
- Service is in a different namespace (use `<service>.<namespace>.svc.cluster.local`)

## Network Traffic Not Being Forwarded

If endpoints exist but traffic does not reach pods, the issue may be at the data plane level.

- **kube-proxy**: Ensure kube-proxy is running on nodes. Check its mode (iptables, IPVS, or userspace).
- **iptables rules**: Inspect NAT rules for the Service:
  ```
  iptables -t nat -L -n | grep <service-name>
  ```
- **CNI plugin**: Verify the container network interface (e.g., Calico, Cilium, Flannel) is operating correctly.
- **NodePort access**: For NodePort Services, verify firewall rules allow traffic on the node port.

## Service Port Configuration Issues

Misconfigured ports are a common source of Service problems.

- **`port`**: The port the Service exposes (cluster-internal or external).
- **`targetPort`**: The port on the pod containers that receives traffic. If omitted, defaults to the value of `port`.
- **`nodePort`**: (For NodePort type) the static port on each node. Must be in the range 30000-32767 unless configured otherwise.

Check for mismatches with:

```
kubectl get endpoints <service-name> -o yaml
```

If the `endpoints` list shows the wrong port, the Service's `targetPort` does not match the pod's `containerPort`.
