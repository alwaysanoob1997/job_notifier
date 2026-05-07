# LinkedIn Automation — task runner.
# Run `make` or `make help` to see available targets.

PYTHON ?= python3
VENV   ?= .venv
VENV_BIN := $(VENV)/bin
HOST   ?= 127.0.0.1
PORT   ?= 8000

.DEFAULT_GOAL := help
.PHONY: help run dev install venv test clean scrape build

help:
	@echo "Available targets:"
	@echo "  make run        Start the web app on $(HOST):$(PORT)"
	@echo "  make dev        Start the web app with --reload (auto-restart on changes)"
	@echo "  make scrape     Run a single headless scrape (python -m app)"
	@echo "  make build      Build the standalone LinkedInJobs.app (macOS only)"
	@echo "  make install    Create $(VENV) and install requirements.txt"
	@echo "  make venv       Create $(VENV) only"
	@echo "  make test       Run pytest"
	@echo "  make clean      Remove caches and build artifacts"
	@echo ""
	@echo "Overrides: HOST=0.0.0.0 PORT=9000 make run"

run:
	./run.sh

dev:
	./run.sh --reload

scrape:
	$(VENV_BIN)/python -m app

build:
	bash scripts/build_macos_app.sh

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -r requirements.txt

test:
	$(VENV_BIN)/pytest tests/

clean:
	rm -rf .pytest_cache build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
