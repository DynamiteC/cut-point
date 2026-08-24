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
}
trap cleanup EXIT

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }

# ---------- 1. Start local ClickHouse ----------
CH_DIR="$REPO_ROOT/.local-clickhouse"
CH_BIN="$CH_DIR/clickhouse"

if [ ! -x "$CH_BIN" ]; then
    fail "ClickHouse binary not found at $CH_BIN"
fi

if [ -f "$CH_DIR/config.xml" ]; then
    "$CH_BIN" server --config-file="$CH_DIR/config.xml" &
else
    "$CH_BIN" server --path="$CH_DIR/" &
fi
PIDS+=($!)

# ---------- 2. Wait for ClickHouse readiness ----------
MAX_WAIT=30
WAITED=0
until [ "$(curl -s 'http://localhost:8123/?query=SELECT+1' 2>/dev/null)" = "1" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $WAITED -ge $MAX_WAIT ]; then
        fail "ClickHouse did not become ready within ${MAX_WAIT}s"
    fi
done
pass "ClickHouse reachable (SELECT 1) after ${WAITED}s"

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
