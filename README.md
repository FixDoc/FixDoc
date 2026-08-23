# FixDoc

Stop losing infrastructure fixes in Slack threads — capture, search, and share them from your terminal.

FixDoc is a CLI tool for cloud engineers that turns raw Terraform and Kubernetes error output into a searchable, version-controlled knowledge base that's risk aware and CI gate friendly. With FixDoc you are building an intelligent error knowledge platform that guards your deployments and lets you understand wider infrastructure impact through smart analysis.

## See It In Action

```bash
# Wrap any command — errors auto-deferred on failure
fixdoc watch -- terraform apply

# Capture from piped output
terraform apply 2>&1 | fixdoc capture

# Search your fix history
fixdoc search "S3 access denied"
```

```
[1] S3 bucket policy denied public access block (2024-11-03)
    Tags: aws, s3, iam
    Resolution: Add explicit s3:GetObject allow for the deployment role ARN

[2] AccessDenied on S3 presigned URL — wrong region endpoint (2024-10-21)
    Tags: aws, s3
    Resolution: Switch client to us-east-1; presigned URLs are region-scoped
```

## Recommended Workflow

The fastest path to getting value out of FixDoc:

```bash
# 1. Try the demo — no cloud account needed
fixdoc demo tour

# 2. Wrap your next deploy
fixdoc watch -- terraform apply

# 3. On failure: errors are auto-deferred. Come back when ready.
fixdoc pending          # list deferred errors
fixdoc pending capture 1  # write up fix #1

# 4. On success: FixDoc prompts you to close out any pending errors
#    (or run fixdoc resolve if you're coming back later)
fixdoc resolve

# 5. Before the next apply, analyze the plan
terraform plan -out=plan.tfplan && terraform show -json plan.tfplan > plan.json
fixdoc analyze plan.json
```

## Try It in 30 Seconds

Run the interactive tour — no cloud account needed:

```bash
fixdoc demo tour
```

The tour walks you through capturing a real Terraform AWS error, a Kubernetes CrashLoopBackOff, searching your database, listing stats, and analyzing a Terraform plan. Every fix captured during the tour is saved locally so you can explore it afterwards.

Pre-populate your database with realistic sample fixes:

```bash
fixdoc demo seed
fixdoc demo seed --clean   # remove old demo data before re-seeding
```

## What It Does

**Core workflow:**
- Pipe or wrap any command — auto-detects Terraform and Kubernetes errors
- On failure, all errors are immediately deferred to a per-project pending queue (no blocking prompts)
- Surfaces similar fixes from your history before creating duplicates
- Stores everything as searchable JSON + human-readable markdown in `~/.fixdoc/`

**Team and CI features:**
- Sync fixes with your team via a shared Git repo
- Analyze Terraform plans against your fix history before `apply` to surface known failure patterns
- Gate CI pipelines on change impact severity (`--exit-on high`)
- Record apply outcomes and surface historical prediction accuracy in future analyses
- Bulk import closed tickets from Jira, ServiceNow, Notion, and Slack

## Installation

**Recommended — pipx** (installs into an isolated environment and puts `fixdoc` on your PATH automatically):

```bash
pipx install fixdoc
```

**pip:**

```bash
pip install fixdoc
```

> **Note:** After installing with pip, make sure `fixdoc` is on your PATH. If the command isn't found, run `pip show -f fixdoc | grep fixdoc` to find the binary location and add that directory to your `PATH`, or use `python3 -m fixdoc` as an alternative.

Requires Python 3.10+. Runtime dependencies: `click` and `pyyaml` only.

**Optional — AI-powered plan explanations:**

```bash
pip install fixdoc[ai]   # adds anthropic SDK for --ai-explain on fixdoc analyze
```

## Command Reference

### Capturing Fixes

**Watch mode** — wrap your command; errors auto-defer on failure, auto-resolve on success:

```bash
fixdoc watch -- terraform apply
fixdoc watch --tags aws,prod -- terraform apply -target=aws_instance.web
```

On failure, all detected errors are immediately deferred to `.fixdoc-pending` (no blocking prompts). A summary card is shown. Press `[c]` to capture one immediately, or `[s]` to skip. The original exit code is always preserved — safe in any CI script.

On success, FixDoc checks for pending errors from the same session and prompts you to close them out.

**Pipe mode** — capture from any command's output:

```bash
terraform apply 2>&1 | fixdoc capture
kubectl apply -f deployment.yaml 2>&1 | fixdoc capture
```

FixDoc reads from stdin, auto-detects the error source (Terraform, Kubernetes, or generic), extracts structured metadata, checks for similar existing fixes, and prompts for the resolution.

**Quick capture** — write a fix directly:

