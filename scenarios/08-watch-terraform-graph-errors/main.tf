locals {
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

# Resource is never reached — TF fails on variable type validation first
resource "aws_s3_bucket" "placeholder" {
  count  = var.bucket_count
  bucket = "${var.name_prefix}-bucket-${count.index}"
  tags   = local.tags
}
