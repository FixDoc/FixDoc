"""Tests for the fixdoc watch command."""

import importlib
import subprocess
from unittest.mock import patch, MagicMock, call

from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.config import FixDocConfig
from fixdoc.models import Fix
from fixdoc.parsers.base import ParsedError, CloudProvider

# Get the actual watch module (not the Click command function)
_watch_mod = importlib.import_module("fixdoc.commands.watch")


def make_obj(tmp_path):
    """Create a ctx.obj dict for test invocations."""
    return {
        "base_path": tmp_path,
        "config": FixDocConfig(),
        "config_manager": MagicMock(),
    }


def mock_popen_success(stdout_lines=None):
    """Create a mock Popen that succeeds with given stdout lines."""
    if stdout_lines is None:
        stdout_lines = [b""]
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout.readline.side_effect = stdout_lines
    mock_proc.wait.return_value = 0
    return mock_proc


def mock_popen_failure(exit_code=1, stdout_lines=None):
    """Create a mock Popen that fails with given exit code and stdout lines."""
    if stdout_lines is None:
        stdout_lines = [b"Error: something went wrong\n", b""]
    mock_proc = MagicMock()
    mock_proc.returncode = exit_code
    mock_proc.stdout.readline.side_effect = stdout_lines
    mock_proc.wait.return_value = exit_code
    return mock_proc


def _make_parsed_error(resource_address="aws_iam_role.app", error_code="AccessDenied", **kwargs):
    """Create a ParsedError for testing."""
    defaults = dict(
        error_type="terraform",
        error_message="access denied",
        raw_output="Error: access denied on resource",
        resource_address=resource_address,
        error_code=error_code,
        cloud_provider=CloudProvider.AWS,
    )
    defaults.update(kwargs)
    return ParsedError(**defaults)


def _make_fix(**kwargs):
    """Create a Fix for testing."""
    defaults = dict(
        issue="aws_iam_role.app: AccessDenied",
        resolution="Added role binding",
        tags="aws,terraform",
    )
    defaults.update(kwargs)
    return Fix(**defaults)


def _patch_store_no_pending(mock_store_cls):
    """Configure a MockStore to return no pending entries on success path."""
    instance = mock_store_cls.return_value
    instance.find_latest_session.return_value = []
    instance.find_by_cwd.return_value = []
    return instance


# ===================================================================
# TestWatchCommandSuccess
# ===================================================================


