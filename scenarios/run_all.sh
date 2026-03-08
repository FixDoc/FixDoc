#!/usr/bin/env bash
set -euo pipefail

# ── LocalStack health check ────────────────────────────────────────────────────
LOCALSTACK_HEALTH_URL="http://localhost:4566/_localstack/health"
if ! curl -sf "$LOCALSTACK_HEALTH_URL" > /dev/null 2>&1; then
  echo "ERROR: LocalStack is not running or not reachable at $LOCALSTACK_HEALTH_URL"
  echo ""
  echo "Start it with:"
  echo "  make localstack-up"
  echo ""
  echo "Or from the repo root:"
  echo "  docker compose up -d"
  exit 1
fi
# ── End health check ──────────────────────────────────────────────────────────

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIOS_DIR="$ROOT_DIR/scenarios"

# Track dirs that we successfully applied, so we can destroy on exit if needed
APPLIED_DIRS=()

cleanup() {
  # Best-effort cleanup; don't fail cleanup if destroy fails
  set +e
  if [[ ${#APPLIED_DIRS[@]} -gt 0 ]]; then
    echo
    echo "============================================================"
    echo "Cleanup: destroying applied scenario infra (best-effort)"
    echo "============================================================"
    for d in "${APPLIED_DIRS[@]}"; do
      echo "Destroying: $(basename "$d")"
      pushd "$d" >/dev/null || continue

      # Use the scenario's fixdoc home (doesn't matter for destroy, but keeps consistent env)
      export FIXDOC_HOME="$d/.fixdoc-test"

      # These destroys match the vars used for apply in each scenario.
      # If you add more apply scenarios later, extend this case block.
      case "$(basename "$d")" in
        02-update-nonboundary)
          terraform destroy -auto-approve -var="variant=baseline" >/dev/null
          ;;
        03-boundary-sg-update)
          terraform destroy -auto-approve -var="sg_exposure=baseline" >/dev/null
          ;;
        12-analyze-iam-deep-chain)
          terraform destroy -auto-approve -var="policy_mode=baseline" >/dev/null
          ;;
        13-analyze-replace-delete-heavy)
          terraform destroy -auto-approve -var="policy_mode=baseline" >/dev/null
          ;;
        *)
          # Default: destroy with no extra vars (safe fallback)
          terraform destroy -auto-approve >/dev/null
          ;;
      esac

      popd >/dev/null
    done
  fi
}
trap cleanup EXIT

seed_fixdoc_home() {
  # Copy the shared fix database into the scenario's isolated FIXDOC_HOME
  if [[ -f "$HOME/.fixdoc/fixes.json" ]]; then
    cp "$HOME/.fixdoc/fixes.json" "$FIXDOC_HOME/fixes.json"
  fi
}

run_plan() {
  local dir="$1"
  local plan_args="${2:-}"

  pushd "$dir" >/dev/null

  # Isolate FixDoc data per scenario so your real ~/.fixdoc doesn't affect results
  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME"
  mkdir -p "$FIXDOC_HOME"
  seed_fixdoc_home

  rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true

  terraform init -upgrade -reconfigure >/dev/null

  terraform plan $plan_args -out=plan.tfplan >/dev/null
  terraform show -json plan.tfplan > plan.json

  # Human output
  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir")"
  echo "============================================================"
  fixdoc analyze plan.json || true

  # Clean JSON output (for CI + assertions)
  fixdoc analyze plan.json --format json > fixdoc.json || true

  popd >/dev/null
}

run_apply_then_plan_update_nonboundary() {
  local dir="$1"
  pushd "$dir" >/dev/null

  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME"
  mkdir -p "$FIXDOC_HOME"
  seed_fixdoc_home

  terraform init -upgrade -reconfigure >/dev/null

  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") (apply baseline -> plan change)"
  echo "============================================================"

  # Apply baseline
  terraform apply -auto-approve -var="variant=baseline" >/dev/null
  APPLIED_DIRS+=("$dir")

  # Plan changed
  rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true
  terraform plan -var="variant=changed" -out=plan.tfplan >/dev/null
  terraform show -json plan.tfplan > plan.json

  fixdoc analyze plan.json || true
  fixdoc analyze plan.json --format json > fixdoc.json || true

  # Destroy baseline infra (so scenarios don't bleed into each other)
  terraform destroy -auto-approve -var="variant=baseline" >/dev/null

  popd >/dev/null
}

