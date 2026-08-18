.PHONY: preflight schema generate-data load verify-data test-analysis mcp-smoke \
        extractor-test test-agent test-report demo api-test verify-all lint

UV := uv run

preflight:
	$(UV) python scripts/preflight.py

preflight-report:
	$(UV) python scripts/preflight.py --report-only

schema:
	$(UV) python ingest/apply_schema.py

generate-data:
	$(UV) python ingest/generate.py --seed 42

load:
	$(UV) python ingest/load.py

verify-data:
	$(UV) python ingest/verify_data.py

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
