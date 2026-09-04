# One command to prove nothing is broken. Every agent runs this before committing;
# the pre-commit hook (make hooks) runs it automatically.

PY := .venv/bin/python
COV_MIN := 85

.PHONY: check test lint cov examples hooks

check: lint test cov          ## lint + tests + coverage gate

lint:
	.venv/bin/ruff check iacsim tests

test:
	.venv/bin/pytest -q

cov:                          ## fail if coverage drops below COV_MIN
	.venv/bin/pytest -q --cov=iacsim --cov-report=term-missing:skip-covered --cov-fail-under=$(COV_MIN)

examples:                     ## smoke: every example still parses, runs, diffs
	.venv/bin/iacsim graph examples/classic-web
	.venv/bin/iacsim graph examples/classic-web-bad
	.venv/bin/iacsim graph examples/foosh-serverless
	.venv/bin/iacsim run   examples/classic-web -o json
	.venv/bin/iacsim diff  examples/classic-web examples/classic-web-bad -o json

hooks:                        ## install the git pre-commit hook
	printf '#!/bin/sh\nmake check\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed: every commit runs make check"
