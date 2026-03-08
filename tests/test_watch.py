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


def _make_parsed_error(resource_address="aws_iam_role.app",
                       error_code="AccessDenied", **kwargs):
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
            mp.return_value = mock_popen_success([b"hello world\n", b""])
            _patch_store_no_pending(MockStore)

            result = runner.invoke(
                cli, ["watch", "--", "echo", "hello"], obj=make_obj(tmp_path)
            )

        assert "deferred error" not in result.output
        assert result.exit_code == 0

    def test_exit_code_zero_preserved(self, tmp_path):
        """Exit code 0 is preserved from the wrapped command."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
            mp.return_value = mock_popen_success()
            _patch_store_no_pending(MockStore)

            result = runner.invoke(
                cli, ["watch", "--", "true"], obj=make_obj(tmp_path)
            )

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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve:
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = [entry]
            instance.find_by_cwd.return_value = []
            mock_resolve.return_value = None

            result = runner.invoke(
                cli, ["watch", "--", "terraform", "apply"], obj=make_obj(tmp_path),
                input="q\n",
            )

        mock_resolve.assert_called_once()

    def test_no_prompt_skips_success_resolve(self, tmp_path):
        """--no-prompt on success skips the resolve flow."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve:
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = []

            result = runner.invoke(
                cli, ["watch", "--no-prompt", "--", "terraform", "apply"],
                obj=make_obj(tmp_path),
            )

        mock_resolve.assert_not_called()

    def test_success_no_matching_pending_no_resolve(self, tmp_path):
        """On success with no matching pending, resolve flow is not triggered."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "resolve_pending_entries") as mock_resolve:
            mp.return_value = mock_popen_success()
            instance = MockStore.return_value
            instance.find_latest_session.return_value = []

            result = runner.invoke(
                cli, ["watch", "--", "terraform", "apply"], obj=make_obj(tmp_path),
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
            mp.return_value = mock_popen_failure()
            mock_parse.return_value = [_make_parsed_error()]
            store_instance = MockStore.return_value

            result = runner.invoke(
                cli,
                ["watch", "--", "failing-cmd"],
                obj=make_obj(tmp_path),
                input="s\n",
            )

        assert "Deferred to pending" in result.output
        assert "I'll ask what fixed these" in result.output
        store_instance.save.assert_called_once()

    def test_single_error_auto_defers_stores_entry(self, tmp_path):
        """Auto-defer saves a PendingEntry for the structured error."""
        runner = CliRunner()
        cli = create_cli()

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "capture_single_error", return_value=mock_fix):
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=[]), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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
        assert "Deferred to pending" in result.output

    def test_generic_capture_via_c_choice(self, tmp_path):
        """Choosing [c] on generic error captures via handle_piped_input."""
        runner = CliRunner()
        cli = create_cli()
        mock_fix = _make_fix()

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=[]), \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "handle_piped_input", return_value=mock_fix):
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=[]), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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
        errors = [_make_parsed_error(resource_address=f"res_{i}", error_code=f"E{i}") for i in range(3)]

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse") as mock_parse, \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=[]), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(
            _watch_mod.subprocess, "Popen", side_effect=FileNotFoundError()
        ):
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=errors), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=errors), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=errors), \
             patch.object(_watch_mod, "PendingStore") as MockStore:
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

        with patch.object(_watch_mod.subprocess, "Popen") as mp, \
             patch.object(_watch_mod, "detect_and_parse", return_value=errors), \
             patch.object(_watch_mod, "PendingStore") as MockStore, \
             patch.object(_watch_mod, "capture_single_error", return_value=mock_fix):
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

        with patch.object(_watch_mod, "capture_single_error", return_value=mock_fix) as mock_cap, \
             patch.object(_watch_mod, "capture_single_k8s_error") as mock_k8s:
            result = _watch_mod._capture_error_for_watch(err, "tag", MagicMock(), MagicMock())

        mock_cap.assert_called_once()
        mock_k8s.assert_not_called()
        assert result == mock_fix

    def test_kubernetes_routes_to_k8s_capture(self):
        err = _make_parsed_error(error_type="kubectl")

        with patch.object(_watch_mod, "capture_single_error") as mock_tf, \
             patch.object(_watch_mod, "capture_single_k8s_error", return_value=None) as mock_k8s:
            result = _watch_mod._capture_error_for_watch(err, None, MagicMock(), MagicMock())

        mock_k8s.assert_called_once()
        mock_tf.assert_not_called()
