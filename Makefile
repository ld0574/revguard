PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: setup lint test coverage evaluate value-evaluate synthetic-validate evidence-bundle capacity postgres-integration openapi generated-check security \
	verify verify-ci verify-release competition-verify demo-reset demo run demo-ui-build demo-ui deploy-local deploy-full

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install -r requirements.lock -r requirements-dev.txt

test:
	$(VENV_PYTHON) -m unittest discover -s tests -v

lint:
	$(VENV_PYTHON) -m ruff check revguard scripts tests

coverage:
	$(VENV_PYTHON) -m coverage run --source=revguard -m unittest discover -s tests
	$(VENV_PYTHON) -m coverage report --omit=revguard/postgres_store.py --fail-under=90

evaluate:
	$(VENV_PYTHON) scripts/run_evaluation.py

value-evaluate:
	$(VENV_PYTHON) scripts/run_value_evaluation.py \
		--input data/value_baseline/synthetic_demo.csv \
		--output docs/value-evaluation-synthetic.json

synthetic-validate:
	$(VENV_PYTHON) scripts/validate_synthetic_dataset.py \
		--output docs/synthetic-data-validation.json

evidence-bundle:
	$(VENV_PYTHON) scripts/build_competition_evidence.py \
		--output docs/evidence/demo-rehearsal

capacity:
	$(VENV_PYTHON) scripts/run_capacity_probe.py --cases 200 --concurrency 20 \
		--output docs/capacity-baseline-local.json

postgres-integration:
	@test -n "$(REVGUARD_TEST_POSTGRES_DSN)" || \
		(echo "set REVGUARD_TEST_POSTGRES_DSN to a disposable PostgreSQL database" >&2; exit 2)
	REVGUARD_TEST_POSTGRES_DSN="$(REVGUARD_TEST_POSTGRES_DSN)" \
		$(VENV_PYTHON) -m unittest tests.test_postgres_store_integration -v

openapi:
	$(VENV_PYTHON) scripts/gen_skill_docs.py
	$(VENV_PYTHON) scripts/export_openapi.py

generated-check:
	$(VENV_PYTHON) scripts/gen_skill_docs.py --check
	$(VENV_PYTHON) scripts/export_openapi.py --check
	$(VENV_PYTHON) scripts/validate_evaluation_snapshot.py
	$(VENV_PYTHON) scripts/validate_synthetic_dataset.py \
		--check docs/synthetic-data-validation.json

security:
	$(VENV_PYTHON) -m pip_audit -r requirements.lock
	$(VENV_PYTHON) -m bandit -q -r revguard scripts

verify: test evaluate

verify-ci: lint coverage evaluate value-evaluate generated-check

verify-release: verify-ci security

competition-verify: verify-release demo-ui-build evidence-bundle

demo-reset:
	$(VENV_PYTHON) scripts/seed_demo.py --db data/revguard.db --reset --gateway-state data/revguard.gateway.json

demo:
	$(VENV_PYTHON) scripts/run_demo.py

run: demo-reset
	REVGUARD_ALLOW_INSECURE_DEMO_KEYS=true \
	REVGUARD_APPROVAL_SIGNING_KEY=revguard-demo-signing-key-change-before-production-2026 \
	$(VENV_PYTHON) -m uvicorn revguard.api:app --host 127.0.0.1 --port 9000

demo-ui-build:
	npm --prefix demo-ui run build

demo-ui: demo-ui-build demo-reset
	REVGUARD_ALLOW_INSECURE_DEMO_KEYS=true \
	REVGUARD_ENABLE_RECORDING_UI=true \
	REVGUARD_APPROVAL_MODE=wait \
	REVGUARD_FINANCE_FAIL_TIMES=1 \
	REVGUARD_VERIFICATION_TAMPER_AMOUNT=1 \
	REVGUARD_APPROVAL_SIGNING_KEY=revguard-demo-signing-key-change-before-production-2026 \
	$(VENV_PYTHON) -m uvicorn revguard.api:app --host 127.0.0.1 --port 9000

deploy-local:
	bash scripts/deploy_demo.sh --local

deploy-full:
	bash scripts/deploy_demo.sh --full
