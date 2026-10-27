PY ?= python

.PHONY: up down migrate seed lint test test-unit eval eval-knowledge

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

# Calls the real LLM (costs money): run when the prompt, rubric, model or CLI version changes.
eval:
	$(PY) -m app.review.eval

# Per-project eval generated from the active review knowledge (docs/specs/19 §5).
# Generated cases are cached under tests/eval/generated/<project>/; add FAKE=1 for a free harness run.
eval-knowledge:
	$(PY) -m app.review.eval --project $(PROJECT) $(if $(FAKE),--fake,)
