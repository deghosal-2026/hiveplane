.PHONY: help install test cov lint type check fmt clean

help:
	@echo "HivePlane developer targets:"
	@echo "  make install  Install package with dev extras (editable)"
	@echo "  make test     Run the test suite"
	@echo "  make cov      Run tests with coverage report"
	@echo "  make lint     Run ruff"
	@echo "  make type     Run mypy (strict)"
	@echo "  make check    lint + type + cov"
	@echo "  make fmt      Auto-fix lint issues"
	@echo "  make clean    Remove caches and build artifacts"

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

cov:
	python -m pytest --cov=src/hiveplane --cov-report=term-missing

lint:
	python -m ruff check

type:
	python -m mypy src/ tests/

check: lint type cov

fmt:
	python -m ruff check --fix
	python -m ruff format

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
