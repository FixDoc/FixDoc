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
