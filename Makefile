.PHONY: dev run-once test

dev:
	python -m app.run_ui

run-once:
	python -m app.run_once

test:
	pytest -q

