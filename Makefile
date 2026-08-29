.PHONY: preflight schema generate-data load verify-data test-analysis mcp-smoke \
        extractor-test test-agent test-report demo api-test verify-all lint \
        ch-up ch-down ch-restart ch-status ch-logs ch-backup ch-restore ch-maintain

UV := uv run

# ---- Local ClickHouse lifecycle + maintenance (dev/demo/tests only) ----
# Docker-free native server under .local-clickhouse/; data persists across
# restarts. The deployed Cloud Run services do NOT use this instance.
ch-up:
	bash scripts/clickhouse.sh up
ch-down:
	bash scripts/clickhouse.sh down
ch-restart:
	bash scripts/clickhouse.sh restart
ch-status:
	bash scripts/clickhouse.sh status
ch-logs:
	bash scripts/clickhouse.sh logs
ch-backup:
	bash scripts/clickhouse.sh backup
ch-restore:
	@test -n "$(DIR)" || { echo "usage: make ch-restore DIR=backups/clickhouse/<name>"; exit 2; }
	bash scripts/clickhouse.sh restore "$(DIR)"
ch-maintain:
	bash scripts/clickhouse.sh maintain

preflight:
	$(UV) python scripts/preflight.py

preflight-report:
	$(UV) python scripts/preflight.py --report-only

schema:
	$(UV) python -m ingest.apply_schema

generate-data:
	$(UV) python -m ingest.generate --seed 42

load:
	$(UV) python -m ingest.load

verify-data:
	$(UV) python -m ingest.verify_data

test-analysis:
	$(UV) pytest tests/test_detector.py -v

mcp-smoke:
	$(UV) python scripts/mcp_smoke.py

extractor-test:
	$(UV) pytest tests/test_extractor.py -v

test-agent:
	$(UV) pytest tests/test_agent.py -v

test-report:
	$(UV) pytest tests/test_report.py -v
	$(UV) python scripts/render_fixture_report.py

demo:
	$(UV) python scripts/run_demo.py

api-test:
	$(UV) pytest tests/test_api.py -v

lint:
	$(UV) ruff check .

verify-all:
	$(UV) ruff check .
	$(UV) pytest -v
	$(MAKE) preflight-report
	$(UV) python scripts/verify_repo_hygiene.py

.PHONY: smoke
smoke:
	bash scripts/smoke.sh

.PHONY: stress-test
stress-test:
	$(UV) python tests/stress/find_breaking_point.py

.PHONY: chaos-test
chaos-test:
	$(UV) pytest tests/chaos/ -v

.PHONY: load-test
load-test:
	$(UV) python tests/load/ingest_load.py
	$(UV) python tests/load/api_load.py
	$(UV) python scripts/load_report.py

.PHONY: soak-test-short
soak-test-short:
	$(UV) python tests/soak/short_soak.py --minutes 30
