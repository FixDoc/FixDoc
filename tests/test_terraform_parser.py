"""Comprehensive tests for the Terraform error parser."""

import pytest
from pathlib import Path

from fixdoc.parsers.terraform import TerraformParser, TerraformError
from fixdoc.parsers.base import CloudProvider, ErrorSeverity


# Get the fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "terraform"


class TestTerraformParserDetection:
    """Tests for error source detection."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_detects_terraform_output_with_error(self):
        text = """
        │ Error: creating Storage Account
        │   with azurerm_storage_account.main
        """
        assert self.parser.can_parse(text) is True

    def test_detects_terraform_with_aws_resource(self):
        text = "aws_s3_bucket.data: Creating..."
        assert self.parser.can_parse(text) is True

    def test_detects_terraform_with_azure_resource(self):
        text = "azurerm_storage_account.main: Creating..."
        assert self.parser.can_parse(text) is True

    def test_detects_terraform_with_gcp_resource(self):
        text = "google_compute_instance.main: Creating..."
        assert self.parser.can_parse(text) is True

    def test_detects_terraform_with_plan_output(self):
        text = "Plan: 5 to add, 2 to change, 1 to destroy."
        assert self.parser.can_parse(text) is True

    def test_does_not_detect_random_text(self):
        text = "Hello, this is just random text without terraform content."
        assert self.parser.can_parse(text) is False

    def test_does_not_detect_kubectl_output(self):
        text = "kubectl apply -f deployment.yaml\nError from server (Forbidden)"
        assert self.parser.can_parse(text) is False


class TestAWSErrorParsing:
    """Tests for AWS-specific error parsing."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_parse_s3_bucket_already_exists(self):
        text = """
        aws_s3_bucket.data: Creating...

        │ Error: creating Amazon S3 (Simple Storage) Bucket (my-company-data-bucket):
        │ operation error S3: CreateBucket, https response error StatusCode: 409,
        │ RequestID: ABC123DEF456, HostID: xyz789,
        │ api error BucketAlreadyExists: The requested bucket name is not available.
        │ The bucket namespace is shared by all users of the system.
        │
        │   with aws_s3_bucket.data,
        │   on storage/main.tf line 1, in resource "aws_s3_bucket" "data":
        │    1: resource "aws_s3_bucket" "data" {
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AWS
        assert error.resource_type == "aws_s3_bucket"
        assert error.resource_name == "data"
        assert error.error_code == "BucketAlreadyExists"
        assert error.file == "storage/main.tf"
        assert error.line == 1
        assert "BucketAlreadyExists" in error.short_error()

    def test_parse_iam_access_denied(self):
        text = """
        │ Error: creating Lambda Function (data-processor): AccessDeniedException:
        │ User: arn:aws:iam::123456789012:user/terraform-ci is not authorized to perform:
        │ iam:PassRole on resource: arn:aws:iam::123456789012:role/lambda-processor-role
        │
        │   with aws_lambda_function.processor,
        │   on modules/lambda/main.tf line 45, in resource "aws_lambda_function" "processor":
        │   45: resource "aws_lambda_function" "processor" {
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AWS
        assert error.resource_type == "aws_lambda_function"
        # Accept either AccessDenied or AccessDeniedException
        assert error.error_code in ("AccessDenied", "AccessDeniedException")
        assert "iam:PassRole" in error.error_message or "PassRole" in error.raw_output

    def test_parse_ec2_capacity_error(self):
        text = """
        │ Error: creating EC2 Instance: InsufficientInstanceCapacity:
        │ We currently do not have sufficient p4d.24xlarge capacity in the
        │ Availability Zone you requested (us-east-1a).
        │
        │   with aws_instance.ml_training,
        │   on compute/ml.tf line 1, in resource "aws_instance" "ml_training":
        │    1: resource "aws_instance" "ml_training" {
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AWS
        assert error.resource_type == "aws_instance"
        assert "InsufficientInstanceCapacity" in error.error_code or "capacity" in error.error_message.lower()

    def test_generates_aws_suggestions(self):
        text = """
        aws_s3_bucket.test: Creating...

        │ Error: creating Amazon S3 Bucket (my-bucket):
        │ api error BucketAlreadyExists: The requested bucket name is not available.
        │
        │   with aws_s3_bucket.test,
        │   on main.tf line 1, in resource "aws_s3_bucket" "test":
        │    1: resource "aws_s3_bucket" "test" {
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AWS
        # Should have suggestions for BucketAlreadyExists
        assert len(error.suggestions) > 0
        assert any("unique" in s.lower() or "different name" in s.lower() for s in error.suggestions)


class TestAzureErrorParsing:
    """Tests for Azure-specific error parsing."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_parse_storage_account_exists(self):
        text = """
        │ Error: creating Storage Account (Subscription: "12345678-1234-1234-1234-123456789abc"
        │ Resource Group Name: "rg-data-prod"
        │ Storage Account Name: "stcompanydata"):
        │
        │ Code: "StorageAccountAlreadyTaken"
        │ Message: "The storage account named stcompanydata is already taken."
        │
        │   with azurerm_storage_account.main,
        │   on storage/main.tf line 1, in resource "azurerm_storage_account" "main":
        │    1: resource "azurerm_storage_account" "main" {
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AZURE
        assert error.resource_type == "azurerm_storage_account"
        assert error.error_code == "StorageAccountAlreadyTaken"

    def test_parse_vm_sku_not_available(self):
        text = """
        │ Error: creating Linux Virtual Machine
        │
        │ Code: "SkuNotAvailable"
        │ Message: "The requested VM size Standard_NC24ads_A100_v4 is not available"
        │
        │   with azurerm_linux_virtual_machine.ml,
        │   on compute/ml.tf line 15
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AZURE
        assert error.error_code == "SkuNotAvailable"

    def test_parse_keyvault_soft_delete_conflict(self):
        text = """
        │ Error: creating Key Vault
        │
        │ Code: "ConflictError"
        │ Message: "A vault with the same name already exists in deleted state."
        │
        │   with azurerm_key_vault.main,
        │   on security/keyvault.tf line 1
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        error = errors[0]
        assert error.cloud_provider == CloudProvider.AZURE
        assert error.error_code == "ConflictError"


class TestMultipleErrors:
    """Tests for parsing multiple errors from single output."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_parse_multiple_errors(self):
        text = """
        │ Error: creating ELBv2 Application Load Balancer (prod-api-alb):
        │ InvalidSubnet: VPC vpc-0abc123def456789a has no internet gateway
        │
        │   with module.app.aws_lb.main,
        │   on modules/app/alb.tf line 1

        │ Error: creating RDS DB Instance (prod-postgres): InvalidParameterValue:
        │ IAM Database Authentication is not supported
        │
        │   with module.database.aws_db_instance.main,
        │   on modules/database/main.tf line 15
        """
        errors = self.parser.parse(text)

        assert len(errors) == 2
        assert errors[0].resource_type == "aws_lb"
        assert errors[1].resource_type == "aws_db_instance"

    def test_deduplicates_errors_by_address(self):
        text = """
        │ Error: creating S3 Bucket (test-bucket): BucketAlreadyExists
        │   with aws_s3_bucket.test,
        │   on main.tf line 1

        │ Error: creating S3 Bucket (test-bucket): BucketAlreadyExists
        │   with aws_s3_bucket.test,
        │   on main.tf line 1
        """
        errors = self.parser.parse(text)

        # Should deduplicate
        assert len(errors) == 1


class TestTagGeneration:
    """Tests for automatic tag generation."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_generates_resource_type_tag(self):
        text = """
        aws_s3_bucket.test: Creating...

        │ Error: test error
        │   with aws_s3_bucket.test,
        │   on main.tf line 1, in resource "aws_s3_bucket" "test":
        """
        errors = self.parser.parse(text)

        assert len(errors) == 1
        tags = errors[0].generate_tags()
        assert "aws_s3_bucket" in tags

    def test_generates_cloud_provider_tag(self):
        text = """
        aws_s3_bucket.test: Creating...

        │ Error: test error
        │   with aws_s3_bucket.test,
        │   on main.tf line 1, in resource "aws_s3_bucket" "test":
        """
        errors = self.parser.parse(text)
        tags = errors[0].generate_tags()
        assert "aws" in tags

    def test_generates_error_code_tag(self):
        text = """
        │ Error: BucketAlreadyExists: test
        │   with aws_s3_bucket.test,
        │   on main.tf line 1
        """
        errors = self.parser.parse(text)
        tags = errors[0].generate_tags()
        assert "BucketAlreadyExists" in tags


class TestFixtureFiles:
    """Tests using the comprehensive fixture files."""

    def setup_method(self):
        self.parser = TerraformParser()

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_aws_iam_permission_errors(self):
        fixture_file = FIXTURES_DIR / "aws" / "iam_permission_errors.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        # Split by fixture separator
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        for i, case in enumerate(test_cases):
            if "Error:" not in case:
                continue

            errors = self.parser.parse(case)
            assert len(errors) >= 1, f"Failed to parse test case {i + 1}"
            assert errors[0].cloud_provider == CloudProvider.AWS

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_aws_s3_errors(self):
        fixture_file = FIXTURES_DIR / "aws" / "s3_errors.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        for i, case in enumerate(test_cases):
            if "Error:" not in case:
                continue

            errors = self.parser.parse(case)
            assert len(errors) >= 1, f"Failed to parse S3 test case {i + 1}"
            # Should identify as AWS
            assert errors[0].cloud_provider == CloudProvider.AWS

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_aws_multi_error_complex(self):
        fixture_file = FIXTURES_DIR / "aws" / "multi_error_complex.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        # Test case 1 should have 5 errors
        if len(test_cases) >= 1:
            errors = self.parser.parse(test_cases[0])
            # The first test case has multiple errors
            assert len(errors) >= 3, "Should parse multiple errors from complex output"

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_azure_comprehensive_errors(self):
        fixture_file = FIXTURES_DIR / "azure" / "comprehensive_errors.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        for i, case in enumerate(test_cases):
            if "Error:" not in case:
                continue

            errors = self.parser.parse(case)
            assert len(errors) >= 1, f"Failed to parse Azure test case {i + 1}"
            assert errors[0].cloud_provider == CloudProvider.AZURE


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_handles_empty_input(self):
        errors = self.parser.parse("")
        assert errors == []

    def test_handles_no_errors_in_output(self):
        text = """
        Terraform will perform the following actions:

        Plan: 5 to add, 0 to change, 0 to destroy.

        Apply complete!
        """
        errors = self.parser.parse(text)
        assert errors == []

    def test_handles_malformed_resource_address(self):
        text = """
        │ Error: Something went wrong
        │ No resource address provided
        """
        errors = self.parser.parse(text)
        # Should still parse but with unknown resource
        assert len(errors) == 1
        assert errors[0].resource_type == "unknown"

    def test_handles_unicode_in_error(self):
        text = """
        │ Error: creating resource "名前"
        │   with aws_s3_bucket.test,
        │   on main.tf line 1
        """
        errors = self.parser.parse(text)
        assert len(errors) == 1

    def test_truncates_very_long_error_message(self):
        long_message = "A" * 1000
        text = f"""
        │ Error: {long_message}
        │   with aws_s3_bucket.test,
        │   on main.tf line 1
        """
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert len(errors[0].error_message) <= 500


class TestResourceDefPattern:
    """Tests for 'in resource \"type\" \"name\":' pattern extraction."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_parses_nine_validation_errors(self):
        """Regression: 9 distinct errors should not collapse into 1."""
        text = """
Error: Missing required argument

  on main.tf line 10, in resource "aws_vpc" "main":
  10: resource "aws_vpc" "main" {

The argument "cidr_block" is required, but no definition was found.

Error: Missing required argument

  on main.tf line 20, in resource "aws_subnet" "public":
  20: resource "aws_subnet" "public" {

The argument "vpc_id" is required, but no definition was found.

Error: Unsupported argument

  on main.tf line 30, in resource "aws_security_group" "web":
  30: resource "aws_security_group" "web" {

An argument named "invalid_arg" is not expected here.

Error: Missing required argument

  on main.tf line 50, in resource "aws_instance" "app":
  50: resource "aws_instance" "app" {

The argument "ami" is required, but no definition was found.

Error: Missing required argument

  on main.tf line 80, in resource "aws_iam_role" "lambda_role":
  80: resource "aws_iam_role" "lambda_role" {

The argument "assume_role_policy" is required, but no definition was found.

Error: Missing required argument

  on main.tf line 100, in resource "aws_lambda_function" "myfunction":
  100: resource "aws_lambda_function" "myfunction" {

The argument "function_name" is required, but no definition was found.

Error: Missing required argument

  on main.tf line 100, in resource "aws_lambda_function" "myfunction":
  100: resource "aws_lambda_function" "myfunction" {

The argument "role" is required, but no definition was found.

Error: Missing required argument

  on main.tf line 120, in resource "aws_s3_bucket" "data":
  120: resource "aws_s3_bucket" "data" {

The argument "bucket" is required, but no definition was found.

Error: Invalid expression

  on main.tf line 168, in resource "aws_rds" "rds":
  168: resource "aws_rds" "rds" {

Expected the start of an expression, but found an invalid expression token.
"""
        errors = self.parser.parse(text)
        assert len(errors) == 9

        # Verify each error has a proper resource address (not 'unknown')
        addresses = [e.resource_address for e in errors]
        assert "unknown" not in addresses


class TestTFConfigErrors:
    """Tests for TF config/workflow error parsing (Pattern 5 & 6)."""

    def setup_method(self):
        self.parser = TerraformParser()

    def test_variable_error_address(self):
        """Invalid default value for variable → resource_address=variable.<name>."""
        text = (
            "Error: Invalid default value for variable\n"
            "\n"
            "  on variables.tf line 17, in variable \"instance_count\":\n"
            "  17: default = \"not-a-number\"\n"
            "\n"
            "This default value is not compatible with the variable's type constraint."
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        e = errors[0]
        assert e.resource_address == "variable.instance_count"
        assert e.error_code == "InvalidDefaultValue"
        assert e.file == "variables.tf"

    def test_output_error_address(self):
        """Error in output block → resource_address=output.<name>."""
        text = (
            "Error: Reference to undeclared output value\n"
            "\n"
            "  on outputs.tf line 3, in output \"vpc_id\":\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].resource_address == "output.vpc_id"

    def test_local_error_address(self):
        """Error in local block → resource_address=local.<name>."""
        text = (
            "Error: Unsupported argument\n"
            "\n"
            "  on main.tf line 5, in local \"tags\":\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].resource_address == "local.tags"

    def test_module_call_error_address(self):
        """Error in module block → resource_address=module.<name>."""
        text = (
            "Error: Missing required argument\n"
            "\n"
            "  on main.tf line 10, in module \"vpc\":\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].resource_address == "module.vpc"

    def test_inconsistent_lock_file_address(self):
        """Lock file error → resource_address=terraform.init, error_code=InconsistentLockFile."""
        text = (
            "Error: Inconsistent dependency lock file\n"
            "\n"
            "The following dependency selections recorded in the lock file are inconsistent\n"
            "with the current configuration.\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        e = errors[0]
        assert e.resource_address == "terraform.init"
        assert e.error_code == "InconsistentLockFile"

    def test_module_not_installed_address(self):
        """Module not installed → resource_address=terraform.init, error_code=ModuleNotInstalled."""
        text = (
            "Error: Module not installed\n"
            "\n"
            "This module is not yet installed. Run `terraform init` to install all modules.\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        e = errors[0]
        assert e.resource_address == "terraform.init"
        assert e.error_code == "ModuleNotInstalled"

    def test_error_code_extracted_for_variable_error(self):
        """error_code populated for config-scope errors without explicit Code: field."""
        text = (
            "Error: Invalid default value for variable\n"
            "\n"
            "  on variables.tf line 5, in variable \"count\":\n"
        )
        errors = self.parser.parse(text)
        assert errors[0].error_code == "InvalidDefaultValue"

    def test_no_regression_on_resource_errors(self):
        """Standard resource errors are not affected by new patterns."""
        text = (
            "Error: creating S3 Bucket (my-bucket): BucketAlreadyExists\n"
            "\n"
            "  with aws_s3_bucket.data,\n"
            "  on main.tf line 5, in resource \"aws_s3_bucket\" \"data\":\n"
        )
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].resource_address == "aws_s3_bucket.data"

    def test_resource_def_pattern_extracts_address(self):
        """The 'in resource \"type\" \"name\":' pattern should extract address."""
        text = """
Error: Missing required argument

  on main.tf line 42, in resource "aws_lambda_function" "processor":
  42: resource "aws_lambda_function" "processor" {

The argument "runtime" is required, but no definition was found.
"""
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].resource_address == "aws_lambda_function.processor"
        assert errors[0].resource_type == "aws_lambda_function"
        assert errors[0].resource_name == "processor"

    def test_error_id_computed_for_terraform_errors(self):
        """TerraformError.__post_init__ should call super() to compute error_id."""
        text = """
Error: Missing required argument

  on main.tf line 10, in resource "aws_vpc" "main":
  10: resource "aws_vpc" "main" {

The argument "cidr_block" is required.
"""
        errors = self.parser.parse(text)
        assert len(errors) == 1
        assert errors[0].error_id != ""
        assert len(errors[0].error_id) == 12
