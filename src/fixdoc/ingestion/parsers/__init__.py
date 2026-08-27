"""
Multi-cloud and multi-tool error parsers for FixDoc.

This module provides unified parsing for:
- Terraform errors (AWS, Azure, GCP)
- Kubernetes errors (kubectl, Helm)
"""

from .base import ParsedError, ErrorParser
from .terraform import TerraformParser, TerraformError
from .kubernetes import KubernetesParser, KubernetesError
from .router import detect_and_parse, detect_error_source, ErrorSource

__all__ = [
    "ParsedError",
    "ErrorParser",
    "TerraformParser",
    "TerraformError",
    "KubernetesParser",
    "KubernetesError",
    "detect_and_parse",
    "detect_error_source",
    "ErrorSource",
]
