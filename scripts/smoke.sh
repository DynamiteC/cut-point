#!/usr/bin/env bash
# Phase 10.1: Smoke test -- verifies the entire CutPoint stack is alive.
# Exits on first failure. Kills all background processes on exit.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

START_TIME=$(date +%s)
PIDS=()

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    # Only stop ClickHouse if this script started it; leave a pre-existing
    # developer server alone.
    if [ "${STARTED_CH:-false}" = "true" ]; then
        bash "$REPO_ROOT/scripts/clickhouse.sh" down 2>/dev/null || true
    fi
}
trap cleanup EXIT

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }

# ---------- 1+2. Start local ClickHouse and wait for readiness ----------
# Delegates to scripts/clickhouse.sh, the single source of truth for how this
# instance is launched (correct `server -- --path` invocation, tracked config,
# pidfile). The old inline `server --path=` fallback here was rejected by
# ClickHouse 26.x and is gone.
CH_DIR="$REPO_ROOT/.local-clickhouse"
CH_BIN="$CH_DIR/clickhouse"

if [ ! -x "$CH_BIN" ]; then
    fail "ClickHouse binary not found at $CH_BIN"
fi

# Track whether smoke.sh started the server, so cleanup only stops what it owns
# and does not kill a server the developer already had running.
STARTED_CH=false
if [ "$(curl -s 'http://localhost:8123/?query=SELECT+1' 2>/dev/null)" != "1" ]; then
    bash "$REPO_ROOT/scripts/clickhouse.sh" up || fail "ClickHouse did not become ready"
    STARTED_CH=true
fi
pass "ClickHouse reachable (SELECT 1)"

# ---------- 3. mcp-clickhouse tool listing ----------
uv run python scripts/mcp_smoke.py > /dev/null 2>&1 \
    && pass "mcp-clickhouse spawns and lists tools" \
    || fail "mcp-clickhouse smoke test failed"

# ---------- 4. Segment extractor /health ----------
uv run uvicorn services.segment_extractor.main:app --port 8900 &
PIDS+=($!)
WAITED=0
until curl -sf http://localhost:8900/health > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $WAITED -ge 15 ]; then
        fail "Segment extractor did not respond on /health within 15s"
    fi
done
pass "Segment extractor /health returns 200"

# ---------- 5. API /trailers ----------
uv run uvicorn api.main:app --port 8901 &
PIDS+=($!)
WAITED=0
until curl -sf http://localhost:8901/trailers > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $WAITED -ge 15 ]; then
        fail "API did not respond on /trailers within 15s"
    fi
done

TRAILERS_RESPONSE=$(curl -s http://localhost:8901/trailers)
echo "$TRAILERS_RESPONSE" | python3 -c "import sys,json; json.loads(sys.stdin.read()); sys.exit(0)" \
    && pass "API /trailers returns valid JSON array" \
    || fail "API /trailers did not return valid JSON"

# ---------- 6. Pipeline dry-run ----------
uv run python -m agent.run_pipeline --trailer demo_001 --dry-run > /dev/null 2>&1 \
    && pass "agent.run_pipeline --dry-run completes successfully" \
    || fail "agent.run_pipeline --dry-run failed"

# ---------- Summary ----------
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo ""
echo "All smoke checks passed in ${ELAPSED}s"
