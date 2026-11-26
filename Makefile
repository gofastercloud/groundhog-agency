.PHONY: verify fmt test report

UV ?= uv

verify:  ## lint, type-check and test (must be green before any PR)
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	pyright --pythonpath .venv/bin/python
	$(UV) run pytest -q

fmt:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

report:  ## spend by agent type across all projects, last 7 days
	python3 harness/.claude/hooks/gtq.py report --all --days 7 --by agent_type
