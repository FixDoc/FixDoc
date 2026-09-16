---
id: fx_b3c4e7f1
type: fix
title: apply fails with BucketAlreadyExists on aws_s3_bucket
status: validated
occurrences: 2
created: '2026-09-15'
resource_type: terraform/aws
---

## Symptom

terraform apply fails: BucketAlreadyExists creating aws_s3_bucket.

## Root cause

S3 names are global; another account owns the name.

## Fix

Add the account/env suffix to the bucket name variable.

## Verification

apply completes; bucket created under unique name.
