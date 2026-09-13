# AKS pods stuck Pending after nodepool scale-up

## Summary

Pods stayed Pending after adding a nodepool. The scheduler reported
"0/14 nodes are available", blocking the application rollout.

## Root cause

Azure CNI reserves max_pods IPs for each node. The existing subnet had
insufficient free addresses for the new pool.

## Resolution

Add a dedicated subnet for the nodepool with enough addresses for the
planned node count and pod capacity.

## Verification

Pods scheduled successfully and availableIpAddressCount remained positive.
