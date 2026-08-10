PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python))

.PHONY: server agent analyzer analyzer-worker test testsets eval coverage lint fmt demo proto deploy deploy-down package-native

proto:
	cd proto && bash compile.sh

server:
	$(PYTHON) -m server.app.main

agent:
	$(PYTHON) -m agent.mini_drop_agent.main

analyzer:
	$(PYTHON) -m analyzer.mini_drop_analyzer.hotmethod_analyzer \
		--task-id demo_task \
		--config analyzer/config.example.toml

analyzer-worker:
	$(PYTHON) -m analyzer.mini_drop_analyzer.worker

test:
	$(PYTHON) -m pytest tests -v

testsets:
	$(PYTHON) scripts/validate_testsets.py testsets
	@if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then \
		docker compose -f testsets/real/online-boutique/deploy/docker-compose.trimmed.yaml config --quiet; \
	else echo "[testsets] docker compose unavailable; compose parse skipped"; fi

eval:
	$(PYTHON) scripts/run_diagnosis_eval.py --output-dir reports/eval

coverage:
	$(PYTHON) -m pytest --cov=server --cov=agent --cov=analyzer --cov-report=term-missing tests

lint:
	$(PYTHON) -m compileall server agent analyzer demo
	@echo "[lint] compileall passed"
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff check server agent analyzer || echo "[lint] ruff not installed (pip install ruff), skipping"
	@which mypy >/dev/null 2>&1 && $(PYTHON) -m mypy server agent analyzer --ignore-missing-imports || echo "[lint] mypy not installed (pip install mypy), skipping"

fmt:
	@which ruff >/dev/null 2>&1 && $(PYTHON) -m ruff format server agent analyzer demo tests || echo "[fmt] ruff not installed, skipping"

demo:
	bash demo/demo.sh

deploy:
	docker compose up -d

deploy-down:
	docker compose down

package-native:
	@test -n "$(RELEASE_NAME)" || { echo "usage: make package-native RELEASE_NAME=mini-drop-release-YYYYMMDD-name [OUTPUT=/tmp/release.tar.gz]" >&2; exit 2; }
	bash deploy/scripts/package-native-release.sh "$(RELEASE_NAME)" $(if $(OUTPUT),"$(OUTPUT)",)
