.PHONY: setup test lint fmt typecheck run-api run-web

UV := uv
BACKEND := backend

setup:
	$(UV) sync --all-packages --all-groups
	cd frontend && pnpm install

test:
	$(UV) run --directory $(BACKEND) pytest

lint:
	$(UV) run ruff check $(BACKEND)
	$(UV) run ruff format --check $(BACKEND)
	$(UV) run --directory $(BACKEND) mypy
	cd $(BACKEND) && $(UV) run lint-imports --config ../.importlinter
	cd frontend && pnpm lint

fmt:
	$(UV) run ruff check --fix $(BACKEND)
	$(UV) run ruff format $(BACKEND)

run-api:
	$(UV) run uvicorn dce.api.app:app --reload --port 8000

run-web:
	cd frontend && pnpm dev
