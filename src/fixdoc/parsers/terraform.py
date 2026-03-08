"""
Terraform error parser with multi-cloud support.

Supports parsing errors from:
- AWS (aws_*)
- Azure (azurerm_*)
- GCP (google_*)
"""

import re
from dataclasses import dataclass
from typing import Optional

from .base import ParsedError, ErrorParser, CloudProvider, ErrorSeverity


# Cloud provider detection patterns
AWS_RESOURCE_PATTERN = re.compile(r'\baws_[a-z_]+\b', re.IGNORECASE)
AZURE_RESOURCE_PATTERN = re.compile(r'\bazurerm_[a-z_]+\b', re.IGNORECASE)
GCP_RESOURCE_PATTERN = re.compile(r'\bgoogle_[a-z_]+\b', re.IGNORECASE)

# TF config/workflow error title → PascalCase error code
TF_CONFIG_ERRORS = {
    "invalid default value": "InvalidDefaultValue",
    "no value for required variable": "MissingRequiredVariable",
    "unsupported argument": "UnsupportedArgument",
    "missing required argument": "MissingRequiredArgument",
    "reference to undeclared": "UndeclaredReference",
    "inconsistent dependency lock file": "InconsistentLockFile",
    "module not installed": "ModuleNotInstalled",
    "failed to query available provider packages": "ProviderQueryFailed",
    "invalid value for input variable": "InvalidInputVariable",
    "this object does not have an attribute": "UndeclaredAttribute",
}

_INIT_CODES = {"InconsistentLockFile", "ModuleNotInstalled", "ProviderQueryFailed"}

# Common AWS error codes
AWS_ERROR_CODES = {
    'AccessDenied', 'AccessDeniedException', 'UnauthorizedAccess',
    'BucketAlreadyExists', 'BucketAlreadyOwnedByYou',
    'InvalidParameterValue', 'InvalidParameterCombination',
    'ResourceNotFoundException', 'ResourceInUseException',
    'LimitExceeded', 'QuotaExceeded', 'ServiceQuotaExceededException',
    'ValidationException', 'ValidationError',
    'InvalidAMIID', 'InvalidSubnet', 'InvalidVpcID',
    'InsufficientInstanceCapacity', 'InstanceLimitExceeded',
    'DBInstanceNotFound', 'DBSubnetGroupDoesNotCoverEnoughAZs',
    'StorageQuotaExceeded', 'InvalidDBInstanceState',
}

# Common Azure error codes
AZURE_ERROR_CODES = {
    'AuthorizationFailed', 'AuthenticationFailed',
    'StorageAccountAlreadyTaken', 'StorageAccountNotFound',
    'SkuNotAvailable', 'QuotaExceeded',
    'ResourceNotFound', 'ResourceGroupNotFound',
    'ConflictError', 'Conflict',
    'InvalidParameter', 'BadRequest',
    'PrincipalNotFound', 'RoleAssignmentExists',
}


@dataclass
class TerraformError(ParsedError):
    """Terraform-specific error with additional context."""

    terraform_action: Optional[str] = None  # create, update, delete
    module_path: Optional[str] = None

    def __post_init__(self):
        self.error_type = "terraform"
        super().__post_init__()