class TestWatchCommandSuccess:
    """Tests for when the watched command succeeds."""

    def test_successful_command_no_fixdoc_output(self, tmp_path):
        """A successful command produces no extra fixdoc output when no pending."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_success([b"hello world\n", b""])
            _patch_store_no_pending(MockStore)

            result = runner.invoke(cli, ["watch", "--", "echo", "hello"], obj=make_obj(tmp_path))

        assert "deferred error" not in result.output
        assert result.exit_code == 0

    def test_exit_code_zero_preserved(self, tmp_path):
        """Exit code 0 is preserved from the wrapped command."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_success()
            _patch_store_no_pending(MockStore)

            result = runner.invoke(cli, ["watch", "--", "true"], obj=make_obj(tmp_path))

        assert result.exit_code == 0

    def test_success_with_matching_pending_calls_resolve(self, tmp_path):
        """On success, if context-matching pending entries exist, resolve flow is triggered."""
        from fixdoc.pending import PendingEntry

        runner = CliRunner()
        cli = create_cli()
        entry = PendingEntry(
            error_id="abc123",
            error_type="terraform",
            short_message="err",
            error_excerpt="text",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
        )

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve,
        ):
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = [entry]
            instance.find_by_cwd.return_value = []
            mock_resolve.return_value = None

            result = runner.invoke(
                cli,
                ["watch", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
                input="q\n",
            )

        mock_resolve.assert_called_once()

    def test_no_prompt_skips_success_resolve(self, tmp_path):
        """--no-prompt on success skips the resolve flow."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve,
        ):
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = []

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
            )

        mock_resolve.assert_not_called()

    def test_success_no_matching_pending_no_resolve(self, tmp_path):
        """On success with no matching pending, resolve flow is not triggered."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve,
        ):
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = []

            result = runner.invoke(
                cli,
                ["watch", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
            )

        mock_resolve.assert_not_called()


# ===================================================================
# TestWatchCommandFailure — single structured error
# ===================================================================


class TestWatchCommandFailure:
    """Tests for when the watched command fails: defer-first behavior."""

    def test_single_error_auto_defers_shows_summary(self, tmp_path):
        """A failed command with one structured error auto-defers and shows summary card."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "deferred to pending" in result.output
        assert "I'll ask what fixed these" in result.output
        store_instance.save.assert_called_once()

    def test_single_error_auto_defers_stores_entry(self, tmp_path):
        """Auto-defer saves a PendingEntry for the structured error."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        store_instance.save.assert_called_once()

    def test_skip_choice_no_fix_created(self, tmp_path):
        """Choosing 's' (skip) creates no fix."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "Fix saved" not in result.output

    def test_exit_code_preserved_on_skip(self, tmp_path):
        """Non-zero exit code is preserved when skipping."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure(exit_code=42)
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert result.exit_code == 42

    def test_capture_one_now_creates_fix(self, tmp_path):
        """Pressing [c] then selecting an index captures that error immediately."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "capture_single_error", return_value=mock_fix),
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            store_instance = MockStore.return_value

            # [c] to capture one now, then 1 to pick error #1
            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="c\n1\n",
            )

        assert "Fix saved" in result.output
        store_instance.remove.assert_called_once()

    def test_empty_output_no_capture_prompt(self, tmp_path):
        """If command fails but produces no output, no capture prompt."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen") as mp:
            mp.return_value = mock_popen_failure(stdout_lines=[b""])

            result = runner.invoke(
                cli,
                ["watch", "--", "silent-fail"],
                obj=make_obj(tmp_path),
            )

        assert "Deferred to pending" not in result.output
        assert result.exit_code == 1


# ===================================================================
# TestWatchCommandFailureGeneric — no structured errors
# ===================================================================


class TestWatchCommandFailureGeneric:
    """Tests for when the watched command fails with unrecognized output."""

    def test_generic_error_auto_defers_one_entry(self, tmp_path):
        """When detect_and_parse returns [], one generic PendingEntry is auto-deferred."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=[]),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure(
                stdout_lines=[b"some generic error text\n", b""],
            )
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        store_instance.save.assert_called_once()
        saved_entry = store_instance.save.call_args[0][0]
        assert saved_entry.error_type == "generic"
        assert "deferred to pending" in result.output

    def test_generic_capture_via_c_choice(self, tmp_path):
        """Choosing [c] on generic error captures via handle_piped_input."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=[]),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "handle_piped_input", return_value=mock_fix),
        ):
            mp.return_value = mock_popen_failure(
                stdout_lines=[b"some generic error text\n", b""],
            )
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="c\n1\n",
            )

        assert "Fix saved" in result.output

    def test_generic_skip_no_fix(self, tmp_path):
        """Choosing 's' on generic error exits without fix."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=[]),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure(
                stdout_lines=[b"some error\n", b""],
            )
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "Fix saved" not in result.output


# ===================================================================
# TestWatchCommandOptions
# ===================================================================


class TestWatchCommandOptions:
    """Tests for --no-prompt and --tags options."""

    def test_no_prompt_auto_defers_structured_error(self, tmp_path):
        """--no-prompt auto-defers structured errors without interactive prompt."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert "deferred to pending" in result.output.lower()
        assert "Fix saved" not in result.output
        store_instance.save.assert_called_once()

    def test_no_prompt_prints_one_liner_on_failure(self, tmp_path):
        """--no-prompt prints a brief 1-line summary on failure."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert "Apply failed" in result.output
        assert "1 error(s) deferred" in result.output

    def test_no_prompt_multi_error_defers_all(self, tmp_path):
        """--no-prompt with multiple errors defers all to pending."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(resource_address=f"res_{i}", error_code=f"E{i}") for i in range(3)
        ]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = errors
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert store_instance.save.call_count == 3

    def test_tags_stored_in_deferred_entry(self, tmp_path):
        """--tags are stored in the PendingEntry when auto-deferring."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=[]),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure(
                stdout_lines=[b"generic error\n", b""],
            )
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--tags", "aws,terraform", "--no-prompt", "--", "cmd"],
                obj=make_obj(tmp_path),
            )

        store_instance.save.assert_called_once()
        saved_entry = store_instance.save.call_args[0][0]
        assert "aws,terraform" in saved_entry.tags or saved_entry.tags == "aws,terraform"


# ===================================================================
# TestWatchCommandNotFound
# ===================================================================


class TestWatchCommandNotFound:
    """Tests for command-not-found handling."""

    def test_command_not_found(self, tmp_path):
        """Non-existent command prints error and exits 127."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen", side_effect=FileNotFoundError()):
            result = runner.invoke(
                cli,
                ["watch", "--", "nonexistent-command-xyz"],
                obj=make_obj(tmp_path),
            )

        assert "Command not found" in result.output
        assert result.exit_code == 127


