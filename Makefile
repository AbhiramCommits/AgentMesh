.PHONY: help dev-up dev-down init-db seed test lint typecheck format demo bench web-install web-test clean

PY ?= python3.11
VENV ?= .venv
BIN := $(VENV)/bin

help:
	@echo "Targets:"
	@echo "  dev-up      Start postgres+pgvector and redis via docker compose"
	@echo "  dev-down    Stop docker compose services"
	@echo "  init-db     Create pgvector extension, tables, and indexes"
	@echo "  seed        Ingest the sample document corpus"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  typecheck   Run mypy"
	@echo "  demo        Run a demo workflow end to end"
	@echo "  bench       Run the benchmark harness"
	@echo "  web-install Install web dependencies"
	@echo "  web-test    Run Vitest"

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

dev-up:
	docker compose up -d postgres redis

dev-down:
	docker compose down

init-db:
	$(BIN)/python scripts/init_db.py

seed:
	$(BIN)/python scripts/seed_docs.py

test:
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff check .

typecheck:
	$(BIN)/mypy agentmesh

format:
	$(BIN)/ruff check --fix .

demo:
	$(BIN)/python scripts/demo.py

bench:
	$(BIN)/python scripts/benchmark.py

web-install:
	cd web && npm install

web-test:
	cd web && npm test

clean:
	rm -rf $(VENV) web/node_modules results/*.tmp
