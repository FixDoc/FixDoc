locals {
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

# Six buckets with the same name — first created wins, rest fail BucketAlreadyExists
# Run with: fixdoc watch -- terraform apply -auto-approve -parallelism=10
resource "aws_s3_bucket" "bomb" {
  count  = 6
  bucket = "${var.name_prefix}-bomb"  # identical name → parallel collision

  tags = merge(local.tags, { Name = "${var.name_prefix}-bomb" })
}
