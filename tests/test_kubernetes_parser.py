"""Comprehensive tests for the Kubernetes error parser."""

import pytest
from pathlib import Path

from fixdoc.parsers.kubernetes import (
    KubernetesParser,
    KubernetesError,
    KubernetesErrorType,
)
from fixdoc.parsers.base import ErrorSeverity

# Get the fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "kubernetes"


class TestKubernetesParserDetection:
    """Tests for error source detection."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_detects_kubectl_apply_error(self):
        text = "Error from server (Forbidden): error when creating"
        assert self.parser.can_parse(text) is True

    def test_detects_kubectl_output(self):
        text = "kubectl apply -f deployment.yaml"
        assert self.parser.can_parse(text) is True

    def test_detects_helm_install(self):
        text = "helm install myapp ./charts/myapp"
        assert self.parser.can_parse(text) is True

    def test_detects_helm_installation_failed(self):
        text = "Error: INSTALLATION FAILED: cannot re-use a name"
        assert self.parser.can_parse(text) is True

    def test_detects_image_pull_back_off(self):
        text = "backend-5d8f9c7b6-x2k9m   0/1     ImagePullBackOff   0          45s"
        assert self.parser.can_parse(text) is True

    def test_detects_crash_loop_back_off(self):
        text = "worker-6b8f7c9d5-p3k8n   0/1     CrashLoopBackOff   5"
        assert self.parser.can_parse(text) is True

    def test_does_not_detect_terraform_output(self):
        text = """
        │ Error: creating Storage Account
        │   with azurerm_storage_account.main
        """
        assert self.parser.can_parse(text) is False

    def test_does_not_detect_random_text(self):
        text = "Hello, this is just random text."
        assert self.parser.can_parse(text) is False


class TestKubectlApplyErrors:
    """Tests for kubectl apply error parsing."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_parse_forbidden_quota_exceeded(self):
        text = """
        Error from server (Forbidden): error when creating "deployment.yaml":
        pods "api-server-7d8f9b6c5-" is forbidden: exceeded quota: compute-resources,
        requested: requests.cpu=2, used: requests.cpu=7500m, limited: requests.cpu=8
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "Forbidden"
        assert "quota" in error.error_message.lower()

    def test_parse_service_account_not_found(self):
        text = """
        Error from server (Forbidden): error when creating "deployment.yaml":
        pods "api-7d8f9b6c5-" is forbidden: error looking up service account
        prod/nonexistent-sa: serviceaccount "nonexistent-sa" not found
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert (
            "serviceaccount" in error.error_message.lower()
            or "service account" in error.error_message.lower()
        )

    def test_parse_yaml_syntax_error(self):
        text = """
        error: error parsing broken-deployment.yaml: error converting YAML to JSON:
        yaml: line 15: did not find expected key
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert "yaml" in error.error_message.lower()

    def test_parse_resource_conflict(self):
        text = """
        Error from server (Conflict): error when applying patch:
        Operation cannot be fulfilled on deployments.apps "api-server":
        the object has been modified; please apply your changes to the latest version
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "Conflict"

    def test_parse_namespace_terminating(self):
        text = """
        Error from server (Forbidden): error when creating "deployment.yaml":
        deployments.apps "api-server" is forbidden: unable to create new content
        in namespace legacy-app because it is being terminated
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        # The message contains "terminated" not "terminating"
        assert "terminat" in error.error_message.lower()  # Matches both terminated/terminating


class TestPodStatusErrors:
    """Tests for pod status error parsing."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_parse_image_pull_back_off(self):
        text = """
        NAME                       READY   STATUS             RESTARTS   AGE
        backend-5d8f9c7b6-x2k9m   0/1     ImagePullBackOff   0          45s

        Events:
        Warning  Failed     57s   kubelet   Failed to pull image "myregistry.azurecr.io/backend:v1.2.3-nonexistent"
        Warning  Failed     57s   kubelet   Error: ImagePullBackOff
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "ImagePullBackOff"
        assert error.severity == ErrorSeverity.ERROR
        assert len(error.suggestions) > 0

    def test_parse_crash_loop_back_off(self):
        text = """
        NAME                      READY   STATUS             RESTARTS      AGE
        worker-6b8f7c9d5-p3k8n   0/1     CrashLoopBackOff   5 (32s ago)   4m12s

        Events:
        Warning  BackOff   2m54s   kubelet   Back-off restarting failed container worker
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "CrashLoopBackOff"
        assert error.severity == ErrorSeverity.CRITICAL

    def test_parse_oom_killed(self):
        text = """
        Containers:
          processor:
            State:          Terminated
              Reason:       OOMKilled
              Exit Code:    137
            Restart Count:  3
            Limits:
              memory:  512Mi

        Events:
        Warning  OOMKilling   45s   kubelet   Memory cgroup out of memory
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "OOMKilled"
        assert error.severity == ErrorSeverity.CRITICAL
        assert error.exit_code == 137

    def test_parse_create_container_config_error(self):
        text = """
        NAME                          READY   STATUS                       RESTARTS   AGE
        api-server-5d8f9c7b6-m2n3o   0/1     CreateContainerConfigError   0          30s

        Events:
        Warning  Failed     5s   kubelet   Error: configmap "app-config" not found
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "CreateContainerConfigError"

    def test_parse_failed_scheduling(self):
        text = """
        NAME                           READY   STATUS    RESTARTS   AGE
        gpu-workload-7c9d8e5f4-k2l3m   0/1     Pending   0          5m

        Events:
        Warning  FailedScheduling   2m15s   default-scheduler   0/5 nodes are available:
        5 node(s) didn't match Pod's node affinity/selector
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        # Should parse either Pending or FailedScheduling
        assert error.error_code in ("Pending", "FailedScheduling")

    def test_parse_readiness_probe_failing(self):
        text = """
        Events:
        Warning  Unhealthy   10s   kubelet   Readiness probe failed: HTTP probe failed with statuscode: 503
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "Unhealthy"