```bash
fixdoc capture --issue "RDS connection timeout" --resolution "Increase connection pool size in db.py"
```

### Pending Queue

Errors deferred during `watch` sessions land here. Project-scoped, stored in `.fixdoc-pending` at your git root.

```bash
fixdoc pending              # list deferred errors
fixdoc pending capture 1    # write a fix for error #1
fixdoc pending capture a3f9 # capture by ID prefix
fixdoc pending remove 1     # discard without capturing
fixdoc pending clear        # clear all pending entries
```

### Resolve

Document fixes for deferred errors in the current directory. Useful when coming back to a failed deploy:

```bash
fixdoc resolve
```

This finds all pending entries for the current working directory and walks you through them one by one.

### Searching and Browsing

```bash
fixdoc search "S3 access denied"     # keyword search across all fix fields
fixdoc search "crashloop" --limit 5  # limit results
fixdoc show abc12345                  # view a single fix in full detail
fixdoc list                           # list all fixes, most recent first
fixdoc list --limit 20                # show more
fixdoc stats                          # tag distribution, totals, coverage
```

Search matches against issue description, resolution text, tags, error excerpt, and resource metadata — so `fixdoc search aws_s3_bucket` finds fixes mentioning that resource type even if the term isn't in the summary.

### Editing and Deleting

```bash
fixdoc edit abc12345    # open a fix in your editor to update issue, resolution, or tags
fixdoc delete abc12345  # permanently remove a fix
```

### Analyzing Terraform Plans

Before `terraform apply`, generate a plan JSON and run it through FixDoc to surface change impact and known failure patterns:

```bash
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan > plan.json
fixdoc analyze plan.json
```

FixDoc traverses the Terraform dependency graph, scores change impact using a sigmoid formula (affected count, IAM/network boundary criticality, action type, fix history), matches changing resources against your fix history, and surfaces historical apply outcomes.

```
Terraform Plan Analysis
=======================
5 resources changing (2 create, 1 update, 2 delete)

Risk Score: 61 / 100  [HIGH]

Why this scored 61:
  +10  delete action on live resources
  +8   IAM policy field changed
  +15  network boundary in change impact (depth 2)

Changes:
  CREATE    aws_iam_role.deploy_role   [iam boundary]
  UPDATE    aws_lambda_function.api
  DELETE    aws_security_group.old_sg  [network boundary]

Impacted Resources (8):
  aws_lambda_function.api              (depth: 1, via: aws_iam_role.deploy_role)
  aws_rds_cluster.main                 (depth: 2, via: aws_security_group.old_sg)
  ...

Relevant Past Fixes (2):
  [high: error code: MissingPolicyStatement] IAM role missing lambda:InvokeFunction
  [medium: resource type match] SG deletion broke RDS inbound rule — apply rolled back
```

Options:

```bash
fixdoc analyze plan.json --graph graph.dot       # provide DOT graph explicitly
fixdoc analyze plan.json --format json           # machine-readable output
fixdoc analyze plan.json --format markdown       # GitHub-flavored markdown for PR comments
fixdoc analyze plan.json --summary               # one-line risk summary
fixdoc analyze plan.json --exit-on high          # exit code 1 if HIGH or CRITICAL
fixdoc analyze plan.json --match strict          # strict history matching
fixdoc analyze plan.json --max-depth 3           # limit dependency traversal
fixdoc analyze plan.json --ai-explain            # AI-generated plain-English explanation (requires fixdoc[ai])
fixdoc analyze plan.json --record                # save this analysis as an outcome for future reference
fixdoc analyze plan.json --record --pr 42 --commit abc123  # with CI metadata
```

If terraform is on your PATH, FixDoc auto-runs `terraform graph` — no `--graph` flag needed.

### Apply Outcome Learning

Record what actually happened after an apply and surface prediction accuracy in future analyses. Outcomes are stored in `.fixdoc-outcomes` at your git root.

```bash
# After apply completes, record the result (links to the prior analysis by plan fingerprint)
fixdoc outcome record-apply --result success
fixdoc outcome record-apply --result failure --error-output "$(cat apply.log)"

# Browse recorded outcomes
fixdoc outcome list            # table of all outcomes with link status
fixdoc outcome show abc12345   # full detail for one outcome
```

Future `fixdoc analyze` runs surface matching historical outcomes in a "Historical Apply Outcomes" section, showing whether prior predictions were accurate — observational only, does not alter the risk score.

### Change Impact (Standalone)

Risk score without history matching:

```bash
fixdoc analyze plan.json
fixdoc analyze plan.json --format json --exit-on critical
```

### Importing Fixes from External Systems

Bulk-import closed tickets from your existing issue trackers to bootstrap your fix database.

**Jira** (CSV or JSON export):

