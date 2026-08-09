PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: setup test evaluate verify demo-reset demo run

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install -r requirements.lock -r requirements-dev.txt

test:
	$(VENV_PYTHON) -m unittest discover -s tests -v

evaluate:
	$(VENV_PYTHON) scripts/run_evaluation.py

verify: test evaluate

demo-reset:
	$(VENV_PYTHON) scripts/seed_demo.py --db data/revguard.db --reset --gateway-state data/revguard.gateway.json

demo:
	$(VENV_PYTHON) scripts/run_demo.py

run: demo-reset
	REVGUARD_ALLOW_INSECURE_DEMO_KEYS=true \
	REVGUARD_APPROVAL_SIGNING_KEY=revguard-demo-signing-key-change-before-production-2026 \
	$(VENV_PYTHON) -m uvicorn revguard.api:app --host 127.0.0.1 --port 9000
