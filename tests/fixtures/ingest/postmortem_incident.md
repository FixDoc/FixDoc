# AKS pods stuck Pending after nodepool scale-up

## Summary

After scaling the user nodepool from 8 to 14 nodes, application pods sat in
Pending for 40 minutes. The scheduler reported "0/14 nodes are available"
although every node showed Ready.

## Root Cause

Azure CNI reserves max_pods IP addresses per node at creation time. The
nodepool subnet had no free addresses left, so kubelet could not allocate
pod IPs on the new nodes.

## Fix

Checked availableIpAddressCount on the nodepool subnet with az network vnet
subnet show. It was zero. Moved the new nodepool to a dedicated subnet and
lowered max_pods from 110 to 60 on future pools.

## Verification

Pods scheduled within a minute of the subnet change; availableIpAddressCount
stayed positive through the next two scale events.
