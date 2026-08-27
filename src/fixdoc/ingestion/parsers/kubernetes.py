"""
Kubernetes error parser for kubectl and Helm.

Supports parsing errors from:
- kubectl apply/create/delete
- kubectl describe (events)
- kubectl logs
- Helm install/upgrade/rollback
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .base import ErrorParser, ErrorSeverity, ParsedError


class KubernetesErrorType(Enum):
    """Types of Kubernetes errors."""

    KUBECTL_APPLY = "kubectl_apply"
    KUBECTL_CREATE = "kubectl_create"
    POD_STATUS = "pod_status"
    POD_EVENT = "pod_event"
    HELM_INSTALL = "helm_install"
    HELM_UPGRADE = "helm_upgrade"
    HELM_TEMPLATE = "helm_template"
    RESOURCE_QUOTA = "resource_quota"
    SCHEDULING = "scheduling"
    UNKNOWN = "unknown"


# Common Kubernetes error patterns
K8S_STATUS_ERRORS = {
    "ImagePullBackOff": {
        "severity": ErrorSeverity.ERROR,
        "suggestions": [
            "Verify the image name and tag exist in the registry",
            "Check imagePullSecrets are configured correctly",
            "Ensure the registry is accessible from the cluster",
        ],
    },
    "ErrImagePull": {
        "severity": ErrorSeverity.ERROR,
        "suggestions": [
            "Check if the image exists in the registry",
            "Verify registry credentials in imagePullSecrets",
        ],
    },
    "CrashLoopBackOff": {
        "severity": ErrorSeverity.CRITICAL,
        "suggestions": [
            "Check container logs: kubectl logs <pod-name>",
            "Verify environment variables and config",
            "Check if the application has proper health checks",
        ],
    },
    "OOMKilled": {
        "severity": ErrorSeverity.CRITICAL,
        "suggestions": [
            "Increase memory limits in the pod spec",
            "Check for memory leaks in the application",
            "Review memory requests vs actual usage",
        ],
    },
    "CreateContainerConfigError": {
        "severity": ErrorSeverity.ERROR,
        "suggestions": [
            "Check if referenced ConfigMaps exist",
            "Check if referenced Secrets exist",
            "Verify volume mount configurations",
        ],
    },
    "Pending": {
        "severity": ErrorSeverity.WARNING,
        "suggestions": [
            "Check node resources and scheduling constraints",
            "Verify PersistentVolumeClaims are bound",
            "Check node affinity and taints/tolerations",
        ],
    },
    "FailedScheduling": {
        "severity": ErrorSeverity.ERROR,
        "suggestions": [
            "Check node resources (CPU, memory)",
            "Verify node selectors and affinity rules",
            "Check for taints on nodes",
        ],
    },
    "Unhealthy": {
        "severity": ErrorSeverity.WARNING,
        "suggestions": [
            "Check readiness/liveness probe configuration",
            "Verify the application endpoint is responding",
            "Review probe timeout and threshold settings",
        ],
    },
}

# Helm-specific error patterns
HELM_ERROR_PATTERNS = {
    "cannot re-use a name": {
        "error_code": "ReleaseExists",
        "suggestions": [
            "Use 'helm upgrade' instead of 'helm install'",
            "Or use '--replace' flag to replace the existing release",
            "Or delete the existing release first: helm uninstall <release>",
        ],
    },
    "chart.*not found": {
        "error_code": "ChartNotFound",
        "suggestions": [
            "Run 'helm repo update' to refresh chart repositories",
            "Verify the chart name and repository",
        ],
    },
    "timed out waiting": {
        "error_code": "Timeout",
        "suggestions": [
            "Increase timeout with --timeout flag",
            "Check pod status for scheduling or image issues",
            "Review pod events for failure reasons",
        ],
    },
    "UPGRADE FAILED.*has no deployed releases": {
        "error_code": "NoDeployedReleases",
        "suggestions": [
            "Use 'helm install' for new releases",
            "Or use 'helm upgrade --install' to install if not present",
        ],
    },
    "failed pre-install|failed post-install": {
        "error_code": "HookFailed",
        "suggestions": [
            "Check hook job logs for failure details",
            "Verify hook resources have correct permissions",
        ],
    },
    "exceeded quota": {
        "error_code": "QuotaExceeded",
        "suggestions": [
            "Request increased quotas from cluster admin",
            "Reduce resource requests in values",
        ],
    },
    "RBAC|forbidden|cannot create": {
        "error_code": "RBACDenied",
        "suggestions": [
            "Check RBAC permissions for the service account",
            "Ensure ClusterRole/Role bindings are correct",
        ],
    },
}


@dataclass
class KubernetesError(ParsedError):
    """Kubernetes-specific error with additional context."""

    k8s_error_type: KubernetesErrorType = KubernetesErrorType.UNKNOWN
    pod_name: Optional[str] = None
    container_name: Optional[str] = None
    exit_code: Optional[int] = None
    restart_count: Optional[int] = None
    helm_release: Optional[str] = None
    helm_chart: Optional[str] = None

    def __post_init__(self):
        self.error_type = "kubernetes"


class KubernetesParser(ErrorParser):
    """Parser for Kubernetes (kubectl/Helm) errors."""

    @property
    def name(self) -> str:
        return "kubernetes"

    def can_parse(self, text: str) -> bool:
        """Check if text looks like Kubernetes/Helm output."""
        indicators = [
            r"kubectl\s+(apply|create|delete|get|describe)",
            r"helm\s+(install|upgrade|rollback|template)",
            r"Error from server",
            r"error when creating",
            r"INSTALLATION FAILED",
            r"UPGRADE FAILED",
            r"ImagePullBackOff",
            r"CrashLoopBackOff",
            r"OOMKilled",
            r"CreateContainerConfigError",
            r"FailedScheduling",
            r"pod/[a-z0-9-]+",
            r"deployment\.apps/",
            r"service/",
            r"namespace:.*\s+\w+",
            r"kubectl.*-n\s+\w+",
            r"\.yaml.*error",
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in indicators)

    def parse(self, text: str) -> list[KubernetesError]:
        """Parse Kubernetes output for all errors."""
        errors = []

        # Try to parse as Helm error first
        if self._is_helm_output(text):
            helm_errors = self._parse_helm_output(text)
            errors.extend(helm_errors)

        # Parse kubectl errors
        kubectl_errors = self._parse_kubectl_output(text)
        errors.extend(kubectl_errors)

        # Parse pod status errors
        status_errors = self._parse_pod_status(text)
        errors.extend(status_errors)

        # Deduplicate
        seen = set()
        unique = []
        for e in errors:
            key = f"{e.error_code}:{e.resource_name}:{e.error_message[:50]}"
            if key not in seen:
                seen.add(key)
                unique.append(e)

        return unique if unique else [self.parse_single(text)] if self.parse_single(text) else []

    def parse_single(self, text: str) -> Optional[KubernetesError]:
        """Parse a single error from the text."""
        # Try Helm first
        if self._is_helm_output(text):
            helm_errors = self._parse_helm_output(text)
            if helm_errors:
                return helm_errors[0]

        # Try kubectl apply errors
        kubectl_errors = self._parse_kubectl_output(text)
        if kubectl_errors:
            return kubectl_errors[0]

        # Try pod status
        status_errors = self._parse_pod_status(text)
        if status_errors:
            return status_errors[0]

        # Generic fallback
        return self._parse_generic_k8s_error(text)

    def _is_helm_output(self, text: str) -> bool:
        """Check if text is Helm output."""
        helm_patterns = [
            r"helm\s+(install|upgrade|rollback)",
            r"INSTALLATION FAILED",
            r"UPGRADE FAILED",
            r"ROLLBACK FAILED",
            r"Error:\s+chart\s+",
            r'release\s+"[^"]+"\s+',
        ]
        return any(re.search(p, text, re.IGNORECASE) for p in helm_patterns)

    def _parse_helm_output(self, text: str) -> list[KubernetesError]:
        """Parse Helm-specific errors."""
        errors = []

        # Extract release name
        release_match = re.search(r'release\s+"([^"]+)"', text, re.IGNORECASE)
        release_name = release_match.group(1) if release_match else None

        # Extract chart name
        chart_match = re.search(r'chart\s+"([^"]+)"', text, re.IGNORECASE)
        chart_name = chart_match.group(1) if chart_match else None

        # Determine error type
        if "INSTALLATION FAILED" in text:
            k8s_type = KubernetesErrorType.HELM_INSTALL
        elif "UPGRADE FAILED" in text:
            k8s_type = KubernetesErrorType.HELM_UPGRADE
        else:
            k8s_type = KubernetesErrorType.UNKNOWN

        # Extract main error message
        error_match = re.search(r"Error:\s*(.+?)(?=\n\n|\n[A-Z]|\Z)", text, re.DOTALL)
        error_message = error_match.group(1).strip() if error_match else "Helm operation failed"
        error_message = re.sub(r"\s+", " ", error_message)[:500]

        # Determine error code and suggestions
        error_code = None
        suggestions = []
        for pattern, info in HELM_ERROR_PATTERNS.items():
            if re.search(pattern, text, re.IGNORECASE):
                error_code = info["error_code"]
                suggestions = info["suggestions"]
                break

        # Check for underlying Kubernetes errors
        underlying_match = re.search(
            r'(pods?|deployments?|services?|configmaps?|secrets?)\s+"?([^":\s]+)"?\s+is\s+(\w+)',
            text,
            re.IGNORECASE,
        )
        resource_type = None
        resource_name = None
        if underlying_match:
            resource_type = underlying_match.group(1).lower()
            resource_name = underlying_match.group(2)

        # Extract namespace
        namespace_match = re.search(r"-n\s+(\S+)|namespace[=:\s]+(\S+)", text, re.IGNORECASE)
        namespace = (
            namespace_match.group(1) or namespace_match.group(2) if namespace_match else None
        )

        errors.append(
            KubernetesError(
                error_type="kubernetes",
                error_message=error_message,
                raw_output=text,
                resource_type=resource_type or "helm_release",
                resource_name=resource_name or release_name,
                namespace=namespace,
                error_code=error_code,
                severity=ErrorSeverity.ERROR,
                suggestions=suggestions,
                tags=["kubernetes", "helm", k8s_type.value],
                k8s_error_type=k8s_type,
                helm_release=release_name,
                helm_chart=chart_name,
            )
        )

        return errors

    def _parse_kubectl_output(self, text: str) -> list[KubernetesError]:
        """Parse kubectl apply/create errors."""
        errors = []

        # Pattern for "Error from server" style errors
        server_errors = re.findall(
            r"Error from server\s*\((\w+)\):\s*(.+?)(?=Error from server|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )

        for error_type, message in server_errors:
            # Extract resource info
            resource_match = re.search(
                r'(pods?|deployments?|services?|configmaps?|secrets?|statefulsets?)\s+"?([^":\s]+)"?',
                message,
                re.IGNORECASE,
            )
            resource_type = resource_match.group(1) if resource_match else None
            resource_name = resource_match.group(2) if resource_match else None

            # Extract namespace
            ns_match = re.search(r'namespace\s+"?([^":\s]+)"?', message, re.IGNORECASE)
            namespace = ns_match.group(1) if ns_match else None

            # Get suggestions based on error type
            suggestions = self._get_suggestions_for_error(error_type, message)

            errors.append(
                KubernetesError(
                    error_type="kubernetes",
                    error_message=re.sub(r"\s+", " ", message.strip())[:500],
                    raw_output=text,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    namespace=namespace,
                    error_code=error_type,
                    severity=ErrorSeverity.ERROR,
                    suggestions=suggestions,
                    tags=["kubernetes", "kubectl", error_type.lower()],
                    k8s_error_type=KubernetesErrorType.KUBECTL_APPLY,
                )
            )

        # Pattern for "error when creating" style errors
        create_errors = re.findall(
            r'error when (\w+)\s+"([^"]+)":\s*(.+?)(?=error when|\Z)',
            text,
            re.DOTALL | re.IGNORECASE,
        )

        for action, file_or_resource, message in create_errors:
            # Extract resource info from message
            resource_match = re.search(r'(\w+)\.(\w+/\w+)\s+"([^"]+)"', message, re.IGNORECASE)
            if resource_match:
                resource_type = resource_match.group(2).split("/")[0]
                resource_name = resource_match.group(3)
            else:
                resource_type = None
                resource_name = None

            # Extract error reason
            reason_match = re.search(r"is (\w+):", message, re.IGNORECASE)
            error_code = reason_match.group(1) if reason_match else None

            errors.append(
                KubernetesError(
                    error_type="kubernetes",
                    error_message=re.sub(r"\s+", " ", message.strip())[:500],
                    raw_output=text,
                    resource_type=resource_type,
                    resource_name=resource_name,
                    file=(
                        file_or_resource
                        if ".yaml" in file_or_resource or ".yml" in file_or_resource
                        else None
                    ),
                    error_code=error_code,
                    severity=ErrorSeverity.ERROR,
                    tags=["kubernetes", "kubectl", action.lower()],
                    k8s_error_type=KubernetesErrorType.KUBECTL_APPLY,
                )
            )

        return errors

    def _parse_pod_status(self, text: str) -> list[KubernetesError]:
        """Parse pod status errors from kubectl get/describe output."""
        errors = []

        # Check for known status errors
        for status, info in K8S_STATUS_ERRORS.items():
            if status in text:
                # Try to extract pod name
                pod_match = re.search(
                    rf"([a-z0-9][-a-z0-9]*)\s+\d+/\d+\s+{status}", text, re.IGNORECASE
                )
                pod_name = pod_match.group(1) if pod_match else None

                # Alternative: from describe output
                if not pod_name:
                    describe_match = re.search(r"Name:\s+(\S+)", text)
                    pod_name = describe_match.group(1) if describe_match else None

                # Extract namespace
                ns_match = re.search(r"Namespace:\s+(\S+)", text)
                namespace = ns_match.group(1) if ns_match else None

                # Extract container name if present
                container_match = re.search(
                    r"Container:\s+(\S+)|container\s+(\S+)", text, re.IGNORECASE
                )
                container_name = (
                    (container_match.group(1) or container_match.group(2))
                    if container_match
                    else None
                )

                # Extract restart count
                restart_match = re.search(r"Restart Count:\s+(\d+)|RESTARTS\s+.*?(\d+)", text)
                restart_count = (
                    int(restart_match.group(1) or restart_match.group(2)) if restart_match else None
                )

                # Extract exit code for OOMKilled/CrashLoop
                exit_match = re.search(r"Exit Code:\s+(\d+)", text)
                exit_code = int(exit_match.group(1)) if exit_match else None

                # Extract additional context from events
                event_message = ""
                event_match = re.search(
                    rf"(Warning|Error)\s+\w+\s+.*?{status}.*?$", text, re.MULTILINE | re.IGNORECASE
                )
                if event_match:
                    event_message = event_match.group(0)

                error_message = f"Pod status: {status}"
                if event_message:
                    error_message += f" - {event_message}"

                errors.append(
                    KubernetesError(
                        error_type="kubernetes",
                        error_message=error_message[:500],
                        raw_output=text,
                        resource_type="Pod",
                        resource_name=pod_name,
                        namespace=namespace,
                        error_code=status,
                        severity=info["severity"],
                        suggestions=info["suggestions"],
                        tags=["kubernetes", "pod", status.lower()],
                        k8s_error_type=KubernetesErrorType.POD_STATUS,
                        pod_name=pod_name,
                        container_name=container_name,
                        exit_code=exit_code,
                        restart_count=restart_count,
                    )
                )

        return errors

    def _parse_generic_k8s_error(self, text: str) -> Optional[KubernetesError]:
        """Parse a generic Kubernetes error when specific patterns don't match."""
        # Try to find any error message
        error_match = re.search(r"(?:Error|error|ERROR)[:\s]+(.+?)(?=\n\n|\Z)", text, re.DOTALL)

        if not error_match:
            return None

        error_message = re.sub(r"\s+", " ", error_match.group(1).strip())[:500]

        # Try to extract resource info
        resource_match = re.search(
            r"(pods?|deployments?|services?|statefulsets?|daemonsets?|jobs?|cronjobs?)/([^\s:]+)",
            text,
            re.IGNORECASE,
        )
        resource_type = resource_match.group(1) if resource_match else None
        resource_name = resource_match.group(2) if resource_match else None

        # Extract namespace
        ns_match = re.search(r"-n\s+(\S+)|namespace[=:\s]+(\S+)", text, re.IGNORECASE)
        namespace = (ns_match.group(1) or ns_match.group(2)) if ns_match else None

        return KubernetesError(
            error_type="kubernetes",
            error_message=error_message,
            raw_output=text,
            resource_type=resource_type,
            resource_name=resource_name,
            namespace=namespace,
            severity=ErrorSeverity.ERROR,
            tags=["kubernetes"],
            k8s_error_type=KubernetesErrorType.UNKNOWN,
        )

    def _get_suggestions_for_error(self, error_type: str, message: str) -> list[str]:
        """Get suggestions based on error type and message."""
        suggestions = []

        error_lower = error_type.lower()
        message_lower = message.lower()

        if error_lower == "forbidden":
            if "quota" in message_lower:
                suggestions = [
                    "Request increased quotas from cluster admin",
                    "Reduce resource requests in pod spec",
                ]
            elif "service account" in message_lower:
                suggestions = [
                    "Create the service account",
                    "Check serviceAccountName in pod spec",
                ]
            else:
                suggestions = [
                    "Check RBAC permissions",
                    "Verify namespace exists and is accessible",
                ]
        elif error_lower == "invalid":
            if "resource" in message_lower:
                suggestions = [
                    "Check resource requests/limits values",
                    "Ensure requests <= limits",
                ]
            else:
                suggestions = [
                    "Validate YAML syntax",
                    "Check Kubernetes API version compatibility",
                ]
        elif error_lower == "conflict":
            suggestions = [
                "Fetch the latest version and reapply",
                "Use server-side apply: kubectl apply --server-side",
            ]
        elif error_lower == "notfound":
            suggestions = [
                "Check if the resource exists",
                "Verify the namespace is correct",
            ]

        return suggestions


# Convenience functions
def parse_kubernetes_output(output: str) -> list[KubernetesError]:
    """Parse Kubernetes output for all errors."""
    parser = KubernetesParser()
    return parser.parse(output)


def is_kubernetes_output(text: str) -> bool:
    """Check if text looks like Kubernetes output."""
    parser = KubernetesParser()
    return parser.can_parse(text)
