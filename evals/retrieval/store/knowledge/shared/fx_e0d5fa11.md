---
id: fx_e0d5fa11
type: fix
title: 'Postgres disk full: stale replication slot pinned WAL'
status: validated
occurrences: 4
created: '2026-09-15'
resource_type: postgres/rds
---

## Symptom

Disk usage climbs until backups fail with 'No space left on device'; pg_wal is huge.

## Root cause

A stale replication slot from a decommissioned replica prevented WAL recycling.

## Fix

pg_drop_replication_slot for the stale slot; vacuum; monitor slot lag.

## Verification

pg_wal shrinks; next two backups succeed.
