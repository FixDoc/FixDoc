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
