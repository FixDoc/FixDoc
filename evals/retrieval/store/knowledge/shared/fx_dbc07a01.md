---
id: fx_dbc07a01
type: fix
title: Databricks jobs stuck pending waiting for cluster (core quota)
status: validated
occurrences: 2
created: '2026-09-15'
resource_type: databricks/jobs
---

## Symptom

Scheduled jobs sit pending because the job cluster never starts; workspace hit its vCPU quota.

## Root cause

Regional vCPU quota exhausted by idle interactive clusters.

## Fix

Terminate idle clusters or request a quota increase; add auto-termination.

## Verification

Job cluster provisions and runs leave pending.
