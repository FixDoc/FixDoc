# Rotate AKS certificates

## When to use

Cluster certificates are approaching their expiry date.

## Steps

1. Schedule a maintenance window.
2. Execute `az aks rotate-certs` for the affected cluster.
3. Refresh the local kubeconfig.
