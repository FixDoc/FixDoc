.DEFAULT_GOAL := help
VENV := .venv
# Prefer the newest interpreter for bootstrapping the venv (floor: 3.10)
PY_BOOT := $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip
# Retrieval gate floor. A ratchet, not a target: with 10 golden cases 0.9
# allows one miss; raise it as the set grows. Traps always gate at zero.
EVAL_MIN_RECALL := 0.9

.PHONY: help setup test e2e eval lint fmt clean

help: ## List all targets with descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' \
		| sort

setup: ## Create .venv and install package with dev + embedding deps
	@if [ ! -d "$(VENV)" ]; then \
		$(PY_BOOT) -m venv $(VENV); \
	fi
	@$(PIP) install --quiet -e ".[dev,embed]"
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"

test: ## Run the test suite
	@$(PYTHON) -m pytest -q

e2e: ## End-to-end check with real embeddings (needs [embed] installed)
	@$(PYTHON) scripts/e2e_mcp.py

eval: ## Retrieval eval with real embeddings, gated on Recall@3 and traps
	@$(VENV)/bin/fixdoc eval retrieval --min-recall $(EVAL_MIN_RECALL)

lint: ## Ruff check
	@$(VENV)/bin/ruff check src/ tests/

fmt: ## Black format
	@$(VENV)/bin/black src/ tests/

clean: ## Remove venv, caches, build artifacts
	@rm -rf $(VENV) build dist .pytest_cache src/*.egg-info
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
