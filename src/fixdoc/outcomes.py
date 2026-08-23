"""Apply outcome learning for fixdoc.

Records what FixDoc predicted at PR time, captures what actually happened
post-apply, links the two, and surfaces historical outcomes in future analyses.
v1 is observational only — outcomes are displayed, not used to alter impact scores.

Storage: per-project `.fixdoc-outcomes` at git root (like `.fixdoc-pending`).
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .pending import _find_git_root


@dataclass
class Outcome:
    """A recorded analysis + optional apply result."""

    outcome_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    plan_fingerprint: str = ""

    # Analysis context (recorded via `fixdoc analyze --record`)
    score: float = 0.0
    severity: str = "low"
    resource_types: list = field(default_factory=list)
    resource_count: int = 0
    top_checks: list = field(default_factory=list)
    commit_sha: Optional[str] = None
    pr_number: Optional[str] = None

    # Apply outcome (recorded via `fixdoc outcome record-apply`)
    apply_result: str = "pending"  # "pending" | "success" | "failure"
    apply_error_output: Optional[str] = None  # Truncated stderr (2000 chars)
    apply_error_codes: list = field(default_factory=list)
    apply_commit_sha: Optional[str] = None

    # Linkage quality
    link_type: str = "none"  # "fingerprint" | "none"

    # Metadata
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied_at: Optional[str] = None
    project_dir: Optional[str] = None
    status: str = "analyzed"  # "analyzed" | "applied"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Outcome":
        return cls(
            outcome_id=data.get("outcome_id", uuid.uuid4().hex[:8]),
            plan_fingerprint=data.get("plan_fingerprint", ""),
            score=data.get("score", 0.0),
            severity=data.get("severity", "low"),
            resource_types=data.get("resource_types", []),
            resource_count=data.get("resource_count", 0),
            top_checks=data.get("top_checks", []),
            commit_sha=data.get("commit_sha"),
            pr_number=data.get("pr_number"),
            apply_result=data.get("apply_result", "pending"),
            apply_error_output=data.get("apply_error_output"),
            apply_error_codes=data.get("apply_error_codes", []),
            apply_commit_sha=data.get("apply_commit_sha"),
            link_type=data.get("link_type", "none"),
            recorded_at=data.get("recorded_at", datetime.now(timezone.utc).isoformat()),
            applied_at=data.get("applied_at"),
            project_dir=data.get("project_dir"),
            status=data.get("status", "analyzed"),
        )


def compute_plan_fingerprint(plan: dict) -> str:
    """Compute a deterministic fingerprint from a Terraform plan.

    Includes address + actions + changed attribute names for tighter linking.
    Order-independent: resource_changes are sorted before hashing.
    """
    changes = []
    for rc in plan.get("resource_changes", []):
        addr = rc.get("address", "")
        actions = tuple(rc.get("change", {}).get("actions", []))
        # Include changed attribute names for precision
        before = rc.get("change", {}).get("before") or {}
        after = rc.get("change", {}).get("after") or {}
        changed_attrs = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        changes.append((addr, actions, tuple(changed_attrs)))
    changes.sort()
    raw = json.dumps(changes, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class OutcomeStore:
    """Manages the .fixdoc-outcomes JSON file."""

    FILENAME = ".fixdoc-outcomes"

    def __init__(self, project_dir: Optional[Path] = None):
        root = _find_git_root(project_dir)
        self._path = root / self.FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _write(self, entries: list[dict]) -> None:
        self._path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    def save(self, outcome: Outcome) -> None:
        """Append or replace by outcome_id."""
        entries = self._read()
        entries = [e for e in entries if e.get("outcome_id") != outcome.outcome_id]
        entries.append(outcome.to_dict())
        self._write(entries)

    def get(self, outcome_id: str) -> Optional[Outcome]:
        """Get an outcome by ID or prefix."""
        for entry in self._read():
            if entry.get("outcome_id", "").startswith(outcome_id):
                return Outcome.from_dict(entry)
        return None

    def list_all(self) -> list[Outcome]:
        """Return all outcomes."""
        return [Outcome.from_dict(e) for e in self._read()]

    def find_by_fingerprint(self, fingerprint: str) -> list[Outcome]:
        """Find outcomes matching a plan fingerprint."""
        return [
            Outcome.from_dict(e) for e in self._read() if e.get("plan_fingerprint") == fingerprint
        ]

    def update_apply_result(
        self,
        outcome_id: str,
        result: str,
        error_output: Optional[str] = None,
        error_codes: Optional[list] = None,
        commit_sha: Optional[str] = None,
    ) -> bool:
        """Transition an outcome from analyzed → applied. Returns True if found."""
        entries = self._read()
        found = False
        for entry in entries:
            if entry.get("outcome_id", "").startswith(outcome_id):
                entry["apply_result"] = result
                entry["status"] = "applied"
                entry["applied_at"] = datetime.now(timezone.utc).isoformat()
                if error_output is not None:
                    entry["apply_error_output"] = error_output[:2000]
                if error_codes is not None:
                    entry["apply_error_codes"] = error_codes
                if commit_sha is not None:
                    entry["apply_commit_sha"] = commit_sha
                entry["link_type"] = "fingerprint"
                found = True
                break
        if found:
            self._write(entries)
        return found

    def clear(self) -> int:
        """Remove all outcomes. Returns count removed."""
        entries = self._read()
        count = len(entries)
        if count > 0:
            self._write([])
        return count
