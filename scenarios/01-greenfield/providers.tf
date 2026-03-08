terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region                      = var.aws_region
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true

  skip_credentials_validation  = true
  skip_metadata_api_check      = true
  skip_requesting_account_id   = true
  skip_region_validation       = true

  endpoints {
    ec2 = var.localstack_endpoint
    iam = var.localstack_endpoint
    sts = var.localstack_endpoint
    s3  = var.localstack_endpoint
    logs = var.localstack_endpoint
  }
}