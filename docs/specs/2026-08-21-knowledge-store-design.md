# Knowledge Store Design — spec v1

> Area 1 of the FixDoc redesign. The knowledge store is the versioned public
> contract everything else derives from. It is a data format, not a location.

## Thesis

FixDoc is a quality-controlled context store for agents, sold as "your agents
stop repeating incidents." An LLM is text in, text out; an agent is a loop
around it; FixDoc is the layer that decides which tribal knowledge enters the
context window. The store holds that knowledge as plain markdown files the team
owns. Agents never read the files directly — retrieval goes through a derived
index via MCP tools, returning ranked, token-budgeted context. Files are cold
storage; the index is the hot path.

The store is a git repo (or, for solo local use, a bare directory — git is what
makes it team-ready). Git supplies, for free: the write path's governance
(quarantine promotion = PR review, CODEOWNERS = per-namespace authz), per-entry
audit history, sync/conflict semantics (re-index from HEAD, never trust a
copy), and the trust story ("delete our software, your knowledge is intact").

## Repo layout

```
.fixdoc/
  config.yaml          # spec_version, embedding model, dedup thresholds, namespaces
knowledge/
  <namespace>/         # per-team directories + shared/
    fx_7c2a91e4.md     # one entry per file; id prefix encodes type
.fixdoc-index/         # gitignored, derived, always rebuildable
```

- One entry per file. Separate files = zero merge conflicts on concurrent
  writes, file-granularity PR review, free per-entry `git log`, visible
  deprecation diffs.
- Namespaces are per-team directories plus `shared/` for org-wide knowledge.
  CODEOWNERS on namespace dirs is the authorization model.
- `.fixdoc-index/` is derived state. It is never migrated, always rebuilt.

## Knowledge types

Three types in v1, no more. `type` is a frontmatter field; the id prefix
mirrors it so humans in PR diffs and agents in tool responses know the type
without opening the file.

| Type | Prefix | What it is |
|---|---|---|
| `fix` | `fx_` | Flagship. A validated incident resolution: symptom, root cause, fix, verification. |
| `playbook` | `pb_` | A multi-step operational procedure with no single triggering symptom. |
| `insight` | `in_` | A short environmental fact an agent should know (e.g. "staging shares prod's NAT gateway"). No procedure. |

The old CLI's `check` type folds into `playbook`.

## Frontmatter schema

Common to all types (no field the engine does not read):

```yaml
---
id: fx_7c2a91e4          # type prefix + 8 hex
type: fix                # fix | playbook | insight
title: "AKS pods stuck Pending after nodepool scale-up"
status: quarantined      # quarantined | validated | deprecated | rejected
confidence: 0.87         # judge score at record/validation time
occurrences: 3           # times this entry resolved a real incident — THE value metric
created: 2026-08-14
validated_by: fiyi       # human or "judge:<model>"
supersedes: fx_19bb02a1  # optional; rot management
related: [fx_88d1c2aa]   # optional; dedup middle-band links
env_scope: [prod, staging]
resource_type: kubernetes/aks
match_keys:              # flat string map, NOT a rigid taxonomy
  error_class: FailedScheduling
  component: cluster-autoscaler
severity: high           # fix only
---
```

## Body sections per type — search-by vs. return

Queries look like symptoms/situations, so we embed the situation, not the
answer. Separating what is embedded from what is returned is a core precision
lever.

| Type | Required sections | Embedded (search-by) | Returned |
|---|---|---|---|
| `fix` | Symptom, Root cause, Fix, Verification | title + Symptom | Fix + Verification |
| `playbook` | When to use, Steps, Verification | title + When to use | Steps + Verification |
| `insight` | Context (short body) | title + body | body |

Root cause must be causal, not correlational (judge checks this). Verification
must state how you know it worked (judge checks presence).

## Lifecycle

```
recorded → quarantined → validated → deprecated
                  ↘ rejected
```

- All types pass through quarantine. `record_*` (any caller, including our own
  hosted service) can only create `quarantined`. This is a security boundary by
  construction: a prompt-injected agent's blast radius is "created a
  quarantined entry someone will review."
- Promotion to `validated` is a human act: locally a PR merging the status
  change; hosted, a review-UI click producing the same commit.
- `deprecated` + `supersedes` exist from day one. Auto-promotion is a later
  opt-in config option, never a default.

## Versioning and migration

`spec_version` lives in `.fixdoc/config.yaml`. The engine refuses formats it
does not know. Every schema change is a spec_version bump — no silent drift.

**Migrations ship in the software, not from us.** We never reach into a team's
repo (local mode: we can't; that's the point). The flow:

1. Team upgrades the fixdoc binary (`pipx upgrade fixdoc`). New binary knows
   spec v2, reads `config.yaml`, sees v1.
2. Engine refuses to write (and `fixdoc doctor` explains); user runs
   `fixdoc migrate`.
3. `fixdoc migrate` rewrites the markdown files in place, bumps `spec_version`,
   and leaves the result as an ordinary git diff the team reviews and commits
   like any other change. A migration is a visible, reviewable commit — the
   same trust story as everything else.
4. Old binary + already-migrated repo: hard refusal with "repo is spec v2,
   upgrade fixdoc."
5. Hosted mode: same primitive, delivered as a PR our sync service opens via
   the GitHub App; the team reviews and merges it.
6. The index is never migrated — after a migration it is rebuilt from the
   markdown.

## Non-goals (v1)

- No tag taxonomy, no folders-by-type, no per-org config beyond thresholds.
- No fourth knowledge type until demand proves one.
- No hosted-only format features, ever — the format must survive "you can
  leave anytime with everything."
