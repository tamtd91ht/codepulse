PY ?= python

.PHONY: up down migrate seed lint test test-unit

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	docker compose run --rm api alembic upgrade head

seed:
	docker compose run --rm api python -m app.seed

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy app tests

# Integration tests start PostgreSQL through Docker (testcontainers).
test:
	$(PY) -m pytest --cov=app --cov-report=term-missing

test-unit:
	$(PY) -m pytest -m "not integration"