run_apply_then_plan_sg_update() {
  local dir="$1"
  pushd "$dir" >/dev/null

  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME"
  mkdir -p "$FIXDOC_HOME"
  seed_fixdoc_home

  terraform init -upgrade -reconfigure >/dev/null

  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") (apply baseline -> plan exposure)"
  echo "============================================================"

  # Apply baseline (internal CIDR)
  terraform apply -auto-approve -var="sg_exposure=baseline" >/dev/null
  APPLIED_DIRS+=("$dir")

  # Plan changed (wide open)
  rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true
  terraform plan -var="sg_exposure=changed" -out=plan.tfplan >/dev/null
  terraform show -json plan.tfplan > plan.json

  fixdoc analyze plan.json || true
  fixdoc analyze plan.json --format json > fixdoc.json || true

  # Destroy baseline infra
  terraform destroy -auto-approve -var="sg_exposure=baseline" >/dev/null

  popd >/dev/null
}

run_apply_then_plan_update_boundary() {                                                                                                                  
    local dir="$1"                                                                                                                                         
    pushd "$dir" >/dev/null                                                                                                                                
                                                                                                                                                           
    export FIXDOC_HOME="$dir/.fixdoc-test"
    rm -rf "$FIXDOC_HOME"
    mkdir -p "$FIXDOC_HOME"
    seed_fixdoc_home

    terraform init -upgrade -reconfigure >/dev/null

    echo
    echo "============================================================"
    echo "Scenario: $(basename "$dir") (apply baseline -> plan change)"
    echo "============================================================"

    # Apply baseline
    terraform apply -auto-approve -var="policy_mode=baseline" >/dev/null
    APPLIED_DIRS+=("$dir")

    # Capture dependency graph while state is live
    terraform graph > graph.dot 2>/dev/null || true

    # Plan changed
    rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true                                                                                            
    terraform plan -var="policy_mode=changed" -out=plan.tfplan >/dev/null                                                                                  
    terraform show -json plan.tfplan > plan.json

    fixdoc analyze plan.json --graph graph.dot || true
    fixdoc analyze plan.json --graph graph.dot --format json > fixdoc.json || true

    # Destroy baseline infra (so scenarios don't bleed into each other)
    terraform destroy -auto-approve -var="policy_mode=baseline" >/dev/null

    popd >/dev/null
  }


run_apply_then_plan_noop() {
  local dir="$1"
  pushd "$dir" >/dev/null

  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME" && mkdir -p "$FIXDOC_HOME"
  seed_fixdoc_home

  terraform init -upgrade -reconfigure >/dev/null

  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") (apply, then plan no-op)"
  echo "============================================================"

  # Apply all resources first so second plan shows no changes
  terraform apply -auto-approve >/dev/null
  APPLIED_DIRS+=("$dir")

  # Plan again → all resources show no-op or read
  rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true
  terraform plan -out=plan.tfplan >/dev/null
  terraform show -json plan.tfplan > plan.json

  fixdoc analyze plan.json || true
  fixdoc analyze plan.json --format json > fixdoc.json || true

  # Destroy inline so APPLIED_DIRS cleanup is a safe no-op
  terraform destroy -auto-approve >/dev/null

  popd >/dev/null
}

run_watch_scenario() {
  local dir="$1"
  local tf_cmd="${2:-terraform apply -auto-approve}"

  pushd "$dir" >/dev/null

  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME" && mkdir -p "$FIXDOC_HOME"
  rm -f .fixdoc-pending

  terraform init -upgrade -reconfigure >/dev/null

  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") [watch]"
  echo "============================================================"

  # --no-prompt: auto-defers all errors, prints 1-line summary, no stdin needed
  fixdoc watch --no-prompt -- $tf_cmd || true

  # Verify .fixdoc-pending was created with at least one entry
  if [[ -f ".fixdoc-pending" ]]; then
    PENDING_COUNT=$(python3 -c \
      "import json; print(len(json.load(open('.fixdoc-pending'))))" \
      2>/dev/null || echo "?")
    echo "PASS: $PENDING_COUNT error(s) deferred to .fixdoc-pending"
  else
    echo "WARN: .fixdoc-pending not created (command may have succeeded or had no output)"
  fi

  rm -f .fixdoc-pending

  # Track apply dirs for cleanup trap
  if [[ "$tf_cmd" == *"apply"* ]]; then
    APPLIED_DIRS+=("$dir")
  fi

  popd >/dev/null
}

