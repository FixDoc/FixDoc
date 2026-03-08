# FixDoc

Stop losing infrastructure fixes in Slack threads — capture, search, and share them from your terminal.

FixDoc is a CLI tool for cloud engineers that turns raw Terraform and Kubernetes error output into a searchable, version-controlled knowledge base that's risk aware and CI gate friendly. With Fixdoc you are building an intelligent error knowledge platform that guards your deployments and lets you understand wider infrastructure impact through smart analysis.

## See It In Action

```bash
# Pipe errors directly from any command
terraform apply 2>&1 | fixdoc capture

# Or wrap the command — auto-captures on failure
fixdoc watch -- terraform apply

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

## Try It in 30 Seconds

Not sure what FixDoc does yet? Run the interactive tour:

```bash
fixdoc demo tour
```

The tour walks you through five steps — capturing a real Terraform AWS error, capturing a Kubernetes CrashLoopBackOff, searching your database, viewing list and stats, and analyzing a Terraform plan — all without needing an actual cloud account. Every fix captured during the tour is saved to your local database so you can explore it afterwards with `fixdoc list` and `fixdoc show`.

If you just want to pre-populate your database with realistic example fixes to try searching and analysis:

```bash
fixdoc demo seed
```

This adds six sample fixes covering common AWS and Kubernetes failures, all tagged `demo` so you can clean them up later:

```bash
fixdoc demo seed --clean   # remove old demo data before re-seeding
```

## What It Does

**Core workflow:**
- Pipe or wrap any command — auto-detects Terraform and Kubernetes errors and prompts for your resolution
- Surfaces similar fixes from your history before creating duplicates, so you find the existing answer instead of writing a redundant entry
- Stores everything as searchable JSON plus human-readable markdown in `~/.fixdoc/`

**Team and CI features:**
- Sync fixes with your team via a shared Git repo
- Analyze Terraform plans against your fix history before `apply` to surface known failure patterns
- Gate CI pipelines on blast radius severity (`--exit-on high`)
- Defer errors to a pending queue and capture them later when you have time

## Installation

**Recommended — pipx** (installs into an isolated environment and puts `fixdoc` on your PATH automatically):

```bash
pipx install fixdoc
```

**pip:**

```bash
pip install fixdoc
```

> **Note:** After installing with pip, make sure `fixdoc` is on your PATH. If the command isn't found, your Python scripts directory may not be in `PATH`. You can fix this by running `pip show -f fixdoc | grep fixdoc` to find the binary location and adding that directory to your `PATH`, or by using `python3 -m fixdoc` as an alternative.

Requires Python 3.9+. Only runtime dependencies are `click` and `pyyaml`.

## Command Reference

### Capturing Fixes

**Pipe mode** — run your command and pipe its output into fixdoc:

```bash
terraform apply 2>&1 | fixdoc capture
kubectl apply -f deployment.yaml 2>&1 | fixdoc capture
```

FixDoc reads from stdin, auto-detects the error source (Terraform, Kubernetes, or generic), extracts structured metadata (provider, resource type, error code), checks your history for similar fixes, and prompts you to write the resolution.

**Watch mode** — wrap your command so FixDoc captures errors automatically on failure:

```bash
fixdoc watch -- terraform apply
fixdoc watch --tags aws,prod -- terraform apply -target=aws_instance.web
```

On success, the wrapped command exits normally. On failure, FixDoc parses the output and presents all detected errors. If multiple errors are found, it shows a summary table and lets you handle each one individually — capture a new fix, use an existing match, skip, or defer to the pending queue. The original exit code is always preserved, so this is safe to drop into any CI script.

**Quick capture** — write a fix directly without piping anything:

```bash
fixdoc capture --issue "RDS connection timeout" --resolution "Increase connection pool size in db.py"
```

### Searching and Browsing

```bash
fixdoc search "S3 access denied"     # keyword search across all fix fields
fixdoc search "crashloop" --limit 5  # limit results
fixdoc show abc12345                  # view a single fix in full detail
fixdoc list                           # list all fixes, most recent first
fixdoc list --limit 20                # show more
fixdoc stats                          # tag distribution, totals, coverage
```

Search matches against the issue description, resolution text, tags, error excerpt, and resource metadata — so `fixdoc search aws_s3_bucket` finds fixes that mention that resource type even if the search term doesn't appear in the summary.

### Editing and Deleting

```bash
fixdoc edit abc12345    # open a fix in your editor to update issue, resolution, or tags
fixdoc delete abc12345  # permanently remove a fix
```

### Analyzing Terraform Plans

Before running `terraform apply`, generate a plan JSON and run it through FixDoc to surface any resources that have caused problems in your history:

```bash
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan > plan.json
fixdoc analyze plan.json
```

FixDoc reads the plan, identifies which resources are changing, traverses the Terraform dependency graph, and scores the potential blast radius of the change. It also matches changing resources against your fix history and flags any known failure patterns with their stored resolutions.

```
Terraform Plan Analysis
=======================
5 resources changing (2 create, 1 update, 2 delete)

