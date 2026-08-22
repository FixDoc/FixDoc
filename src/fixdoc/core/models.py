"""Entry model + markdown round-trip for the knowledge store (spec v1).

One entry per file: YAML frontmatter + ``## Section`` markdown body.
See docs/superpowers/specs/2026-08-21-knowledge-store-design.md.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import yaml

TYPE_PREFIXES = {"fix": "fx", "playbook": "pb", "insight": "in"}

REQUIRED_SECTIONS = {
    "fix": ["Symptom", "Root cause", "Fix", "Verification"],
    "playbook": ["When to use", "Steps", "Verification"],
    "insight": ["Context"],
}

# Search-by vs. return split: queries look like symptoms, so embed the
# situation, return the answer.
SEARCH_SECTIONS = {"fix": ["Symptom"], "playbook": ["When to use"], "insight": ["Context"]}
RETURN_SECTIONS = {
    "fix": ["Fix", "Verification"],
    "playbook": ["Steps", "Verification"],
    "insight": ["Context"],
}

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^## (.+)$")


def new_id(entry_type: str) -> str:
    if entry_type not in TYPE_PREFIXES:
        raise ValueError(f"unknown entry type: {entry_type!r}")
    return f"{TYPE_PREFIXES[entry_type]}_{uuid.uuid4().hex[:8]}"


@dataclass
class Entry:
    id: str
    type: str
    title: str
    sections: dict = field(default_factory=dict)  # heading -> text, in file order
    status: str = "quarantined"
    confidence: Optional[float] = None
    occurrences: int = 0
    created: str = field(default_factory=lambda: date.today().isoformat())
    validated_by: Optional[str] = None
    supersedes: Optional[str] = None
    related: list = field(default_factory=list)
    env_scope: list = field(default_factory=list)
    resource_type: Optional[str] = None
    match_keys: dict = field(default_factory=dict)
    severity: Optional[str] = None  # fix only

    def search_text(self) -> str:
        parts = [self.title] + [self.sections.get(s, "") for s in SEARCH_SECTIONS[self.type]]
        return "\n".join(p for p in parts if p)

    def return_text(self) -> str:
        parts = [self.sections.get(s, "") for s in RETURN_SECTIONS[self.type]]
        return "\n\n".join(p for p in parts if p)

    def validate(self) -> list:
        if self.type not in TYPE_PREFIXES:
            return [f"unknown type: {self.type!r}"]
        problems = []
        prefix = TYPE_PREFIXES[self.type] + "_"
        if not self.id.startswith(prefix):
            problems.append(f"id {self.id!r} should start with {prefix!r} for type {self.type}")
        for name in REQUIRED_SECTIONS[self.type]:
            if not self.sections.get(name, "").strip():
                problems.append(f"missing required section: {name}")
        return problems

    def to_markdown(self) -> str:
        front = {"id": self.id, "type": self.type, "title": self.title, "status": self.status}
        if self.confidence is not None:
            front["confidence"] = self.confidence
        front["occurrences"] = self.occurrences
        front["created"] = self.created
        for key in ("validated_by", "supersedes"):
            if getattr(self, key):
                front[key] = getattr(self, key)
        for key in ("related", "env_scope"):
            if getattr(self, key):
                front[key] = getattr(self, key)
        if self.resource_type:
            front["resource_type"] = self.resource_type
        if self.match_keys:
            front["match_keys"] = self.match_keys
        if self.severity:
            front["severity"] = self.severity
        yaml_text = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
        body = "\n\n".join(f"## {name}\n\n{text.strip()}" for name, text in self.sections.items())
        return f"---\n{yaml_text}\n---\n\n{body}\n"

    @classmethod
    def from_markdown(cls, text: str) -> "Entry":
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise ValueError("not a knowledge entry: missing YAML frontmatter")
        front = yaml.safe_load(match.group(1)) or {}
        sections = {}
        current = None
        for line in match.group(2).splitlines():
            heading = _HEADING_RE.match(line)
            if heading:
                current = heading.group(1).strip()
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        return cls(
            id=front["id"],
            type=front["type"],
            title=front["title"],
            sections={k: "\n".join(v).strip() for k, v in sections.items()},
            status=front.get("status", "quarantined"),
            confidence=front.get("confidence"),
            occurrences=front.get("occurrences", 0),
            created=str(front.get("created", "")),  # yaml parses dates as date objects
            validated_by=front.get("validated_by"),
            supersedes=front.get("supersedes"),
            related=front.get("related", []),
            env_scope=front.get("env_scope", []),
            resource_type=front.get("resource_type"),
            match_keys=front.get("match_keys", {}),
            severity=front.get("severity"),
        )
