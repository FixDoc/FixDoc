#!/usr/bin/env bash
# setup-dev.sh — Check prerequisites and set up the FixDoc development environment.
# Usage:
#   bash scripts/setup-dev.sh            # check deps + install Python packages
#   bash scripts/setup-dev.sh --check-only  # only check deps, exit 0/1

set -euo pipefail

CHECK_ONLY=false
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }

ERRORS=0

echo "FixDoc developer environment setup"
echo "==================================="
echo

# ── Python 3.9+ ────────────────────────────────────────────────────────────────
echo "Checking Python 3.9+..."
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
  PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
  if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]]; then
    ok "Python $PY_VERSION found at $(command -v python3)"
  else
    fail "Python $PY_VERSION is too old — need 3.9+"
    warn "Install instructions:"
    warn "  macOS:  brew install python@3.11"
    warn "  Ubuntu: sudo apt install python3.11 python3.11-venv"
    warn "  Other:  https://www.python.org/downloads/"
    ERRORS=$((ERRORS + 1))
  fi
else
  fail "python3 not found"
  warn "Install instructions:"
  warn "  macOS:  brew install python@3.11"
  warn "  Ubuntu: sudo apt install python3.11 python3.11-venv"
  warn "  Other:  https://www.python.org/downloads/"
  ERRORS=$((ERRORS + 1))
fi

# ── Docker daemon ──────────────────────────────────────────────────────────────
echo
echo "Checking Docker..."
if command -v docker &>/dev/null; then
  if docker info &>/dev/null 2>&1; then
    DOCKER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
    ok "Docker $DOCKER_VERSION daemon is running"
  else
    fail "Docker is installed but the daemon is not running"
    warn "Start Docker Desktop or run: sudo systemctl start docker"
    ERRORS=$((ERRORS + 1))
  fi
else
  fail "docker not found"
  warn "Install instructions:"
  warn "  macOS:  https://www.docker.com/products/docker-desktop/"
  warn "  Ubuntu: sudo apt install docker.io && sudo systemctl enable --now docker"
  ERRORS=$((ERRORS + 1))
fi

# ── Docker Compose plugin ──────────────────────────────────────────────────────
echo
echo "Checking Docker Compose..."
if docker compose version &>/dev/null 2>&1; then
  DC_VERSION=$(docker compose version --short 2>/dev/null || echo "unknown")
  ok "Docker Compose $DC_VERSION plugin found"
else
  fail "docker compose plugin not found (need 'docker compose', not 'docker-compose')"
  warn "Install instructions:"
  warn "  macOS:  Included with Docker Desktop — update Docker Desktop"
  warn "  Ubuntu: sudo apt install docker-compose-plugin"
  warn "  Other:  https://docs.docker.com/compose/install/"
  ERRORS=$((ERRORS + 1))
fi

# ── Terraform 1.5+ ────────────────────────────────────────────────────────────
echo
echo "Checking Terraform 1.5+..."
if command -v terraform &>/dev/null; then
  TF_VERSION=$(terraform version -json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['terraform_version'])" 2>/dev/null \
    || terraform version | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  TF_MINOR=$(echo "$TF_VERSION" | cut -d. -f2)
  TF_MAJOR=$(echo "$TF_VERSION" | cut -d. -f1)
  if [[ "$TF_MAJOR" -ge 1 && "$TF_MINOR" -ge 5 ]]; then
    ok "Terraform $TF_VERSION found at $(command -v terraform)"
  else
    fail "Terraform $TF_VERSION is too old — need 1.5+"
    warn "Install instructions:"
    warn "  macOS:  brew install tfenv && tfenv install 1.9.0 && tfenv use 1.9.0"
    warn "  Ubuntu: https://developer.hashicorp.com/terraform/install#linux"
    warn "  tfenv:  https://github.com/tfutils/tfenv"
    ERRORS=$((ERRORS + 1))
  fi
else
  fail "terraform not found"
  warn "Install instructions:"
  warn "  macOS:  brew install tfenv && tfenv install 1.9.0 && tfenv use 1.9.0"
  warn "  Ubuntu: https://developer.hashicorp.com/terraform/install#linux"
  warn "  tfenv:  https://github.com/tfutils/tfenv"
  ERRORS=$((ERRORS + 1))
fi

# ── Result ─────────────────────────────────────────────────────────────────────
echo
if [[ "$ERRORS" -gt 0 ]]; then
  fail "$ERRORS prerequisite(s) missing — fix the issues above and re-run this script."
  exit 1
fi

ok "All prerequisites satisfied."

if [[ "$CHECK_ONLY" == "true" ]]; then
  exit 0
fi

# ── Python virtual environment + package install ───────────────────────────────
echo
echo "Setting up Python virtual environment..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
  ok "Created .venv"
else
  ok ".venv already exists — skipping creation"
fi

echo "Installing FixDoc in development mode..."
.venv/bin/pip install --quiet -e ".[dev]"
ok "fixdoc installed in .venv"

echo
echo "Setup complete!"
echo
echo "Next steps:"
echo "  source .venv/bin/activate    # activate the virtual environment"
echo "  make localstack-up           # start LocalStack mock AWS (port 4566)"
echo "  make test                    # run 579 tests"
echo "  make scenarios               # run full LocalStack scenario matrix"
echo "  make help                    # see all available make targets"