Risk Score: 61 / 100  [HIGH]

Changes:
  CREATE    aws_iam_role.deploy_role   [iam boundary]
  UPDATE    aws_lambda_function.api
  DELETE    aws_security_group.old_sg  [network boundary]

Impacted Resources (8):
  aws_lambda_function.api              (depth: 1, via: aws_iam_role.deploy_role)
  aws_rds_cluster.main                 (depth: 2, via: aws_security_group.old_sg)
  ...

Risk Warnings from History (2):
  FIX-3a8f12c4: IAM role missing lambda:InvokeFunction — lambda failed silently
  FIX-91b0e5aa: SG deletion broke RDS inbound rule — apply rolled back
```

Options:

```bash
fixdoc analyze plan.json --graph graph.dot       # provide DOT graph explicitly
fixdoc analyze plan.json --format json           # machine-readable output
fixdoc analyze plan.json --summary               # one-line risk summary
fixdoc analyze plan.json --exit-on high          # exit code 1 if HIGH or CRITICAL
fixdoc analyze plan.json --match strict          # strict history matching
fixdoc analyze plan.json --max-depth 3           # limit dependency traversal
```

If terraform is on your PATH, FixDoc auto-runs `terraform graph` to build the dependency DAG — no `--graph` flag needed.

### Blast Radius (Standalone)

The blast radius engine is also available as a standalone command if you want just the risk score without the history matching:

```bash
fixdoc blast-radius plan.json
fixdoc blast-radius plan.json --format json --exit-on critical
```

### Pending Queue

When you're in the middle of a deployment and don't have time to write up a fix, defer the error to the pending queue and come back to it later:

```bash
# During a watch session, choose "defer" for any error
fixdoc watch -- terraform apply

# Later, list what's pending
fixdoc pending

# Capture a specific pending error by number or ID
fixdoc pending capture 1
fixdoc pending capture a3f9

# Remove one without capturing
fixdoc pending remove 1

# Clear everything
fixdoc pending clear
```

Pending entries are stored in `.fixdoc-pending` at your git repository root, so they're project-scoped and survive terminal sessions.

### Team Sync

Share fixes with your team via a shared Git repo:

```bash
fixdoc sync init git@github.com:your-org/infra-fixes.git
fixdoc sync push                # push your fixes to the shared repo
fixdoc sync pull                # pull and merge fixes from teammates
fixdoc sync status              # show sync state
```

Fixes marked as private (`is_private: true`) are always excluded from sync. On pull, FixDoc rebuilds the JSON database from the markdown files in the shared repo and merges with your local data, handling conflicts for fixes that were modified on both sides.

## How It Works

Source-specific parsers (Terraform, Kubernetes, and a generic fallback) extract structured metadata from raw error output — provider, resource type, error code, resource address — so fixes are queryable by field, not just full-text searchable. A similarity engine scores your existing fixes before capture so you see potentially matching resolutions before writing a new entry.

Everything persists as structured JSON plus a human-readable markdown file per fix, so your database is both machine-queryable and readable in any editor or GitHub repository. The analysis command traverses your Terraform dependency graph using bounded BFS propagation and scores blast radius with a sigmoid formula that weights affected resource count, control point criticality (IAM, RBAC, network boundaries), action type, and fix history. Sensitive field values are redacted before any output is written.

## CI Integration

Quick start — add this to any workflow that generates a Terraform plan:

```yaml
- name: Analyze risk
  run: |
    terraform show -json plan.tfplan > plan.json
    fixdoc analyze plan.json --exit-on high
```

### Review Modes

The included GitHub Actions workflow (`.github/workflows/terraform-risk-analysis.yml`) supports three review modes via the `REVIEW_MODE` env var or `workflow_dispatch` input:

| Mode | Behavior |
|------|----------|
| `advisory` (default) | Posts analysis to job summary + PR comment. Never blocks. |
| `warn` | Same as advisory + adds GitHub warning annotation if high/critical. |
| `gate` | Same as warn + fails the workflow if severity >= `GATE_THRESHOLD`. |

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

The workflow uses `--format markdown` for PR comments and `--format json` to extract severity for gating decisions.

See `.github/workflows/terraform-risk-analysis.yml` for the full workflow with matrix strategy, PR comments, and review mode support.

## Local Development & Testing

Prerequisites: Python 3.9+, Docker, Docker Compose, Terraform ≥ 1.5

Quick start:

```bash
bash scripts/setup-dev.sh   # check deps + install Python packages
make localstack-up          # start LocalStack mock AWS (port 4566)
make test                   # run 579 tests
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

Run `make test` (579 tests). Format with `make fmt` and lint with `make lint`.

Good places to start: new error parsers live in `src/fixdoc/parsers/` and share a common interface; new CLI commands are self-contained files in `src/fixdoc/commands/`; the GitHub Actions integration is at `.github/workflows/terraform-risk-analysis.yml`.

## License

MIT
