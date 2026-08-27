# FixDoc PRD Log

## 2026-08-21 — Redesign Area 1: Knowledge Store spec

FixDoc redesigned as an agent context store (pitch stays incident-specific:
"your agents stop repeating incidents"). Area 1 spec written and committed:
`docs/specs/2026-08-21-knowledge-store-design.md`.

Key decisions: format-not-location (git repo of markdown, index is derived hot
path, agents only read via MCP); `knowledge/<namespace>/` tree with per-team
dirs + `shared/`; three types (`fix`/`playbook`/`insight`, id-prefixed
`fx_`/`pb_`/`in_`, `check` folded into playbook); common frontmatter + per-type
section templates with explicit embed-vs-return split; unchanged quarantine
lifecycle; migrations ship in the CLI (`fixdoc migrate` produces a reviewable
git diff), never via repo access.

Next: Area 2 — core engine (index, retrieval, dedup, curation/judge, events).

## 2026-08-22 — Areas 2-3: core engine + MCP server (merged: #13, #15, #17, #18, #19)

Engine slices, all TDD: Entry model + markdown round-trip; three-band dedup
(0.92/0.75, TODO(calibration)); incremental SQLite index (content-hash sync,
model + schema self-invalidation); two-stage retrieval (filter -> blended
rank: similarity + match_keys + trust(occurrences, confidence) - staleness ->
token budget, whole bodies only); record pipeline (engine-forced quarantine,
engine-assigned ids, rule-scored confidence, duplicate -> occurrences++ with
payload preserved in events); append-only events log (local JSONL).
MCP server: four tools over hand-rolled newline-delimited JSON-RPC (stdlib
only; official SDK when HTTP lands), tool descriptions as product copy,
10 writes/min limit, 8000-char field caps, quarantined results named but
withheld. Verified over real pipes, then with a live Claude Code agent
(retrieved at 0.92 similarity, confirmed unprompted). fastembed backend
behind `fixdoc[embed]`; float32 bug found by real-embeddings E2E
(scripts/e2e_mcp.py, kept as release check). Python floor raised to 3.10,
click>=8.2, line-length 100.

## 2026-08-23 — Legacy removal + init (merged: #20, #21)

`fixdoc init`: store scaffold, .mcp.json merge, instruction stanza
(CLAUDE.md/AGENTS.md) + Claude Code skill — the adoption layer. serve reads
embedding_model from .fixdoc/config.yaml. Legacy CLI-era product deleted
wholesale (196 files, -42k lines); README/Makefile rewritten; parsers and
importers to be resurrected from git history for ingestion.

## 2026-08-26 — v0.1.0, seeding direction, white paper (merged: #23)

Version single-sourced (pyproject -> importlib.metadata). PyPI publish
pending (name `fixdoc` is ours, at 0.0.4). Legacy import abandoned (#22
closed): day-0 seeding = ingestion + classification pipeline instead —
teams feed logs/postmortems; FixDoc classifies knowledge-vs-noise, types
entries (fix/playbook/insight), redacts secrets, emits a quarantined store.
Customer pinned: Head of Platform Engineering (or DevOps/SRE lead),
cloud-native 50-500 eng, Terraform+K8s on AWS/Azure, real on-call, engineers
already on coding agents. White paper (2pp PDF + artifact) written. Website
remarketed earlier (#16) now gains a waitlist signup.