```bash
fixdoc import jira issues.csv
fixdoc import jira backup.json --auto        # skip interactive review
fixdoc import jira issues.csv --dry-run      # preview without saving
fixdoc import jira issues.csv --tags team:platform,env:prod
```

**ServiceNow** (JSON export):

```bash
fixdoc import servicenow incidents.json
fixdoc import servicenow incidents.json --auto --max 200
```

**Notion** (API-based):

```bash
fixdoc import notion --token secret_xxx --database abc123def456
fixdoc import notion --token secret_xxx --database abc123def456 --auto
```

**Slack** (API-based, two-emoji convention):

```bash
fixdoc import slack --token xoxb-xxx --channel C0ABC1234
fixdoc import slack --token xoxb-xxx --channel-name infra-incidents
```

Slack import uses a two-emoji convention: mark an incident message with `:red_circle:` and the resolution reply with `:white_check_mark:`. Required bot scopes: `channels:history`, `channels:read`, `users:read`, `reactions:read`.

All importers:
- Default to interactive review mode — shows each fix card with `[y]es / [e]dit / [s]kip / [a]ccept remaining / [q]uit`
- `--auto` skips review, applying a high-signal filter (resource type tags or infra keywords)
- `--dry-run` previews without saving
- Tag each imported fix with `source:system:id` for idempotent re-runs

### Team Sync

Share fixes via a shared Git repo:

```bash
fixdoc sync init git@github.com:your-org/infra-fixes.git
fixdoc sync push                # push your fixes to the shared repo
fixdoc sync pull                # pull and merge fixes from teammates
fixdoc sync status              # show sync state
```

Fixes marked `is_private: true` are always excluded from sync.

## How It Works

Source-specific parsers (Terraform, Kubernetes, and a generic fallback) extract structured metadata from raw error output — provider, resource type, error code, resource address — so fixes are queryable by field, not just full-text searchable. A similarity engine scores existing fixes before capture so you see matching resolutions before writing a new entry.

Everything persists as structured JSON plus a human-readable markdown file per fix. The analysis engine traverses your Terraform dependency graph using bounded BFS propagation, scores change impact with a sigmoid formula weighting IAM/network control points, and matches changes against fix history using multi-signal scoring (error code, resource address, changed attributes, recency). Sensitive field values are redacted before any output is written.

## CI Integration

Quick start — add to any workflow that generates a Terraform plan:

```yaml
- name: Analyze risk
  run: |
    terraform show -json plan.tfplan > plan.json
    fixdoc analyze plan.json --exit-on high --record --pr ${{ github.event.pull_request.number }}
```

### Review Modes

The included workflow (`.github/workflows/terraform-risk-analysis.yml`) supports three modes via `REVIEW_MODE`:

| Mode | Behavior |
|------|----------|
| `advisory` (default) | Posts analysis to job summary + PR comment. Never blocks. |
| `warn` | Same as advisory + adds GitHub warning annotation if high/critical. |
| `gate` | Same as warn + fails the workflow if severity >= `GATE_THRESHOLD`. |

A second workflow, `.github/workflows/terraform-apply-outcome.yml`, records apply results post-merge and links them back to the corresponding analysis outcome.

### `--exit-on` for CI Gating

```bash
fixdoc analyze plan.json --exit-on high      # fail if HIGH or CRITICAL
fixdoc analyze plan.json --exit-on critical   # fail only on CRITICAL
fixdoc analyze plan.json --exit-on low        # fail on any non-zero score
```

### Output Formats

| Format | Flag | Use case |
|--------|------|----------|
| Human | `--format human` (default) | Terminal output with color |
| JSON | `--format json` | Machine-readable for scripts |
| Markdown | `--format markdown` | PR comments and job summaries |
| Summary | `--summary` | One-line risk summary |

## Local Development & Testing

Prerequisites: Python 3.10+, Docker, Docker Compose, Terraform ≥ 1.5

```bash
bash scripts/setup-dev.sh   # check deps + install Python packages
make localstack-up          # start LocalStack mock AWS (port 4566)
make test                   # run 822 tests
make scenarios              # run full LocalStack scenario matrix
```

Or all at once:

```bash
make dev
```

Run `make help` to see all available targets (lint, fmt, clean, and more).

## Contributing

```bash
git clone https://github.com/fiyiogunkoya/fixdoc.git
cd fixdoc
bash scripts/setup-dev.sh
source .venv/bin/activate
```

Run `make test` (822 tests). Format with `make fmt` and lint with `make lint`.

Good places to start: new error parsers live in `src/fixdoc/parsers/` and share a common interface; new CLI commands are self-contained files in `src/fixdoc/commands/`; the GitHub Actions integration is at `.github/workflows/terraform-risk-analysis.yml`.

## License

MIT
