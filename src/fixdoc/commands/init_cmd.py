"""fixdoc init — one command from empty repo to agent-ready.

Writes four kinds of things, all idempotent:
  1. the store scaffold (knowledge/shared/, .fixdoc/config.yaml, gitignore)
  2. the MCP wiring (.mcp.json, and .cursor/mcp.json for Cursor)
  3. the adoption layer: an instruction stanza in CLAUDE.md/AGENTS.md and a
     Claude Code skill — tool descriptions alone under-trigger; instruction
     files are read at the start of every session and carry the "reach for
     FixDoc" reflex. This text is product copy: it decides whether agents
     actually use the store.
"""

import json
from pathlib import Path

import click
import yaml

from fixdoc.core.embedding import DEFAULT_MODEL

MARKER = "<!-- fixdoc:init -->"

STANZA = f"""
{MARKER}
## FixDoc: team incident knowledge

This repo serves the team's validated fixes to agents over MCP (server: `fixdoc`).

- Hitting an infrastructure error or production symptom? Call `search_fixes`
  with the symptom BEFORE debugging from scratch — someone may have solved it.
- A retrieved fix resolved your issue? Call `confirm_fix` with its id.
- Resolved something the store didn't cover? Call `record_fix` with a causal
  root cause and how you verified the fix. It waits in quarantine until a
  human reviews it.
"""

# The trigger text: one line in the emitted YAML, wrapped here for the linter.
_SKILL_DESCRIPTION = (
    "Use when debugging infrastructure errors, incidents, or production symptoms "
    "(Terraform, Kubernetes, cloud providers, CI failures). Search the team's "
    "validated fix knowledge before diagnosing from scratch, confirm fixes that "
    "worked, record new ones."
)

SKILL = f"""---
name: fixdoc
description: {_SKILL_DESCRIPTION}
---

# FixDoc workflow

1. On any infrastructure error or production symptom, call `search_fixes` first,
   with the symptom as observed. Pass `resource_type` (e.g. `kubernetes/aks`,
   `terraform/aws`) when you know the platform — it filters out wrong-universe
   matches.
2. If a returned validated fix applies, use it — then call `confirm_fix` with its
   id. That one call is how the knowledge base learns what actually works.
3. If nothing matched and you resolve the incident yourself, call `record_fix`:
   causal root cause (why it happened, not what made symptoms stop), concrete fix
   steps, and real verification. It lands in quarantine for human review.
"""

KNOWLEDGE_README = """# Team knowledge

One entry per file, named by its id (`fx_`/`pb_`/`in_` + 8 hex chars). Agents
write new entries via `record_fix`; they arrive with `status: quarantined` and
are NOT served to agents until a human reviews the file and changes the status
to `validated` — normally as a pull request.

Delete FixDoc any time: these files are plain markdown and stay yours.
"""


def _append_once(path: Path, text: str, marker: str) -> bool:
    existing = path.read_text() if path.exists() else ""
    if marker in existing:
        return False
    path.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + text)
    return True


def _merge_mcp_config(path: Path) -> bool:
    config = json.loads(path.read_text()) if path.exists() else {}
    servers = config.setdefault("mcpServers", {})
    if "fixdoc" in servers:
        return False
    # Relative store path on purpose: .mcp.json lives at the repo root and the
    # harness runs the server from there, so the file works on every clone.
    servers["fixdoc"] = {"command": "fixdoc", "args": ["serve", "--store", "."]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True


@click.command("init")
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root to initialize (default: current directory).",
)
@click.option(
    "--harness",
    type=click.Choice(["auto", "claude-code", "cursor", "none"]),
    default="auto",
    show_default=True,
    help="Which agent harness to configure. auto: always writes .mcp.json, "
    "plus Cursor/skill files when .cursor/.claude dirs exist.",
)
def init_command(store_dir, harness):
    """Make this repo agent-ready: store scaffold, MCP config, agent instructions."""
    root = Path(store_dir)
    done = []

    # 1. store scaffold
    (root / "knowledge" / "shared").mkdir(parents=True, exist_ok=True)
    readme = root / "knowledge" / "README.md"
    if not readme.exists():
        readme.write_text(KNOWLEDGE_README)
    config_path = root / ".fixdoc" / "config.yaml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump({"spec_version": 1, "embedding_model": DEFAULT_MODEL}, sort_keys=False)
        )
        done.append("store scaffold (knowledge/, .fixdoc/config.yaml)")
    if _append_once(root / ".gitignore", ".fixdoc-index/\n", ".fixdoc-index/"):
        done.append(".gitignore: .fixdoc-index/")

    # 2. MCP wiring
    if harness != "none":
        if _merge_mcp_config(root / ".mcp.json"):
            done.append(".mcp.json (fixdoc server)")
        if harness == "cursor" or (harness == "auto" and (root / ".cursor").exists()):
            if _merge_mcp_config(root / ".cursor" / "mcp.json"):
                done.append(".cursor/mcp.json")

    # 3. adoption layer: stanza in whichever instruction file the repo already
    # uses (CLAUDE.md wins, then AGENTS.md), creating AGENTS.md if neither exists
    if harness != "none":
        claude_md = root / "CLAUDE.md"
        target = claude_md if claude_md.exists() else root / "AGENTS.md"
        if _append_once(target, STANZA, MARKER):
            done.append(f"{target.name}: agent instructions")
        if harness == "claude-code" or (harness == "auto" and (root / ".claude").exists()):
            skill = root / ".claude" / "skills" / "fixdoc" / "SKILL.md"
            if not skill.exists():
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text(SKILL)
                done.append(".claude/skills/fixdoc")

    if done:
        for item in done:
            click.echo(f"  + {item}")
        click.echo("\nfixdoc is wired up. Next:")
        click.echo("  1. commit these files so your team gets them too")
        click.echo("  2. open your agent (it will ask once to approve the fixdoc MCP server)")
        click.echo("  3. hit an infra error and watch it check the knowledge base first")
    else:
        click.echo("already initialized — nothing to do.")
