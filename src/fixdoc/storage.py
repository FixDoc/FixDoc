"""Storage management for fixdoc."""

import json
from pathlib import Path
from typing import Optional

from .config import resolve_base_path
from .models import Fix
from .formatter import fix_to_markdown


class FixRepository:
    """
    Manages the local fix database and markdown files.

    Storage structure:
        ~/.fixdoc/
            fixes.json      # JSON database
            docs/           # Generated markdown files
    """

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or resolve_base_path()
        self.db_path = self.base_path / "fixes.json"
        self.docs_path = self.base_path / "docs"
        self._ensure_paths()

    def _ensure_paths(self) -> None:
        """Create necessary directories if they don't exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.docs_path.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._write_db([])

    def _read_db(self) -> list[dict]:
        """Read the JSON database."""
        try:
            with open(self.db_path, "r") as f:
                # print('--------------',json.load(f))
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write_db(self, data: list[dict]) -> None:
        """Write to the JSON database."""
        with open(self.db_path, "w") as f:
            json.dump(data, f, indent=2)

    def _write_markdown(self, fix: Fix) -> Path:
        """Generate markdown file for a fix."""
        md_path = self.docs_path / f"{fix.id}.md"
        with open(md_path, "w") as f:
            f.write(fix_to_markdown(fix))
        return md_path

    def save(self, fix: Fix) -> Fix:
        """Save a fix to the database and generate markdown."""
        fixes = self._read_db()

        existing_idx = next((i for i, f in enumerate(fixes) if f.get("id") == fix.id), None)

        if existing_idx is not None:
            fixes[existing_idx] = fix.to_dict()
        else:
            fixes.append(fix.to_dict())

        self._write_db(fixes)
        self._write_markdown(fix)
        return fix

    def get(self, fix_id: str) -> Optional[Fix]:
        """Retrieve a fix by ID"""
        fixes = self._read_db()
        fix_id_lower = fix_id.lower()

        for f in fixes:
            if f["id"].lower().startswith(fix_id_lower):
                return Fix.from_dict(f)
        return None

    def list_all(self) -> list[Fix]:
        """Return all fixes in the database."""
        return [Fix.from_dict(f) for f in self._read_db()]

    def search(self, query: str) -> list[Fix]:
        """Search fixes by query string (case-insensitive)."""
        return [f for f in self.list_all() if f.matches(query)]

    def find_by_resource_type(self, resource_type: str) -> list[Fix]:
        """Find all fixes tagged with a specific resource type."""
        return [f for f in self.list_all() if f.matches_resource_type(resource_type)]

    def delete(self, fix_id: str) -> bool:
        """Delete a fix by ID. Returns True if deleted."""
        fixes = self._read_db()
        fix_id_lower = fix_id.lower()

        for i, f in enumerate(fixes):
            if f["id"].lower().startswith(fix_id_lower):
                deleted_fix = fixes.pop(i)
                self._write_db(fixes)

                md_path = self.docs_path / f"{deleted_fix['id']}.md"
                if md_path.exists():
                    md_path.unlink()
                return True
        return False

    def count(self) -> int:
        """Return the number of fixes in the database."""
        return len(self._read_db())

    def purge(self) -> int:
        """Delete all fixes. Returns count of deleted fixes."""
        fixes = self._read_db()
        for f in fixes:
            md_path = self.docs_path / f"{f['id']}.md"
            if md_path.exists():
                md_path.unlink()
        self._write_db([])
        return len(fixes)

    def get_by_full_id(self, fix_id: str) -> Optional[Fix]:
        """Get fix by exact ID match (for sync operations)."""
        fixes = self._read_db()
        for f in fixes:
            if f["id"] == fix_id:
                return Fix.from_dict(f)
        return None

    def list_markdown_files(self) -> list[Path]:
        """List all markdown files in docs directory."""
        if not self.docs_path.exists():
            return []
        return list(self.docs_path.glob("*.md"))

    def get_fix_ids(self) -> set[str]:
        """Get set of all fix IDs."""
        return {f["id"] for f in self._read_db()}