# ===================================================================
# TestWatchNoCommand
# ===================================================================


class TestWatchNoCommand:
    """Tests for missing command argument."""

    def test_no_command_shows_error(self, tmp_path):
        """Running watch without a command shows usage error."""
        runner = CliRunner()
        cli = create_cli()

        result = runner.invoke(
            cli,
            ["watch", "--"],
            obj=make_obj(tmp_path),
        )

        assert result.exit_code != 0


# ===================================================================
# TestWatchDeferFirstBehavior — multi-error scenarios
# ===================================================================


class TestWatchDeferFirstBehavior:
    """Tests confirming all errors are auto-deferred on failure."""

    def test_multiple_errors_all_auto_deferred(self, tmp_path):
        """Multiple structured errors are all auto-deferred without prompting."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(resource_address=f"aws_resource_{i}.name", error_code=f"Error{i}")
            for i in range(3)
        ]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert store_instance.save.call_count == 3
        assert "3 error(s)" in result.output

    def test_failure_summary_shows_resource_names(self, tmp_path):
        """Defer summary card lists resources."""
        runner = CliRunner()
        cli = create_cli()
        errors = [_make_parsed_error(resource_address="aws_iam_role.app")]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "aws_iam_role.app" in result.output

    def test_failure_calls_supersede_context_before_save(self, tmp_path):
        """On failure, supersede_context is called before saving new entries."""
        runner = CliRunner()
        cli = create_cli()
        errors = [_make_parsed_error()]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure()
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        store_instance.supersede_context.assert_called_once()
        # supersede_context must be called before any save
        supersede_call_idx = store_instance.method_calls.index(
            next(c for c in store_instance.method_calls if c[0] == "supersede_context")
        )
        save_call_idx = store_instance.method_calls.index(
            next(c for c in store_instance.method_calls if c[0] == "save")
        )
        assert supersede_call_idx < save_call_idx

    def test_capture_one_removes_entry_from_store(self, tmp_path):
        """After capturing with [c], the entry is removed from the store."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix()
        errors = [_make_parsed_error()]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "capture_single_error", return_value=mock_fix),
        ):
            mp.return_value = mock_popen_failure()
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="c\n1\n",
            )

        store_instance.remove.assert_called_once()


# ===================================================================
# TestCaptureErrorForWatch — unit tests
# ===================================================================


class TestCaptureErrorForWatch:
    """Unit tests for _capture_error_for_watch routing."""

    def test_terraform_routes_to_capture_single_error(self):
        err = _make_parsed_error(error_type="terraform")
        mock_fix = _make_fix()

        with (
            patch.object(_watch_mod, "capture_single_error", return_value=mock_fix) as mock_cap,
            patch.object(_watch_mod, "capture_single_k8s_error") as mock_k8s,
        ):
            result = _watch_mod._capture_error_for_watch(err, "tag", MagicMock(), MagicMock())

        mock_cap.assert_called_once()
        mock_k8s.assert_not_called()
        assert result == mock_fix

    def test_kubernetes_routes_to_k8s_capture(self):
        err = _make_parsed_error(error_type="kubectl")

        with (
            patch.object(_watch_mod, "capture_single_error") as mock_tf,
            patch.object(_watch_mod, "capture_single_k8s_error", return_value=None) as mock_k8s,
        ):
            result = _watch_mod._capture_error_for_watch(err, None, MagicMock(), MagicMock())

        mock_k8s.assert_called_once()
        mock_tf.assert_not_called()


