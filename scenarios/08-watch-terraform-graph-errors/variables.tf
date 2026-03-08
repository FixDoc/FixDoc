variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}
variable "name_prefix" {
  type    = string
  default = "fixdoc-watch08"
}

# Type mismatch: string default for number type
variable "instance_count" {
  type    = number
  default = 3   # invalid — TF expects a number
}

# Type mismatch: string default for number type
variable "bucket_count" {
  type    = number
  default = 2    # invalid — TF expects a number
}
