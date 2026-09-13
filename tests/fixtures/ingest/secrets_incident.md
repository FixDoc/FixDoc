# Deploy failed after credential rotation

## Summary

Deploy pipeline failed with 403 after rotating credentials. The job env still
had AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE and password=hunter2 baked into
the runner config, plus an old header Authorization: Bearer abc123def456ghi789
and a database URL postgres://svc:s3cretpw@db.internal/app.

## Fix

Rotated the runner secrets and moved credentials to the vault. Purged the
stale token=ghp_2938471password from CI variables.

## Verification

Deploy succeeded; secret scanner reports clean.
