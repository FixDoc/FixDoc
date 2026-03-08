# Scenario 15: No-Op Plan
#
# A full apply followed by an immediate re-plan. All resources show "no-op"
# or "read" actions in the second plan, with nothing actionable.
#
# Expected blast radius behavior:
#   - is_actionable_change() returns False for all nodes → score = 0
#   - resource_warnings = [] (no actionable changed resources)
#   - severity = "low"
#
# Includes a data source (data "aws_caller_identity") to confirm "read"
# actions are also filtered correctly.
#
# Run with: run_apply_then_plan_noop (apply → plan with no changes)

locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name_prefix}-data"
  tags   = merge(local.tags, { Name = "${var.name_prefix}-data" })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/fixdoc/${var.name_prefix}/app"
  retention_in_days = 7
  tags              = local.tags
}

data "aws_caller_identity" "current" {}
