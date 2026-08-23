"""
Error source detection and routing.

This module provides unified error parsing by detecting the source
of an error and routing to the appropriate parser.
"""

from enum import Enum
from typing import Optional

from .base import ParsedError
from .terraform import TerraformParser, TerraformError
from .kubernetes import KubernetesParser, KubernetesError


class ErrorSource(Enum):
    """Detected error source."""

    TERRAFORM = "terraform"
    KUBERNETES = "kubernetes"
    HELM = "helm"
    ANSIBLE = "ansible"  # Future support
    UNKNOWN = "unknown"


# Singleton parser instances
_terraform_parser = TerraformParser()
_kubernetes_parser = KubernetesParser()


def detect_error_source(text: str) -> ErrorSource:
    """
    Detect the source of an error from the text.

    This uses heuristics to determine whether the error comes from
    Terraform, Kubernetes (kubectl/Helm), or another source, we may have to change this at some point

    Args:
        text: The error output text to analyze

    Returns:
        detected error source
    """
    # Check for Helm first (subset of Kubernetes)
    helm_indicators = [
        "helm install",
        "helm upgrade",
        "helm rollback",
        "INSTALLATION FAILED",
        "UPGRADE FAILED",
        "ROLLBACK FAILED",
        "helm template",
        'release "',
    ]
    if any(ind.lower() in text.lower() for ind in helm_indicators):
        return ErrorSource.HELM

    # Check for kubectl/Kubernetes
    if _kubernetes_parser.can_parse(text):
        return ErrorSource.KUBERNETES

    # Check for Terraform
    if _terraform_parser.can_parse(text):
        return ErrorSource.TERRAFORM

    return ErrorSource.UNKNOWN


def detect_and_parse(text: str) -> list[ParsedError]:
    """
    Automatically detect the error source and parse the text.

    This is the main entry point for unified error parsing. It detects
    whether the input is from Terraform, Kubernetes, or another source
    and routes to the appropriate parser.

    Args:
        text: The error output text to parse

    Returns:
        List of ParsedError objects (may be TerraformError or KubernetesError)
    """
    source = detect_error_source(text)

    if source == ErrorSource.TERRAFORM:
        return _terraform_parser.parse(text)

    if source in (ErrorSource.KUBERNETES, ErrorSource.HELM):
        return _kubernetes_parser.parse(text)

    # Unknown source - try all parsers
    errors = []

    # Try Terraform parser
    tf_errors = _terraform_parser.parse(text)
    if tf_errors:
        errors.extend(tf_errors)

    # Try Kubernetes parser
    k8s_errors = _kubernetes_parser.parse(text)
    if k8s_errors:
        errors.extend(k8s_errors)

    return errors


def parse_single_error(text: str) -> Optional[ParsedError]:
    """
    Parse a single error from the text.

    Useful when you expect only one error or want the most relevant one.

    Args:
        text: The error output text to parse

    Returns:
        A single ParsedError or None if no error found
    """
    errors = detect_and_parse(text)
    return errors[0] if errors else None


def get_parser_for_source(source: ErrorSource):
    """
    Get the parser instance for a given error source.

    Args:
        source: The ErrorSource to get a parser for

    Returns:
        The appropriate ErrorParser instance or None
    """
    if source == ErrorSource.TERRAFORM:
        return _terraform_parser
    if source in (ErrorSource.KUBERNETES, ErrorSource.HELM):
        return _kubernetes_parser
    return None


def summarize_errors(errors: list[ParsedError]) -> str:
    """
    Generate a summary of multiple errors.

    Args:
        errors: List of ParsedError objects

    Returns:
        A human-readable summary string
    """
    if not errors:
        return "No errors found"

    if len(errors) == 1:
        e = errors[0]
        return f"1 {e.error_type} error: {e.short_error()}"

    # Group by error type
    by_type = {}
    for e in errors:
        by_type.setdefault(e.error_type, []).append(e)

    parts = []
    for error_type, type_errors in by_type.items():
        parts.append(f"{len(type_errors)} {error_type} error(s)")

    return f"{len(errors)} errors found: " + ", ".join(parts)