# ===================================================================
# TestWatchFixSurfacing — Feature 1
# ===================================================================


class TestWatchFixSurfacing:
    """Tests for surfacing known fixes on watch failure."""

    def test_failure_shows_known_fixes(self, tmp_path):
        """When find_similar_fixes returns matches, 'Known fixes' is shown."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix(resolution="Added role binding for service account")

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[mock_fix]),
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "Known fixes that may help:" in result.output
        assert "Added role binding" in result.output

    def test_failure_no_fixes_when_repo_empty(self, tmp_path):
        """When find_similar_fixes returns [], 'Known fixes' is NOT shown."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[]),
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "Known fixes" not in result.output

    def test_no_prompt_still_shows_fixes(self, tmp_path):
        """--no-prompt flag still shows fix suggestions."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix(resolution="Add random suffix to bucket name")

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[mock_fix]),
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert "Known fixes that may help:" in result.output

    def test_max_two_fixes_per_error(self, tmp_path):
        """Only up to 2 fixes per error are shown (limit_per_error default)."""
        fixes = [_make_fix(resolution=f"Fix {i}") for i in range(5)]
        # find_similar_fixes will be called with limit=2, so it returns at most 2
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=fixes[:2]) as mock_fsf,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        # Verify limit=2 was passed
        assert (
            mock_fsf.call_args[1].get("limit") == 2 or mock_fsf.call_args[0][3]
            if len(mock_fsf.call_args[0]) > 3
            else mock_fsf.call_args[1].get("limit") == 2
        )

    def test_fix_dedup_across_errors(self, tmp_path):
        """Same fix matching 2 errors is shown only once."""
        shared_fix = _make_fix(resolution="Shared fix across errors")
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[shared_fix]),
        ):
            mp.return_value = mock_popen_failure()
            # Two different errors
            mock_parse.return_value = [
                _make_parsed_error(resource_address="aws_iam_role.a"),
                _make_parsed_error(resource_address="aws_iam_role.b"),
            ]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        # The fix resolution should appear exactly once
        assert result.output.count("Shared fix across errors") == 1

    def test_correct_args_to_find_similar(self, tmp_path):
        """Verify entry fields are passed correctly to find_similar_fixes."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[]) as mock_fsf,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [
                _make_parsed_error(
                    resource_address="aws_iam_role.app",
                    error_code="AccessDenied",
                )
            ]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        mock_fsf.assert_called_once()
        call_kwargs = mock_fsf.call_args
        # Verify resource_address was passed
        assert call_kwargs[1].get("resource_address") is not None

    def test_fix_surfacing_passes_error_id(self, tmp_path):
        """Verify error_id from pending entry is passed to find_similar_fixes."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse") as mock_parse,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "find_similar_fixes", return_value=[]) as mock_fsf,
        ):
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        mock_fsf.assert_called_once()
        assert mock_fsf.call_args[1].get("error_id") is not None

    def test_source_error_id_fix_ranked_first(self, tmp_path):
        """A fix with matching source_error_ids scores higher."""
        from fixdoc.suggestions import find_similar_fixes
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        # Create two fixes — one with source_error_ids, one without
        fix_with_id = Fix(
            issue="AccessDenied on aws_iam_role.app",
            resolution="Added role binding",
            tags="aws_iam_role,terraform",
            source_error_ids=["target_error_123"],
        )
        fix_without = Fix(
            issue="AccessDenied on aws_iam_role.app",
            resolution="Different fix",
            tags="aws_iam_role,terraform",
        )
        repo.save(fix_with_id)
        repo.save(fix_without)

        results = find_similar_fixes(
            repo,
            "Error: access denied on aws_iam_role.app",
            tags="aws_iam_role",
            error_id="target_error_123",
        )

        assert len(results) >= 1
        assert results[0].id == fix_with_id.id


# ===================================================================
# TestWatchEffectivenessTracking — Fix Effectiveness
# ===================================================================


# ===================================================================
# TestWatchApplyCancelled
# ===================================================================


class TestWatchApplyCancelled:
    """Tests for 'Apply cancelled' not being treated as an error."""

    def test_apply_cancelled_not_deferred(self, tmp_path):
        """When terraform apply is cancelled (user says no), nothing is deferred."""
        runner = CliRunner()
        cli = create_cli()
        cancelled_output = (
            b"Plan: 1 to add, 0 to change, 0 to destroy.\n"
            b"\n"
            b"Do you want to perform these actions?\n"
            b"  Terraform will perform the actions described above.\n"
            b"  Only 'yes' will be accepted to approve.\n"
            b"\n"
            b"  Enter a value: no\n"
            b"\n"
            b"Apply cancelled.\n"
        )

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            mp.return_value = mock_popen_failure(
                stdout_lines=list(cancelled_output.split(b"\n")[:-1]) + [b""],
            )
            # Make each line end with \n for readline simulation
            lines = [line + b"\n" for line in cancelled_output.split(b"\n") if line] + [b""]
            mp.return_value.stdout.readline.side_effect = lines
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
            )

        # Should NOT defer any entries
        store_instance.save.assert_not_called()
        assert "Deferred to pending" not in result.output

    def test_apply_cancelled_no_prompt_not_deferred(self, tmp_path):
        """With --no-prompt, cancelled apply also skips deferral."""
        runner = CliRunner()
        cli = create_cli()

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
        ):
            lines = [
                b"Plan: 2 to add, 0 to change, 0 to destroy.\n",
                b"Apply cancelled.\n",
                b"",
            ]
            mp.return_value = mock_popen_failure(stdout_lines=lines)
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
            )

        store_instance.save.assert_not_called()
        assert "error(s) deferred" not in result.output


# ===================================================================
# TestWatchEffectivenessTracking
# ===================================================================


class TestWatchEffectivenessTracking:
    """Tests for effectiveness tracking helper functions."""

    def test_success_increments_applied_and_success(self, tmp_path):
        """_track_effectiveness_success increments both counters for linked fixes."""
        from fixdoc.pending import PendingEntry
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="AccessDenied",
            resolution="Added binding",
            source_error_ids=["err_abc"],
        )
        repo.save(fix)

        entry = PendingEntry(
            error_id="err_abc",
            error_type="terraform",
            short_message="err",
            error_excerpt="text",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
        )

        _watch_mod._track_effectiveness_success([entry], repo)

        updated = repo.get(fix.id)
        assert updated.applied_count == 1
        assert updated.success_count == 1
        assert updated.last_applied_at is not None

    def test_failure_increments_applied_not_success(self, tmp_path):
        """_track_effectiveness_failure increments only applied_count."""
        from fixdoc.pending import PendingEntry
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="AccessDenied",
            resolution="Added binding",
            source_error_ids=["err_recurring"],
        )
        repo.save(fix)

        entry = PendingEntry(
            error_id="err_recurring",
            error_type="terraform",
            short_message="access denied",
            error_excerpt="Error: access denied",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
        )

        _watch_mod._track_effectiveness_failure([entry], repo)

        updated = repo.get(fix.id)
        assert updated.applied_count == 1
        assert updated.success_count == 0
        assert updated.last_applied_at is not None

    def test_no_linked_fixes_no_tracking(self, tmp_path):
        """When no fixes have matching source_error_ids, nothing changes."""
        from fixdoc.pending import PendingEntry
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="Unrelated fix",
            resolution="something else",
            source_error_ids=["other_error"],
        )
        repo.save(fix)

        entry = PendingEntry(
            error_id="different_error",
            error_type="terraform",
            short_message="err",
            error_excerpt="text",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
        )

        _watch_mod._track_effectiveness_success([entry], repo)

        updated = repo.get(fix.id)
        assert updated.applied_count == 0
        assert updated.success_count == 0


# ===================================================================
# TestWatchClassifierIntegration
# ===================================================================


class TestWatchClassifierIntegration:
    """Tests for memory-worthiness classifier integration in watch."""

    def test_self_explanatory_hidden_in_interactive_summary(self, tmp_path):
        """Self-explanatory errors show collapsed count, not individual entries."""
        runner = CliRunner()
        cli = create_cli()
        # MissingRequiredArgument on a terraform_config kind -> self_explanatory
        errors = [
            _make_parsed_error(
                resource_address="variable.foo",
                error_code="MissingRequiredVariable",
            )
        ]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "classify_entry", return_value="self_explanatory"),
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert "self-explanatory" in result.output
        # No capture prompt since all errors are self-explanatory
        assert "[c] capture one now" not in result.output

    def test_memory_worthy_shown_in_numbered_list(self, tmp_path):
        """Memory-worthy errors appear in the numbered list."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(
                resource_address="aws_iam_role.app",
                error_code="AccessDenied",
            )
        ]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "classify_entry", return_value="memory_worthy"),
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "deferred to pending" in result.output
        assert "aws_iam_role.app" in result.output
        assert "[c] capture one now" in result.output

    def test_mixed_errors_both_sections(self, tmp_path):
        """Mixed errors show both numbered list and collapsed count."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(resource_address="aws_iam_role.app", error_code="AccessDenied"),
            _make_parsed_error(
                resource_address="variable.foo", error_code="MissingRequiredVariable"
            ),
        ]
        # Classify first as memory_worthy, second as self_explanatory
        classify_results = iter(["memory_worthy", "self_explanatory"])

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "classify_entry", side_effect=classify_results),
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "1 deferred to pending" in result.output
        assert "1 self-explanatory" in result.output

    def test_no_prompt_self_explanatory_count(self, tmp_path):
        """--no-prompt shows self-explanatory count line."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(
                resource_address="variable.foo",
                error_code="MissingRequiredVariable",
            )
        ]

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "classify_entry", return_value="self_explanatory"),
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        assert "self-explanatory" in result.output

    def test_fix_suggestions_only_for_memory_worthy(self, tmp_path):
        """Fix suggestions are only searched for memory-worthy entries."""
        runner = CliRunner()
        cli = create_cli()
        errors = [
            _make_parsed_error(resource_address="aws_iam_role.app", error_code="AccessDenied"),
            _make_parsed_error(
                resource_address="variable.foo", error_code="MissingRequiredVariable"
            ),
        ]
        classify_results = iter(["memory_worthy", "self_explanatory"])

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "detect_and_parse", return_value=errors),
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "classify_entry", side_effect=classify_results),
            patch.object(_watch_mod, "find_similar_fixes", return_value=[]) as mock_fsf,
        ):
            mp.return_value = mock_popen_failure()
            MockStore.return_value = MagicMock()

            result = runner.invoke(
                cli,
                ["watch", "--no-prompt", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
            )

        # find_similar_fixes should only be called for memory_worthy entry
        assert mock_fsf.call_count == 1

    def test_success_auto_resolves_self_explanatory(self, tmp_path):
        """Success path auto-resolves self-explanatory entries from same session."""
        from fixdoc.pending import PendingEntry

        runner = CliRunner()
        cli = create_cli()

        mw_entry = PendingEntry(
            error_id="mw1",
            error_type="terraform",
            short_message="access denied",
            error_excerpt="text",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
            session_id="abc123",
            worthiness="memory_worthy",
        )
        se_entry = PendingEntry(
            error_id="se1",
            error_type="terraform",
            short_message="missing var",
            error_excerpt="text",
            tags="",
            cwd="/some/dir",
            command="terraform apply",
            session_id="abc123",
            worthiness="self_explanatory",
        )

        with (
            patch.object(_watch_mod.subprocess, "Popen") as mp,
            patch.object(_watch_mod, "PendingStore") as MockStore,
            patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve,
        ):
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            # First call (default): returns memory-worthy only
            # Second call (include_self_explanatory=True): returns self-explanatory
            instance.find_latest_session.side_effect = [
                [mw_entry],  # default call
                [se_entry],  # include_self_explanatory=True call
            ]
            instance.find_by_cwd.return_value = []
            mock_resolve.return_value = None

            result = runner.invoke(
                cli,
                ["watch", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
                input="q\n",
            )

        # Verify self-explanatory entry was removed
        instance.remove.assert_called_once_with("se1")


# ===================================================================
# TestWatchFixSuggestionTypes — Memory Types Phase 2
# ===================================================================


class TestWatchFixSuggestionTypes:
    """Tests for type-aware suggestion rendering in watch."""

    def test_fix_type_renders_plain_preview(self, tmp_path):
        """Fix type renders plain resolution preview (backward compatible)."""
        from fixdoc.rendering import format_suggestion_preview

        fix = Fix(issue="test", resolution="Added IAM binding", memory_type="fix")
        preview = format_suggestion_preview(fix)
        assert preview == "Added IAM binding"
        assert not preview.startswith("Verify:")
        assert not preview.startswith("Context:")

    def test_check_type_renders_verify_prefix(self, tmp_path):
        """Check type renders 'Verify: ' prefix."""
        from fixdoc.rendering import format_suggestion_preview

        fix = Fix(issue="test", resolution="Ensure SG rules allow port 443", memory_type="check")
        preview = format_suggestion_preview(fix)
        assert preview.startswith("Verify: ")
        assert "Ensure" not in preview  # Stutter prevention

    def test_playbook_type_renders_step_count(self, tmp_path):
        """Playbook type renders step count and first step."""
        from fixdoc.rendering import format_suggestion_preview

        resolution = "1. Stop service\n2. Update config\n3. Restart"
        fix = Fix(issue="test", resolution=resolution, memory_type="playbook")
        preview = format_suggestion_preview(fix)
        assert "Playbook (3 steps):" in preview
        assert "Stop service" in preview

    def test_insight_type_renders_context_prefix(self, tmp_path):
        """Insight type renders 'Context: ' prefix."""
        from fixdoc.rendering import format_suggestion_preview

        fix = Fix(issue="test", resolution="Root cause was drift", memory_type="insight")
        preview = format_suggestion_preview(fix)
        assert preview.startswith("Context: ")

    def test_backward_compat_default_memory_type(self, tmp_path):
        """Fixes with default memory_type='fix' render exactly as before."""
        from fixdoc.rendering import format_suggestion_preview

        fix = Fix(issue="test", resolution="A" * 100)
        assert fix.memory_type == "fix"
        preview = format_suggestion_preview(fix)
        assert preview == "A" * 60 + "..."


# ===================================================================
# TestClassifyAndConfirm — Capture integration
# ===================================================================


class TestClassifyAndConfirm:
    """Tests for _classify_and_confirm in capture_handlers."""

    def test_skips_prompt_for_fix_type(self):
        """Auto-classification as 'fix' skips the override prompt."""
        import importlib

        _ch_mod = importlib.import_module("fixdoc.commands.capture_handlers")
        with (
            patch.object(_ch_mod.click, "prompt") as mock_prompt,
            patch.object(_ch_mod.click, "echo"),
        ):
            result = _ch_mod._classify_and_confirm("Added IAM role binding")
        assert result == "fix"
        mock_prompt.assert_not_called()

    def test_shows_prompt_for_non_fix_type(self):
        """Non-fix detected type shows override prompt."""
        import importlib

        _ch_mod = importlib.import_module("fixdoc.commands.capture_handlers")
        with (
            patch.object(_ch_mod.click, "prompt", return_value="check") as mock_prompt,
            patch.object(_ch_mod.click, "echo"),
        ):
            result = _ch_mod._classify_and_confirm("Verify the IAM roles are correct")
        assert result == "check"
        mock_prompt.assert_called_once()

    def test_shorthand_accepted(self):
        """Shorthand 'p' is accepted as 'playbook'."""
        import importlib

        _ch_mod = importlib.import_module("fixdoc.commands.capture_handlers")
        with (
            patch.object(_ch_mod.click, "prompt", return_value="p") as mock_prompt,
            patch.object(_ch_mod.click, "echo"),
        ):
            result = _ch_mod._classify_and_confirm("Verify the IAM roles")
        assert result == "playbook"