class TestHelmErrors:
    """Tests for Helm error parsing."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_parse_release_already_exists(self):
        text = """
        $ helm install myapp ./charts/myapp -n production

        Error: INSTALLATION FAILED: cannot re-use a name that is still in use
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "ReleaseExists"
        assert error.k8s_error_type == KubernetesErrorType.HELM_INSTALL
        assert len(error.suggestions) > 0

    def test_parse_chart_not_found(self):
        text = """
        $ helm install myapp bitnami/nonexistent-chart

        Error: INSTALLATION FAILED: chart "nonexistent-chart" not found in
        https://charts.bitnami.com/bitnami repository
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "ChartNotFound"

    def test_parse_upgrade_failed_no_releases(self):
        text = """
        $ helm upgrade myapp ./charts/myapp -n production

        Error: UPGRADE FAILED: "myapp" has no deployed releases
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "NoDeployedReleases"
        assert error.k8s_error_type == KubernetesErrorType.HELM_UPGRADE

    def test_parse_helm_timeout(self):
        text = """
        $ helm install myapp ./charts/myapp --wait --timeout 5m

        Error: INSTALLATION FAILED: timed out waiting for the condition
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "Timeout"

    def test_parse_helm_rbac_denied(self):
        text = """
        $ helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring

        Error: INSTALLATION FAILED: failed to create resource: clusterroles.rbac.authorization.k8s.io
        is forbidden: User "system:serviceaccount:default:helm" cannot create resource "clusterroles"
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "RBACDenied"

    def test_parse_helm_hook_failed(self):
        text = """
        $ helm install myapp ./charts/myapp

        Error: INSTALLATION FAILED: failed pre-install: job failed: BackoffLimitExceeded
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.error_code == "HookFailed"

    def test_extracts_helm_release_name(self):
        text = """
        Error: INSTALLATION FAILED: release "myapp-prod" failed
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.helm_release == "myapp-prod"


