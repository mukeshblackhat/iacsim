# One command to prove nothing is broken. Every agent runs this before committing;
# the pre-commit hook (make hooks) runs it automatically.

PY := .venv/bin/python
COV_MIN := 85

.PHONY: check test lint cov examples dashboard-sample dashboard hooks ci

check: lint test cov          ## lint + tests + coverage gate

ci: check examples            ## what GitHub Actions runs

lint:
	.venv/bin/ruff check iacsim tests   # line length + rule set come from pyproject.toml

test:
	.venv/bin/pytest -q

cov:                          ## fail if coverage drops below COV_MIN
	.venv/bin/pytest -q --cov=iacsim --cov-report=term-missing:skip-covered --cov-fail-under=$(COV_MIN)

examples:                     ## smoke: every example still parses, runs, diffs
	.venv/bin/iacsim graph examples/classic-web
	.venv/bin/iacsim graph examples/classic-web-bad
	.venv/bin/iacsim graph examples/foosh-serverless
	.venv/bin/iacsim graph examples/order-queue
	.venv/bin/iacsim run   examples/order-queue -o json
	.venv/bin/iacsim run   examples/classic-web -o json
	.venv/bin/iacsim diff  examples/classic-web examples/classic-web-bad -o json
	.venv/bin/iacsim graph examples/real-world/ecs-alb --region us-east-1
	.venv/bin/iacsim run   examples/real-world/two-tier -o json
	.venv/bin/iacsim validate examples/real-world/serverless-apigw-lambda-dynamodb
	.venv/bin/iacsim graph examples/gcp-web
	.venv/bin/iacsim graph examples/gcp-web-bad
	.venv/bin/iacsim run   examples/gcp-web -o json
	.venv/bin/iacsim diff  examples/gcp-web examples/gcp-web-bad -o json
	.venv/bin/iacsim graph examples/real-world/gcp-glb-mig-backend
	.venv/bin/iacsim run   examples/real-world/gcp-ntier-serverless-web -o json
	$(MAKE) dashboard-sample

dashboard-sample:             ## regenerate examples/dashboard/ (real output; tests/test_dashboard_sample.py holds it)
	.venv/bin/iacsim run   examples/gcp-web -o json -o html
	cp examples/gcp-web/.iacsim/report.json examples/gcp-web/.iacsim/report.html examples/dashboard/gcp-web/
	.venv/bin/iacsim run   examples/foosh-serverless --walker load -o json -o html
	cp examples/foosh-serverless/.iacsim/report.json examples/foosh-serverless/.iacsim/report.html examples/dashboard/foosh-load/
	.venv/bin/iacsim diff  examples/classic-web examples/classic-web-bad -o json -o html
	cp examples/classic-web-bad/.iacsim/diff.json examples/classic-web-bad/.iacsim/diff.html examples/dashboard/classic-web-diff/

dashboard:                    ## open the sample dashboard in the browser
	open examples/dashboard/gcp-web/report.html 2>/dev/null || xdg-open examples/dashboard/gcp-web/report.html

hooks:                        ## install the git pre-commit hook
	printf '#!/bin/sh\nmake check\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
	@echo "pre-commit hook installed: every commit runs make check"
