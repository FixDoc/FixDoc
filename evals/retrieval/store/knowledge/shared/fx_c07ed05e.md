---
id: fx_c07ed05e
type: fix
title: Cluster DNS NXDOMAIN after CoreDNS ConfigMap edit
status: validated
occurrences: 3
created: '2026-09-15'
resource_type: kubernetes/aks
---

## Symptom

All service-to-service calls fail with NXDOMAIN after a CoreDNS config change; nslookup kubernetes.default fails.

## Root cause

Edited Corefile dropped the kubernetes plugin stanza, so cluster.local is not served.

## Fix

Roll back the ConfigMap from git; restart CoreDNS with maxUnavailable 1.

## Verification

nslookup succeeds from three namespaces.
