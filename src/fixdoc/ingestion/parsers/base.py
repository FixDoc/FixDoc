"""Base classes and interfaces for error parsers."""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


def compute_error_id(
    resource_address: Optional[str] = None,
    error_code: Optional[str] = None,
    file: Optional[str] = None,
    error_message: str = "",
) -> str:
    """Compute a stable 12-char hex hash for an error.

    Uses resource_address, error_code, file, and the first 160 chars
    of the error message (lowered + stripped) as inputs.
    """
    parts = [
        resource_address or "",
        error_code or "",
        file or "",
        error_message[:160].lower().strip(),
    ]
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class CloudProvider(Enum):
    """Cloud provider enumeration."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """Error severity levels."""

    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ParsedError:
    """
    Base class for all parsed errors.

    This provides a unified interface for errors from different sources
    (Terraform, Kubernetes, Ansible, etc.)
    """

    # Core error information
    error_type: str  # e.g., "terraform", "kubectl", "helm"
    error_message: str
    raw_output: str

    # Resource identification
    resource_type: Optional[str] = None  # e.g., "aws_s3_bucket", "Deployment"
    resource_name: Optional[str] = None
    resource_address: Optional[str] = None  # Full address like "module.app.aws_s3_bucket.main"

    # Location information
    file: Optional[str] = None
    line: Optional[int] = None
    namespace: Optional[str] = None  # For K8s resources

    # Error classification
    error_code: Optional[str] = None  # e.g., "BucketAlreadyExists", "ImagePullBackOff"
    cloud_provider: CloudProvider = CloudProvider.UNKNOWN
    severity: ErrorSeverity = ErrorSeverity.ERROR

    # Additional context
    suggestions: list[str] = field(default_factory=list)
    related_resources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Stable identifier (computed in __post_init__)
    error_id: str = field(default="", init=False)

    def __post_init__(self):
        self.error_id = compute_error_id(
            resource_address=self.resource_address,
            error_code=self.error_code,
            file=self.file,
            error_message=self.error_message,
        )

    def short_error(self, max_length: int = 100) -> str:
        """Return a shortened error description."""
        if self.error_code:
            prefix = f"{self.error_code}: "
            remaining = max_length - len(prefix)
            return f"{prefix}{self.error_message[:remaining]}"
        return self.error_message[:max_length]

    def generate_tags(self) -> str:
        """Generate comma-separated tags for this error."""
        tags = list(self.tags)

        if self.resource_type:
            tags.append(self.resource_type)

        if self.cloud_provider != CloudProvider.UNKNOWN:
            tags.append(self.cloud_provider.value)

        if self.error_code:
            tags.append(self.error_code)

        if self.error_type:
            tags.append(self.error_type)

        # Deduplicate while preserving order
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag.lower() not in seen:
                seen.add(tag.lower())
                unique_tags.append(tag)

        return ",".join(unique_tags)

    def to_issue_string(self) -> str:
        """Generate an issue string for Fix creation."""
        parts = []

        if self.resource_address:
            parts.append(self.resource_address)
        elif self.resource_type and self.resource_name:
            parts.append(f"{self.resource_type}/{self.resource_name}")
        elif self.resource_type:
            parts.append(self.resource_type)

        parts.append(self.short_error())

        return ": ".join(parts)


class ErrorParser(ABC):
    """Abstract base class for error parsers."""

    @abstractmethod
    def can_parse(self, text: str) -> bool:
        """Check if this parser can handle the given text."""
        pass

    @abstractmethod
    def parse(self, text: str) -> list[ParsedError]:
        """Parse the text and return a list of errors."""
        pass

    @abstractmethod
    def parse_single(self, text: str) -> Optional[ParsedError]:
        """Parse a single error block."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this parser."""
        pass
