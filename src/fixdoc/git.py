"""Git operations wrapper for fixdoc sync."""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple


class GitError(Exception):
    """Raised when a git operation fails."""

    def __init__(self, message: str, stderr: str = ""):
        self.message = message
        self.stderr = stderr
        super().__init__(f"{message}: {stderr}" if stderr else message)


class SyncStatus(Enum):
    """Status of local repo relative to remote."""

    UP_TO_DATE = "up_to_date"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    NOT_INITIALIZED = "not_initialized"
    NO_REMOTE = "no_remote"


@dataclass
class GitStatusInfo:
    """Detailed git status information."""

    status: SyncStatus
    commits_ahead: int = 0
    commits_behind: int = 0
    local_changes: list[str] = field(default_factory=list)


class GitOperations:
    """Encapsulates git subprocess operations."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def _run(
        self, *args: str, check: bool = True, capture_output: bool = True
    ) -> subprocess.CompletedProcess:
        """Execute a git command in the repo directory."""
        cmd = ["git"] + list(args)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=capture_output,
                text=True,
                check=False,
            )
            if check and result.returncode != 0:
                raise GitError(f"Git command failed: {' '.join(cmd)}", result.stderr)
            return result
        except FileNotFoundError:
            raise GitError("Git is not installed or not in PATH")

    def is_git_repo(self) -> bool:
        """Check if directory is a git repository."""
        result = self._run("rev-parse", "--git-dir", check=False)
        return result.returncode == 0

    def init(self) -> None:
        """Initialize a new git repository."""
        self._run("init")

    def clone(self, url: str, branch: str = "main") -> None:
        """Clone a remote repository into the repo path."""
        parent = self.repo_path.parent
        name = self.repo_path.name
        subprocess.run(
            ["git", "clone", "-b", branch, url, name],
            cwd=parent,
            capture_output=True,
            text=True,
            check=True,
        )

    def add(self, *paths: str) -> None:
        """Stage files for commit."""
        if not paths:
            return
        self._run("add", *paths)

    def add_all(self) -> None:
        """Stage all changes."""
        self._run("add", "-A")

    def commit(self, message: str, author: Optional[str] = None) -> str:
        """Create a commit, return commit hash."""
        args = ["commit", "-m", message]
        if author:
            args.extend(["--author", author])

        result = self._run(*args)
        hash_result = self._run("rev-parse", "HEAD")
        return hash_result.stdout.strip()

    def push(self, remote: str = "origin", branch: str = "main") -> None:
        """Push commits to remote."""
        self._run("push", "-u", remote, branch)

    def pull(self, remote: str = "origin", branch: str = "main") -> Tuple[bool, list[str]]:
        """
        Pull from remote.
        Returns (had_conflicts, conflicted_files).
        """
        result = self._run("pull", remote, branch, check=False)

        if result.returncode != 0:
            if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                conflict_files = self._get_conflict_files()
                return (True, conflict_files)
            raise GitError("Pull failed", result.stderr)

        return (False, [])

    def _get_conflict_files(self) -> list[str]:
        """Get list of files with merge conflicts."""
        result = self._run("diff", "--name-only", "--diff-filter=U", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")
        return []

    def fetch(self, remote: str = "origin") -> None:
        """Fetch from remote without merging."""
        self._run("fetch", remote)

    def remote_add(self, name: str, url: str) -> None:
        """Add a remote."""
        self._run("remote", "add", name, url)

    def remote_get_url(self, name: str = "origin") -> Optional[str]:
        """Get remote URL."""
        result = self._run("remote", "get-url", name, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def remote_set_url(self, name: str, url: str) -> None:
        """Set remote URL."""
        self._run("remote", "set-url", name, url)

    def has_remote(self, name: str = "origin") -> bool:
        """Check if a remote exists."""
        result = self._run("remote", "get-url", name, check=False)
        return result.returncode == 0

    def get_status(self, remote: str = "origin", branch: str = "main") -> GitStatusInfo:
        """Get sync status relative to remote."""
        if not self.is_git_repo():
            return GitStatusInfo(status=SyncStatus.NOT_INITIALIZED)

        if not self.has_remote(remote):
            return GitStatusInfo(status=SyncStatus.NO_REMOTE)

        self.fetch(remote)

        ahead = self._count_commits(f"{remote}/{branch}..HEAD")
        behind = self._count_commits(f"HEAD..{remote}/{branch}")
        local_changes = self.get_changed_files()

        if ahead > 0 and behind > 0:
            status = SyncStatus.DIVERGED
        elif ahead > 0:
            status = SyncStatus.AHEAD
        elif behind > 0:
            status = SyncStatus.BEHIND
        else:
            status = SyncStatus.UP_TO_DATE

        return GitStatusInfo(
            status=status,
            commits_ahead=ahead,
            commits_behind=behind,
            local_changes=local_changes,
        )

    def _count_commits(self, range_spec: str) -> int:
        """Count commits in a range."""
        result = self._run("rev-list", "--count", range_spec, check=False)
        if result.returncode == 0:
            try:
                return int(result.stdout.strip())
            except ValueError:
                return 0
        return 0

    def get_changed_files(self) -> list[str]:
        """Get list of modified/added/untracked files."""
        result = self._run("status", "--porcelain", check=False)
        if result.returncode == 0 and result.stdout.strip():
            files = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    files.append(line[3:])
            return files
        return []

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        result = self._run("status", "--porcelain", check=False)
        return bool(result.stdout.strip())

    def get_current_branch(self) -> Optional[str]:
        """Get the current branch name."""
        result = self._run("branch", "--show-current", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def checkout_branch(self, branch: str, create: bool = False) -> None:
        """Checkout a branch."""
        if create:
            self._run("checkout", "-b", branch)
        else:
            self._run("checkout", branch)

    def stash(self) -> bool:
        """Stash current changes. Returns True if something was stashed."""
        result = self._run("stash", check=False)
        return "No local changes" not in result.stdout

    def stash_pop(self) -> None:
        """Pop the most recent stash."""
        self._run("stash", "pop", check=False)

    def reset_hard(self, ref: str = "HEAD") -> None:
        """Hard reset to a reference."""
        self._run("reset", "--hard", ref)

    def get_file_content_at_ref(self, filepath: str, ref: str) -> Optional[str]:
        """Get file contents at a specific revision."""
        result = self._run("show", f"{ref}:{filepath}", check=False)
        if result.returncode == 0:
            return result.stdout
        return None

    def get_last_commit_message(self) -> Optional[str]:
        """Get the last commit message."""
        result = self._run("log", "-1", "--pretty=%B", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def is_git_available() -> bool:
        """Check if git is available on the system."""
        try:
            subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
