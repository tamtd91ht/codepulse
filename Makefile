PY ?= python

.PHONY: up down lint test

up:
	docker compose up -d --build

down:
	docker compose down

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy app tests

test:
	$(PY) -m pytest
