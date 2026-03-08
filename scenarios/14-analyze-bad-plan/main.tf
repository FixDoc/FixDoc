# Scenario 14: Bad Plan — Graceful Error Handling
#
# A minimal valid Terraform config (just an S3 bucket) provides the "happy
# path" baseline plan for comparison. The fixtures/ subdirectory contains
# three malformed plan JSON files that stress-test blast_radius error handling:
#
#   fixtures/empty.json      — {} (no format_version, no resource_changes)
#   fixtures/invalid.json    — not valid JSON at all
#   fixtures/no_changes.json — valid plan JSON with resource_changes: []
#
# Run with: run_analyze_bad_plan (runs valid plan + probes each fixture)

resource "aws_s3_bucket" "baseline" {
  bucket = "${var.name_prefix}-baseline"

  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}
