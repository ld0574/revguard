PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: setup lint test coverage evaluate openapi generated-check security \
	verify verify-ci verify-release demo-reset demo run demo-ui-build demo-ui

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install -r requirements.lock -r requirements-dev.txt

test:
	$(VENV_PYTHON) -m unittest discover -s tests -v

lint:
	$(VENV_PYTHON) -m ruff check revguard scripts tests

coverage:
	$(VENV_PYTHON) -m coverage run --source=revguard -m unittest discover -s tests
	$(VENV_PYTHON) -m coverage report --fail-under=90

evaluate:
	$(VENV_PYTHON) scripts/run_evaluation.py

openapi:
	$(VENV_PYTHON) scripts/gen_skill_docs.py
	$(VENV_PYTHON) scripts/export_openapi.py

generated-check:
	$(VENV_PYTHON) scripts/gen_skill_docs.py --check
	$(VENV_PYTHON) scripts/export_openapi.py --check
	$(VENV_PYTHON) scripts/validate_evaluation_snapshot.py

security:
	$(VENV_PYTHON) -m pip_audit -r requirements.lock
	$(VENV_PYTHON) -m bandit -q -r revguard scripts

verify: test evaluate

verify-ci: lint coverage evaluate generated-check

verify-release: verify-ci security

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
