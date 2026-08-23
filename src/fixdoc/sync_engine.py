"""Core synchronization logic for fixdoc sync."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

from .config import ConfigManager
from .formatter import fix_to_markdown
from .git import GitOperations, GitError, SyncStatus
from .markdown_parser import markdown_to_fix, MarkdownParseError
from .models import Fix
from .storage import FixRepository


class ConflictType(Enum):
    """Types of sync conflicts."""

    BOTH_MODIFIED = "both_modified"
    LOCAL_DELETED_REMOTE_MODIFIED = "local_deleted"
    REMOTE_DELETED_LOCAL_MODIFIED = "remote_deleted"


@dataclass
class SyncConflict:
    """Represents a conflict during sync."""

    fix_id: str
    conflict_type: ConflictType
    local_fix: Optional[Fix] = None
    remote_fix: Optional[Fix] = None


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    pushed_fixes: List[str] = field(default_factory=list)
    pulled_fixes: List[str] = field(default_factory=list)
    conflicts: List[SyncConflict] = field(default_factory=list)
    error_message: Optional[str] = None


class SyncEngine:
    """Handles the core sync logic."""

    def __init__(
        self,
        repo: FixRepository,
        git: GitOperations,
        config_manager: ConfigManager,
    ):
        self.repo = repo
        self.git = git
        self.config_manager = config_manager

    def prepare_push(self, push_all: bool = False) -> List[Fix]:
        """
        Identify fixes that need to be pushed.

        By default, only returns fixes that are new or have been modified
        since the last push. Use push_all=True to push all non-private fixes.
        """
        config = self.config_manager.load()
        all_fixes = self.repo.list_all()

        pushable = []
        for fix in all_fixes:
            if fix.is_private:
                continue
            if fix.id in config.private_fixes:
                continue
            pushable.append(fix)

        if push_all:
            return pushable

        # Filter to only new or changed fixes by comparing generated
        # markdown against what's already committed in HEAD.
        changed = []
        for fix in pushable:
            current_md = fix_to_markdown(fix)
            committed_md = self.git.get_file_content_at_ref(f"docs/{fix.id}.md", "HEAD")
            if committed_md is None or committed_md != current_md:
                changed.append(fix)

        return changed

    def execute_push(self, fixes: List[Fix], commit_message: Optional[str] = None) -> SyncResult:
        """
        Push fixes to remote:
        1. Regenerate markdown files for fixes
        2. Git add changed files
        3. Git commit with message and author info
        4. Git push to remote
        """
        config = self.config_manager.load()

        if not config.sync.remote_url:
            return SyncResult(
                success=False,
                error_message="Sync not configured. Run 'fixdoc sync init' first.",
            )

        if not fixes:
            return SyncResult(
                success=True,
                pushed_fixes=[],
                error_message="No fixes to push.",
            )

        try:
            docs_path = self.repo.docs_path
            pushed_ids = []

            for fix in fixes:
                if config.user.name and not fix.author:
                    fix.author = config.user.name
                    fix.author_email = config.user.email
                    self.repo.save(fix)

                md_path = docs_path / f"{fix.id}.md"
                with open(md_path, "w") as f:
                    f.write(fix_to_markdown(fix))
                pushed_ids.append(fix.id)

            self.git.add("docs/")

            if not self.git.has_uncommitted_changes():
                return SyncResult(
                    success=True,
                    pushed_fixes=[],
                    error_message="No changes to push.",
                )

            if not commit_message:
                if len(fixes) == 1:
                    commit_message = f"[fixdoc] Add fix: {fixes[0].id[:8]}"
                else:
                    commit_message = f"[fixdoc] Add/update {len(fixes)} fixes"

            author = None
            if config.user.name and config.user.email:
                author = f"{config.user.name} <{config.user.email}>"

            self.git.commit(commit_message, author=author)
            self.git.push(branch=config.sync.branch)

            return SyncResult(success=True, pushed_fixes=pushed_ids)

        except GitError as e:
            return SyncResult(success=False, error_message=str(e))

    def execute_pull(self, force: bool = False) -> SyncResult:
        """
        Pull fixes from remote:
        1. Git fetch
        2. Detect conflicts
        3. If no conflicts or force=True: git pull and update local DB
        4. If conflicts: return conflicts for user resolution
        """
        config = self.config_manager.load()

        if not config.sync.remote_url:
            return SyncResult(
                success=False,
                error_message="Sync not configured. Run 'fixdoc sync init' first.",
            )

        try:
            if force and self.git.has_uncommitted_changes():
                self.git.stash()

            had_conflicts, conflict_files = self.git.pull(branch=config.sync.branch)

            if had_conflicts and not force:
                conflicts = self._build_conflicts_from_files(conflict_files)
                self.git.reset_hard("HEAD")
                return SyncResult(success=False, conflicts=conflicts)

            pulled_fixes = self.rebuild_json_from_markdown()

            if force:
                self.git.stash_pop()

            return SyncResult(success=True, pulled_fixes=pulled_fixes)

        except GitError as e:
            return SyncResult(success=False, error_message=str(e))

    def _build_conflicts_from_files(self, conflict_files: List[str]) -> List[SyncConflict]:
        """Build conflict objects from conflicted file paths."""
        conflicts = []
        for filepath in conflict_files:
            if filepath.startswith("docs/") and filepath.endswith(".md"):
                fix_id = Path(filepath).stem
                local_fix = self.repo.get(fix_id)
                conflicts.append(
                    SyncConflict(
                        fix_id=fix_id,
                        conflict_type=ConflictType.BOTH_MODIFIED,
                        local_fix=local_fix,
                        remote_fix=None,
                    )
                )
        return conflicts

    def rebuild_json_from_markdown(self) -> List[str]:
        """
        Rebuild fixes.json from markdown files.
        Markdown is source of truth for sync.
        Returns list of fix IDs that were updated/added.
        """
        docs_path = self.repo.docs_path
        updated_ids = []

        if not docs_path.exists():
            return updated_ids

        existing_fixes = {fix.id: fix for fix in self.repo.list_all()}

        for md_file in docs_path.glob("*.md"):
            fix_id = md_file.stem
            try:
                with open(md_file, "r") as f:
                    content = f.read()
                parsed_fix = markdown_to_fix(content, fix_id)

                if fix_id in existing_fixes:
                    existing = existing_fixes[fix_id]
                    if parsed_fix.is_private or existing.is_private:
                        parsed_fix.is_private = existing.is_private

                self.repo.save(parsed_fix)
                updated_ids.append(fix_id)
            except MarkdownParseError:
                continue

        return updated_ids

    def resolve_conflict(self, conflict: SyncConflict, resolution: str) -> Optional[Fix]:
        """
        Resolve a conflict based on user choice:
        - 'local': Keep local version
        - 'remote': Accept remote version
        - 'merge': Combine both (add both to notes)
        """
        if resolution == "local":
            return conflict.local_fix

        elif resolution == "remote":
            return conflict.remote_fix

        elif resolution == "merge" and conflict.local_fix and conflict.remote_fix:
            merged = Fix(
                id=conflict.fix_id,
                issue=conflict.remote_fix.issue,
                resolution=conflict.remote_fix.resolution,
                error_excerpt=conflict.remote_fix.error_excerpt or conflict.local_fix.error_excerpt,
                tags=self._merge_tags(conflict.local_fix.tags, conflict.remote_fix.tags),
                notes=self._merge_notes(conflict.local_fix, conflict.remote_fix),
                created_at=conflict.local_fix.created_at,
                updated_at=conflict.remote_fix.updated_at,
                author=conflict.remote_fix.author or conflict.local_fix.author,
                author_email=conflict.remote_fix.author_email or conflict.local_fix.author_email,
            )
            return merged

        return None

    def _merge_tags(self, local_tags: Optional[str], remote_tags: Optional[str]) -> Optional[str]:
        """Merge two tag strings, removing duplicates."""
        if not local_tags and not remote_tags:
            return None

        local_set = set(t.strip() for t in (local_tags or "").split(",") if t.strip())
        remote_set = set(t.strip() for t in (remote_tags or "").split(",") if t.strip())
        merged = local_set | remote_set

        return ",".join(sorted(merged)) if merged else None

    def _merge_notes(self, local_fix: Fix, remote_fix: Fix) -> Optional[str]:
        """Merge notes from both versions."""
        parts = []

        if local_fix.notes:
            parts.append(f"### Local Notes:\n{local_fix.notes}")

        if remote_fix.notes:
            parts.append(f"### Remote Notes:\n{remote_fix.notes}")

        if local_fix.resolution != remote_fix.resolution:
            parts.append(f"### Local Resolution (merged):\n{local_fix.resolution}")

        if parts:
            return "\n\n".join(parts)
        return None

    def get_sync_status(self) -> dict:
        """Get current sync status information."""
        config = self.config_manager.load()

        if not config.sync.remote_url:
            return {
                "configured": False,
                "message": "Sync not configured.",
            }

        git_status = self.git.get_status(branch=config.sync.branch)
        all_fixes = self.repo.list_all()
        pushable = self.prepare_push(push_all=False)
        private_count = len([f for f in all_fixes if f.is_private or f.id in config.private_fixes])

        return {
            "configured": True,
            "remote_url": config.sync.remote_url,
            "branch": config.sync.branch,
            "status": git_status.status.value,
            "commits_ahead": git_status.commits_ahead,
            "commits_behind": git_status.commits_behind,
            "local_changes": git_status.local_changes,
            "pushable_fixes": len(pushable),
            "private_fixes": private_count,
            "total_fixes": len(all_fixes),
        }
