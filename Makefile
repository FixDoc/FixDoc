.DEFAULT_GOAL := help
VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: help check-deps setup dev test test-unit test-integration lint fmt \
        localstack-up localstack-down localstack-health scenarios clean

help: ## List all targets with descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' \
		| sort

check-deps: ## Verify Python 3.9+, Docker, Docker Compose, and Terraform 1.5+ are present
	@bash scripts/setup-dev.sh --check-only

setup: ## Create .venv and install package in development mode
	@if [ ! -d "$(VENV)" ]; then \
		python3 -m venv $(VENV); \
	fi
	@$(PIP) install --quiet -e ".[dev]"
	@echo "Setup complete. Activate with: source $(VENV)/bin/activate"

dev: check-deps setup localstack-up ## Full dev setup: check deps, install packages, start LocalStack

test: ## Run full pytest suite
	@$(PYTHON) -m pytest

test-unit: ## Run unit tests only (exclude integration)
	@$(PYTHON) -m pytest -m "not integration" tests/

test-integration: ## Run integration tests only
	@$(PYTHON) -m pytest tests/test_integration_terraform.py

lint: ## Run ruff linter on src/ and tests/
	@$(VENV)/bin/ruff check src/ tests/

fmt: ## Format code with black
	@$(VENV)/bin/black src/ tests/

localstack-up: ## Start LocalStack (Docker Compose, detached), waits for health
	@docker compose up -d
	@echo "Waiting for LocalStack to be ready..."
	@for i in $$(seq 1 30); do \
		if curl -sf http://localhost:4566/_localstack/health > /dev/null 2>&1; then \
			echo "LocalStack is ready."; \
			break; \
		fi; \
		if [ $$i -eq 30 ]; then \
			echo "LocalStack did not become healthy within 30s."; \
			exit 1; \
		fi; \
		sleep 1; \
	done

localstack-down: ## Stop LocalStack
	@docker compose down

localstack-health: ## Check LocalStack health endpoint
	@curl -s http://localhost:4566/_localstack/health | python3 -m json.tool

scenarios: ## Run the full LocalStack scenario matrix (scenarios/run_all.sh)
	@bash scenarios/run_all.sh

clean: ## Remove .venv, __pycache__, .pytest_cache, and build artifacts
	@rm -rf $(VENV) .pytest_cache
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete."