class TerraformParser(ErrorParser):
    """Parser for Terraform apply/plan errors."""

    @property
    def name(self) -> str:
        return "terraform"

    def can_parse(self, text: str) -> bool:
        """Check if text looks like Terraform output."""
        indicators = [
            r'Error:',
            r'│\s*Error:',
            r'aws_\w+\.',
            r'azurerm_\w+\.',
            r'google_\w+\.',
            r'\.tf\s+line\s+\d+',
            r'with\s+\w+\.\w+',
            r'Plan:.*to add.*to change.*to destroy',
            r'terraform\s+(init|plan|apply)',
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in indicators) #regex to determine if we can parse

    def parse(self, text: str) -> list[TerraformError]:
        """Parse Terraform output for all errors."""
        errors = []

        # Split on error boundaries
        # Handle both box-drawing and plain error formats
        parts = re.split(r'(?=│?\s*Error:)', text)

        for part in parts:
            if 'Error:' in part:
                parsed = self.parse_single(part)
                if parsed:
                    errors.append(parsed)

        # Deduplicate by resource address + raw error block content
        seen = set()
        unique = []
        for e in errors:
            key = (e.resource_address, hash(e.raw_output.strip()))
            if key not in seen:
                seen.add(key)
                unique.append(e)

        return unique

    def parse_single(self, text: str) -> Optional[TerraformError]:
        """Parse a single Terraform error block."""

        # Extract error message
        error_match = re.search(
            r'│?\s*Error:\s*(.+?)(?=\n│?\s*\n|\n\n|$)',
            text,
            re.DOTALL
        )
        if not error_match:
            error_match = re.search(r'Error:\s*(.+?)(?=\n\n|$)', text, re.DOTALL)
        if not error_match:
            return None

        error_block = error_match.group(0)

        # Extract resource information
        resource_info = self._extract_resource_info(text)

        # Extract file and line
        file_match = re.search(r'on\s+([^\s]+\.tf)\s+line\s+(\d+)', text)
        file = file_match.group(1) if file_match else None
        line = int(file_match.group(2)) if file_match else None

        # Detect cloud provider (from text and resource type)
        cloud_provider = self._detect_cloud_provider(text, resource_info.get('type'))

        # Extract error code
        error_code = self._extract_error_code(text)

        # Extract error message
        error_message = self._extract_error_message(text, error_block)

        # Detect action (create, update, delete)
        action = self._detect_action(text)

        # Generate tags
        tags = self._generate_tags(resource_info, cloud_provider, error_code)

        # Generate suggestions
        suggestions = self._generate_suggestions(error_code, error_message, cloud_provider)

        return TerraformError(
            error_type="terraform",
            error_message=error_message,
            raw_output=text,
            resource_type=resource_info.get('type'),
            resource_name=resource_info.get('name'),
            resource_address=resource_info.get('address'),
            file=file,
            line=line,
            error_code=error_code,
            cloud_provider=cloud_provider,
            severity=ErrorSeverity.ERROR,
            suggestions=suggestions,
            tags=tags,
            terraform_action=action,
            module_path=resource_info.get('module'),
        )

    def _extract_resource_info(self, text: str) -> dict:
        """Extract resource type, name, address from error text."""
        info = {
            'type': 'unknown',
            'name': 'unknown',
            'address': 'unknown',
            'module': None,
        }

        # Clean up box-drawing characters for matching
        clean_text = re.sub(r'│', '', text)

        # Try to match "with <resource_address>" pattern
        # Handles: "with aws_s3_bucket.data," or "with module.app.aws_s3_bucket.data,"
        resource_match = re.search(
            r'with\s+((?:module\.[a-z0-9_-]+\.)*([a-z][a-z0-9_]*)\.([-a-z0-9_]+))',
            clean_text,
            re.IGNORECASE,
        )

        if resource_match:
            info['address'] = resource_match.group(1)
            info['type'] = resource_match.group(2)
            info['name'] = resource_match.group(3)

            # Extract module path if present
            if 'module.' in info['address']:
                module_match = re.match(r'(module\.[^.]+)', info['address'])
                if module_match:
                    info['module'] = module_match.group(1)
        else:
            # Try alternative: look for resource type patterns directly
            # Matches aws_*, azurerm_*, google_* resource types
            direct_match = re.search(
                r'\b((?:aws|azurerm|google)_[a-z0-9_]+)\.([a-z0-9_-]+)\b',
                clean_text,
                re.IGNORECASE
            )
            if direct_match:
                info['type'] = direct_match.group(1)
                info['name'] = direct_match.group(2)
                info['address'] = f"{info['type']}.{info['name']}"
            else:
                # Fallback: "in resource "type" "name":" pattern from validation errors
                resource_def_match = re.search(
                    r'in\s+resource\s+"((?:aws|azurerm|google)_[a-z0-9_]+)"\s+"([a-z0-9_-]+)"',
                    clean_text,
                    re.IGNORECASE
                )
                if resource_def_match:
                    info['type'] = resource_def_match.group(1)
                    info['name'] = resource_def_match.group(2)
                    info['address'] = f"{info['type']}.{info['name']}"
            if info['address'] == 'unknown':
                # Fallback: Pattern "creating <ResourceType> (<name>)"
                alt_match = re.search(
                    r'(?:creating|updating|deleting)\s+([A-Za-z0-9_\s]+)\s*\(([^)]+)\)',
                    clean_text,
                    re.IGNORECASE
                )
                if alt_match:
                    info['type'] = alt_match.group(1).strip().replace(' ', '_').lower()
                    info['name'] = alt_match.group(2).strip()
                    info['address'] = f"{info['type']}.{info['name']}"

        if info['address'] == 'unknown':
            # Pattern 5: TF config-scope blocks (variable, local, output, module call)
            for scope_type in ("variable", "local", "output", "module"):
                m = re.search(rf'in {scope_type} "([a-zA-Z0-9_-]+)"', clean_text)
                if m:
                    name = m.group(1)
                    info['address'] = f'{scope_type}.{name}'
                    info['type'] = scope_type
                    info['name'] = name
                    return info

        if info['address'] == 'unknown':
            # Pattern 6: Terraform workflow errors (init/lock/provider)
            # Extract error title from the Error: line
            title_match = re.search(r'Error:\s*(.+?)(?:\n|$)', clean_text)
            error_title = title_match.group(1).lower() if title_match else ""
            for fragment, code in TF_CONFIG_ERRORS.items():
                if fragment in error_title:
                    if code in _INIT_CODES:
                        info['address'] = 'terraform.init'
                        info['type'] = 'terraform'
                        info['name'] = 'init'
                    return info

        return info

    def _detect_cloud_provider(self, text: str, resource_type: Optional[str] = None) -> CloudProvider:
        """Detect which cloud provider the error relates to."""
        # First check resource type if provided
        if resource_type and resource_type != 'unknown':
            if resource_type.startswith('aws_'):
                return CloudProvider.AWS
            if resource_type.startswith('azurerm_'):
                return CloudProvider.AZURE
            if resource_type.startswith('google_'):
                return CloudProvider.GCP

        # Then check text patterns
        if AWS_RESOURCE_PATTERN.search(text):
            return CloudProvider.AWS
        if AZURE_RESOURCE_PATTERN.search(text):
            return CloudProvider.AZURE
        if GCP_RESOURCE_PATTERN.search(text):
            return CloudProvider.GCP

        # Check for provider-specific error patterns
        aws_patterns = [
            r'arn:aws:', r'amazonaws\.com', r'aws-sdk',
            r'ec2:', r's3:', r'iam:', r'lambda:',
        ]
        azure_patterns = [
            r'azure\.com', r'microsoft\.com', r'\.azure\.',
            r'subscription.*resource\s*group',
        ]
        gcp_patterns = [
            r'googleapis\.com', r'gcloud', r'projects/[^/]+/',
        ]

        for pattern in aws_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return CloudProvider.AWS
        for pattern in azure_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return CloudProvider.AZURE
        for pattern in gcp_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return CloudProvider.GCP

        return CloudProvider.UNKNOWN

    def _extract_error_code(self, text: str) -> Optional[str]:
        """Extract error code from Terraform output."""
        # Try explicit Code: field first (highest priority)
        code_match = re.search(r'Code:\s*["\']?([A-Za-z][A-Za-z0-9_]+)["\']?', text)
        if code_match:
            return code_match.group(1)

        # Try api error pattern: "api error <ErrorCode>: message"
        api_error_match = re.search(r'api\s+error\s+([A-Za-z][A-Za-z0-9_]+):', text, re.IGNORECASE)
        if api_error_match:
            return api_error_match.group(1)

        # Try to find known AWS-style error codes (prioritize over generic status)
        for code in AWS_ERROR_CODES:
            if code in text:
                return code

        # Try to find Azure-style error codes
        for code in AZURE_ERROR_CODES:
            if code in text:
                return code

        # Try Status: field with descriptive name (e.g., "403 Forbidden")
        status_match = re.search(r'Status:\s*(\d+\s*[A-Za-z]+)', text)
        if status_match:
            return status_match.group(1).replace(' ', '')

        # Fallback: try generic status code
        status_code_match = re.search(r'StatusCode:\s*(\d+)', text)
        if status_code_match:
            return status_code_match.group(1)

        # Fallback: match error title against known TF config error patterns
        title_match = re.search(r'Error:\s*(.+?)(?:\n|$)', text)
        if title_match:
            title_lower = title_match.group(1).lower()
            for fragment, code in TF_CONFIG_ERRORS.items():
                if fragment in title_lower:
                    return code

        return None

    def _extract_error_message(self, text: str, error_block: str) -> str:
        """Extract the main error message."""
        # Try Message: field first
        msg_match = re.search(
            r'Message:\s*["\']?(.+?)["\']?(?=\n│|\n\n|$)',
            text,
            re.DOTALL
        )
        if msg_match:
            message = msg_match.group(1).strip()
        else:
            # Use the first line of the error block
            first_line = error_block.split('\n')[0]
            message = re.sub(r'^│?\s*Error:\s*', '', first_line).strip()

        # Clean up the message
        message = re.sub(r'\s+', ' ', message).strip()
        message = re.sub(r'^│\s*', '', message)

        return message[:500]

    def _detect_action(self, text: str) -> Optional[str]:
        """Detect the Terraform action being performed."""
        if re.search(r'creating', text, re.IGNORECASE):
            return 'create'
        if re.search(r'updating|modifying', text, re.IGNORECASE):
            return 'update'
        if re.search(r'deleting|destroying', text, re.IGNORECASE):
            return 'delete'
        return None

    def _generate_tags(
        self,
        resource_info: dict,
        cloud_provider: CloudProvider,
        error_code: Optional[str]
    ) -> list[str]:
        """Generate relevant tags for the error."""
        tags = ['terraform']

        if cloud_provider != CloudProvider.UNKNOWN:
            tags.append(cloud_provider.value)

        if resource_info.get('type') and resource_info['type'] != 'unknown':
            tags.append(resource_info['type'])

        if error_code:
            tags.append(error_code)

        return tags

    def _generate_suggestions(
        self,
        error_code: Optional[str],
        error_message: str,
        cloud_provider: CloudProvider
    ) -> list[str]:
        """Generate fix suggestions based on error patterns."""
        suggestions = []

        if not error_code:
            return suggestions

        # AWS-specific suggestions
        if cloud_provider == CloudProvider.AWS:
            if error_code in ('AccessDenied', 'AccessDeniedException'):
                suggestions.append("Check IAM permissions for the Terraform execution role")
                suggestions.append("Verify the resource policy allows the action")
            elif error_code == 'BucketAlreadyExists':
                suggestions.append("S3 bucket names are globally unique - use a different name")
                suggestions.append("Add a random suffix to the bucket name")
            elif 'Quota' in error_code or 'Limit' in error_code:
                suggestions.append("Request a service quota increase via AWS Support")
                suggestions.append("Check current usage in AWS Service Quotas console")
            elif error_code == 'InvalidAMIID':
                suggestions.append("Verify the AMI exists in the target region")
                suggestions.append("Check if the AMI is shared with your account")
            elif 'InsufficientCapacity' in error_code:
                suggestions.append("Try a different availability zone")
                suggestions.append("Try a different instance type")

        # Azure-specific suggestions
        elif cloud_provider == CloudProvider.AZURE:
            if error_code in ('AuthorizationFailed', 'AuthenticationFailed'):
                suggestions.append("Check Azure RBAC role assignments")
                suggestions.append("Verify service principal credentials")
            elif error_code == 'StorageAccountAlreadyTaken':
                suggestions.append("Storage account names are globally unique - use a different name")
            elif error_code == 'SkuNotAvailable':
                suggestions.append("Check VM size availability in the target region")
                suggestions.append("Try a different region or VM size")
            elif error_code == 'ConflictError' and 'soft' in error_message.lower():
                suggestions.append("Recover or purge the soft-deleted resource")
                suggestions.append("Use az keyvault purge or az keyvault recover")

        return suggestions


# Convenience function for backwards compatibility
def parse_terraform_output(output: str) -> list[TerraformError]:
    """Parse Terraform output for all errors."""
    parser = TerraformParser()
    return parser.parse(output)


def is_terraform_output(text: str) -> bool:
    """Check if text looks like Terraform output."""
    parser = TerraformParser()
    return parser.can_parse(text)
