PYTHON ?= python3

.PHONY: dev run-once test

dev:
	$(PYTHON) -m app.run_ui

run-once:
	$(PYTHON) -m app.run_once

test:
	$(PYTHON) -m pytest -q

