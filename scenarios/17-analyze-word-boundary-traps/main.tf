# Scenario 17: Word Boundary Traps
#
# Plan changes aws_s3_bucket. The fix database (seeded manually per RUNBOOK)
# contains fixes for aws_s3_bucket_policy and aws_s3_bucket_public_access_block
# but NOT aws_s3_bucket itself.
#
# Verifies: No false-positive text_match
#   - The regex \baws_s3_bucket\b does NOT match aws_s3_bucket_policy because
#     underscore (_) is a word character, so no \b boundary exists between
#     "bucket" and "_policy".
#   - "Prior Issues for Changed Resources" section should be ABSENT.
#
# Manual setup required (see RUNBOOK.md section 17):
#   fixdoc add --issue "aws_s3_bucket_policy permission denied" \
#              --resolution "Check bucket policy JSON" \
#              --tags "s3,policy"
#   fixdoc add --issue "aws_s3_bucket_public_access_block not propagating" \
#              --resolution "Wait for eventual consistency" \
#              --tags "s3,public-access"
#
# Run with: run_plan (plan-only)

resource "aws_s3_bucket" "main" {
  bucket = "${var.name_prefix}-main"

  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}

resource "aws_s3_bucket" "secondary" {
  bucket = "${var.name_prefix}-secondary"

  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}