class TestSuggestions:
    """Tests for suggestion generation."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_image_pull_suggestions(self):
        text = "ImagePullBackOff"
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        suggestions = errors[0].suggestions
        assert len(suggestions) > 0
        assert any("image" in s.lower() or "registry" in s.lower() for s in suggestions)

    def test_crash_loop_suggestions(self):
        text = "CrashLoopBackOff"
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        suggestions = errors[0].suggestions
        assert len(suggestions) > 0
        assert any("logs" in s.lower() for s in suggestions)

    def test_oom_killed_suggestions(self):
        text = "OOMKilled"
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        suggestions = errors[0].suggestions
        assert len(suggestions) > 0
        assert any("memory" in s.lower() for s in suggestions)


class TestTagGeneration:
    """Tests for automatic tag generation."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_generates_kubernetes_tag(self):
        text = "Error from server (Forbidden): error when creating"
        errors = self.parser.parse(text)

        if errors:
            tags = errors[0].generate_tags()
            assert "kubernetes" in tags

    def test_generates_helm_tag(self):
        text = "Error: INSTALLATION FAILED: cannot re-use"
        errors = self.parser.parse(text)

        if errors:
            tags = errors[0].generate_tags()
            assert "helm" in tags


class TestFixtureFiles:
    """Tests using the comprehensive fixture files."""

    def setup_method(self):
        self.parser = KubernetesParser()

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_kubectl_apply_errors(self):
        fixture_file = FIXTURES_DIR / "kubectl" / "apply_errors.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        parsed_count = 0
        for i, case in enumerate(test_cases):
            if (
                "Error" not in case
                and "ImagePullBackOff" not in case
                and "CrashLoopBackOff" not in case
            ):
                continue

            errors = self.parser.parse(case)
            if errors:
                parsed_count += 1

        # Should parse at least some of the test cases
        assert parsed_count > 0, "Should parse at least some kubectl errors"

    @pytest.mark.skipif(not FIXTURES_DIR.exists(), reason="Fixtures not found")
    def test_parse_helm_errors(self):
        fixture_file = FIXTURES_DIR / "helm" / "helm_errors.txt"
        if not fixture_file.exists():
            pytest.skip("Fixture file not found")

        content = fixture_file.read_text()
        test_cases = content.split("---FIXTURE_SEPARATOR---")

        parsed_count = 0
        for i, case in enumerate(test_cases):
            if "Error:" not in case and "FAILED" not in case:
                continue

            errors = self.parser.parse(case)
            if errors:
                parsed_count += 1

        # Should parse at least some of the test cases
        assert parsed_count > 0, "Should parse at least some Helm errors"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def setup_method(self):
        self.parser = KubernetesParser()

    def test_handles_empty_input(self):
        errors = self.parser.parse("")
        assert errors == []

    def test_handles_successful_output(self):
        text = """
        deployment.apps/api-server created
        service/api-server created
        """
        errors = self.parser.parse(text)
        assert errors == []

    def test_handles_unicode_in_error(self):
        text = """
        Error from server (Forbidden): 日本語エラー
        """
        errors = self.parser.parse(text)
        assert len(errors) >= 1

    def test_extracts_namespace(self):
        text = """
        kubectl apply -f deployment.yaml -n production
        Error from server (Forbidden): error in namespace production
        """
        errors = self.parser.parse(text)

        if errors:
            # Should extract namespace
            assert errors[0].namespace == "production" or "production" in str(errors[0])

    def test_extracts_pod_name_from_status(self):
        text = """
        NAME                       READY   STATUS             RESTARTS   AGE
        my-pod-abc123-xyz456      0/1     ImagePullBackOff   0          45s
        """
        errors = self.parser.parse(text)

        assert len(errors) >= 1
        error = errors[0]
        assert error.pod_name == "my-pod-abc123-xyz456"
