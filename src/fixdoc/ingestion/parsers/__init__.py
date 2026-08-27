"""
Multi-cloud and multi-tool error parsers for FixDoc.

This module provides unified parsing for:
- Terraform errors (AWS, Azure, GCP)
- Kubernetes errors (kubectl, Helm)
"""

from .base import ErrorParser, ParsedError
from .kubernetes import KubernetesError, KubernetesParser
from .router import ErrorSource, detect_and_parse, detect_error_source
from .terraform import TerraformError, TerraformParser

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
