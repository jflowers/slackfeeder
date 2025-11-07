.PHONY: install install-dev format lint test requirements help

help:
	@echo "Available commands:"
	@echo "  make install       - Install runtime dependencies"
	@echo "  make install-dev   - Install with development dependencies"
	@echo "  make format        - Format code with black and isort"
	@echo "  make lint          - Run pylint"
	@echo "  make test          - Run tests"
	@echo "  make requirements  - Generate requirements.txt from pyproject.toml"

install:
	pip install .

install-dev:
	pip install -e ".[dev]"

format:
	black src/ tests/
	isort src/ tests/

lint:
	pylint src/ tests/

test:
	pytest

requirements:
	@./scripts/update_requirements.sh
