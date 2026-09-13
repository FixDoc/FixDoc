# Example credential exposure incident

All values in this fixture are synthetic examples for redaction tests.

## Summary

Debug output included AKIAIOSFODNN7EXAMPLE and password=hunter2.

## Root cause

The test client logged Authorization: Bearer abc123def456ghi789,
postgres://demo:s3cretpw@localhost/example and token=ghp_2938471password.

## Resolution

Remove credential logging and replace the exposed test credentials.

## Verification

The next test run contained no credentials in its debug output.
