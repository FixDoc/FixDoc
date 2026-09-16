---
id: fx_d151b0bb
type: fix
title: Ingress 502s after TLS certificate rotation
status: validated
occurrences: 2
created: '2026-09-15'
resource_type: kubernetes/aks
---

## Symptom

nginx ingress returns 502 for all routes right after rotating TLS certs.

## Root cause

Ingress pods held the old secret mount; no rollout after secret update.

## Fix

kubectl rollout restart the ingress deployment after cert updates; automate via reloader.

## Verification

502s stop; cert serial matches the new cert.
