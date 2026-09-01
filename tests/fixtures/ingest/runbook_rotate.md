# Rotating AKS node certificates

## Summary

Cert expiry alerts fire about two weeks before AKS node certificates lapse.

## Fix

1. Run az aks rotate-certs on the affected cluster.
2. Wait for all nodepools to cycle; nodes drain one at a time.
3. Confirm every node reports Ready and workloads rescheduled.
