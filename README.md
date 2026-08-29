# FixDoc

**Your agents stop repeating incidents.**

FixDoc is an incident knowledge store your AI agents query over MCP. Validated
fixes, playbooks, and environment insights go in; ranked, token-budgeted
context comes out. Knowledge lives as markdown files in a git repo you own —
delete FixDoc any time and everything stays readable.

## Five-minute start

```bash
uv tool install "fixdoc[embed]"      # or: pipx install "fixdoc[embed]"
cd your-infra-repo
fixdoc init                          # scaffold + MCP config + agent instructions
```

Open your agent (Claude Code, Cursor, any MCP client), approve the `fixdoc`
server once, and hit an infrastructure error: the agent checks the knowledge
base before debugging from scratch.

## Seed the knowledge base (day 0)

Two moves take a new store from empty to useful:

**1. The interview — highest value first.** Open your agent in the repo and say:
*"interview me: ten facts about our environment every new engineer learns the
hard way, plus our five worst incidents — record each one."* The agent asks the
questions and writes the entries; you review the quarantine afterwards.

**2. Import the documents you already have:**

```bash
fixdoc ingest ./runbooks ./postmortems
```

- Markdown documents with a resolution-type section (Fix, Mitigation,
  Resolution, Remediation, Workaround, ...) become quarantined entries.
  Sections map by heading: Summary/Impact → Symptom, Root Cause → Root cause,
  Verification → Verification. Docs with three or more numbered steps become
  playbooks automatically.
- Anything a document never stated becomes a visible "(add during review)"
  placeholder — nothing is invented. Documents with no resolution content are
  skipped: they're notes, not knowledge.
- **Secrets are redacted before anything is written** (AWS keys, private-key
  blocks, JWTs, bearer tokens, `password=`-style values) — the store is a git
  repo you'll push, so nothing sensitive may enter it.
- Volume is capped: `--limit` (default 200, max 1000) keeps the
  highest-substance candidates; re-run after reviewing a batch to drain the
  next wave (re-runs never duplicate). `--dry-run` previews without writing.
- Logs are deliberately not ingested: errors carry no remediation. Knowledge
  comes from documents, threads, and people.

Then review each new entry — fill the placeholders, change
`status: quarantined` to `status: validated` (normally via pull request) —
and it becomes retrievable.

## How it works

Four MCP tools are the entire agent surface:

| Tool | What it does |
|---|---|
| `search_fixes` | Validated knowledge for a symptom, ranked by relevance and proof |
| `record_fix` | Write a new fix — it lands in **quarantine** for human review |
| `confirm_fix` | Report that a retrieved fix worked (raises its trust) |
| `get_fix` | Fetch one entry in full by id |

Everything an agent writes starts `quarantined` and is invisible to retrieval
until a human promotes it to `validated` — normally a pull request review.
There is no delete or admin tool by design.

## The store

```
.fixdoc/config.yaml      # spec_version, embedding model
knowledge/
  <namespace>/           # per-team dirs; CODEOWNERS decides who validates what
    fx_7c2a91e4.md       # one entry per file, filename = id
.fixdoc-index/           # derived (SQLite + embeddings), gitignored, rebuildable
```

Entries are YAML frontmatter + markdown sections (Symptom / Root cause / Fix /
Verification). Search embeds the symptom; results return the fix. Embeddings
run fully local (`bge-small-en-v1.5` via fastembed) — no account, no network
call in the query path.

## Where things stand (v0.1.0)

Working and verified today: the four MCP tools end to end with a live agent,
semantic retrieval with trust-weighted ranking and token budgets, the
quarantine write path with dedup, the events log, local embeddings, and
`fixdoc init`. Next up, in order: day-0 ingestion (feed logs and postmortems
in, get a classified, secret-redacted, quarantined store out), Slack/Jira/
ServiceNow/Notion importers, the ops surface (`status`, `promote`, `doctor`),
and the eval harness.

## Developing

```bash
make setup      # venv + editable install (dev + embed)
make test       # unit suite
make e2e        # end-to-end with real embeddings
make lint fmt
```

Design docs live in `docs/specs/`. The engine is `src/fixdoc/core/`
(models, dedup, index, retrieval, record, events); the MCP server is
`src/fixdoc/mcp_server.py`.

## License

MIT