run_watch_scenario_plan() {
  local dir="$1"
  run_watch_scenario "$dir" "terraform plan"
}

run_watch_scenario_docs_only() {
  local dir="$1"
  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") [docs-only, real AWS required]"
  echo "============================================================"
  echo "SKIP: Requires real AWS credentials. See scenarios/RUNBOOK.md."
}

run_analyze_bad_plan() {
  local dir="$1"
  pushd "$dir" >/dev/null

  export FIXDOC_HOME="$dir/.fixdoc-test"
  rm -rf "$FIXDOC_HOME" && mkdir -p "$FIXDOC_HOME"
  seed_fixdoc_home

  rm -f plan.tfplan plan.json fixdoc.json 2>/dev/null || true

  terraform init -upgrade -reconfigure >/dev/null
  terraform plan -out=plan.tfplan >/dev/null
  terraform show -json plan.tfplan > plan.json

  echo
  echo "============================================================"
  echo "Scenario: $(basename "$dir") [bad-plan]"
  echo "============================================================"

  echo "--- Valid plan (baseline) ---"
  fixdoc analyze plan.json || true
  fixdoc analyze plan.json --format json > fixdoc.json || true

  echo "--- Fixture: empty.json ---"
  fixdoc analyze "$dir/fixtures/empty.json" 2>&1 | head -5 || true

  echo "--- Fixture: invalid.json ---"
  fixdoc analyze "$dir/fixtures/invalid.json" 2>&1 | head -5 || true

  echo "--- Fixture: no_changes.json ---"
  fixdoc analyze "$dir/fixtures/no_changes.json" 2>&1 | head -5 || true

  popd >/dev/null
}

echo "Running FixDoc scenario matrix with LocalStack..."
echo "Root: $ROOT_DIR"

# 01: plan-only greenfield
run_plan "$SCENARIOS_DIR/01-greenfield"

# 02: apply baseline then plan update
run_apply_then_plan_update_nonboundary "$SCENARIOS_DIR/02-update-nonboundary"

# 03: apply baseline then plan SG exposure update
run_apply_then_plan_sg_update "$SCENARIOS_DIR/03-boundary-sg-update"

run_apply_then_plan_update_boundary "$SCENARIOS_DIR/04-iam-boundary-update"

# Watch scenarios (05-09; 10 is real-AWS only)
run_watch_scenario      "$SCENARIOS_DIR/05-watch-multi-failure-missing-vars"
run_watch_scenario      "$SCENARIOS_DIR/06-watch-invalid-resources"
run_watch_scenario      "$SCENARIOS_DIR/07-watch-parallelism-bomb" \
                        "terraform apply -auto-approve -parallelism=10"
run_watch_scenario_plan "$SCENARIOS_DIR/08-watch-terraform-graph-errors"
run_watch_scenario      "$SCENARIOS_DIR/09-watch-multi-module-same-error"
run_watch_scenario_docs_only "$SCENARIOS_DIR/10-watch-iam-cascade-deny"

# Analyze scenarios (11-17)
run_plan                          "$SCENARIOS_DIR/11-analyze-create-only-non-boundary"
run_apply_then_plan_update_boundary "$SCENARIOS_DIR/12-analyze-iam-deep-chain"
run_apply_then_plan_update_boundary "$SCENARIOS_DIR/13-analyze-replace-delete-heavy"
run_analyze_bad_plan              "$SCENARIOS_DIR/14-analyze-bad-plan"
run_apply_then_plan_noop          "$SCENARIOS_DIR/15-analyze-no-op-plan"
run_plan                          "$SCENARIOS_DIR/16-analyze-huge-plan"
run_plan                          "$SCENARIOS_DIR/17-analyze-word-boundary-traps"

echo
echo "Done. Each scenario folder contains:"
echo "  - plan.tfplan"
echo "  - plan.json"
echo "  - fixdoc.json (analyze scenarios)"
echo "  - .fixdoc-pending (watch scenarios, if errors were deferred)"