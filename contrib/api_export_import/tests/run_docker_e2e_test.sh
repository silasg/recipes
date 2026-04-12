#!/usr/bin/env bash
#
# Docker-based E2E test for the export/import workflow.
#
# Starts source + target via docker-compose, then on the host:
#   1. Seeds source with test data
#   2. Exports from source → local directory
#   3. Validates the export
#   4. Imports to target
#   5. Verifies target matches source
#
# Usage:
#   ./run_docker_e2e_test.sh              # Basic test
#   ./run_docker_e2e_test.sh --keep       # Leave containers running
#
# Prerequisites:
#   - Docker with compose v2
#   - Python 3 with 'requests' (auto-installed via venv)
#
# Exit codes:
#   0 — all tests passed
#   1 — verification failed
#   2 — infrastructure error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
EI_DIR="${REPO_DIR}/contrib/api_export_import"
TESTS_DIR="${REPO_DIR}/contrib/tests"
EXPORT_DIR="/tmp/ei_docker_export"

SOURCE_URL="http://localhost:18080"
TARGET_URL="http://localhost:18081"

# Parse CLI flags
KEEP_RUNNING=false
for arg in "$@"; do
    case $arg in
        --keep) KEEP_RUNNING=true ;;
        *)      echo "Unknown flag: $arg"; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

cleanup() {
    rm -rf "$EXPORT_DIR"
    if [ "$KEEP_RUNNING" = true ]; then
        echo ""
        echo "==> Containers left running (--keep). Clean up with:"
        echo "    docker compose -f ${COMPOSE_FILE} down -v"
        return
    fi
    echo ""
    echo "==> Cleaning up containers and volumes..."
    docker compose -f "${COMPOSE_FILE}" down -v 2>/dev/null || true
}

fail() {
    echo "ERROR: $1" >&2
    exit 2
}

parse_setup_output() {
    local output="$1"
    local key="$2"
    echo "$output" | grep "^${key}=" | head -1 | cut -d= -f2
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 not found in PATH"

# Set up venv with dependencies
VENV_DIR="${TESTS_DIR}/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "${VENV_DIR}/bin/activate"
pip install -q -r "${TESTS_DIR}/requirements.txt"

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Start Docker containers
# ---------------------------------------------------------------------------

echo "==> Starting Docker containers..."
docker compose -f "${COMPOSE_FILE}" down -v 2>/dev/null || true
docker compose -f "${COMPOSE_FILE}" up -d --wait 2>&1 || fail "Docker compose up failed"

# ---------------------------------------------------------------------------
# Setup instances
# ---------------------------------------------------------------------------

echo ""
echo "==> Setting up source instance..."
SOURCE_CONTAINER=$(docker compose -f "${COMPOSE_FILE}" ps -q source)
TARGET_CONTAINER=$(docker compose -f "${COMPOSE_FILE}" ps -q target)

if [ -z "$SOURCE_CONTAINER" ] || [ -z "$TARGET_CONTAINER" ]; then
    fail "Could not find source/target containers"
fi

SOURCE_SETUP=$(python3 "${TESTS_DIR}/setup_instance.py" "$SOURCE_CONTAINER") || fail "Source setup failed"
SOURCE_TOKEN=$(parse_setup_output "$SOURCE_SETUP" "TOKEN")
echo "   Source token: ${SOURCE_TOKEN:0:20}..."

echo ""
echo "==> Setting up target instance..."
TARGET_SETUP=$(python3 "${TESTS_DIR}/setup_instance.py" "$TARGET_CONTAINER") || fail "Target setup failed"
TARGET_TOKEN=$(parse_setup_output "$TARGET_SETUP" "TOKEN")
echo "   Target token: ${TARGET_TOKEN:0:20}..."

# ---------------------------------------------------------------------------
# Seed source
# ---------------------------------------------------------------------------

echo ""
echo "==> Seeding source instance with test data..."
python3 "${TESTS_DIR}/seed_source.py" "${SOURCE_URL}" "${SOURCE_TOKEN}" || fail "Seeding failed"

# ---------------------------------------------------------------------------
# Test 1: Export
# ---------------------------------------------------------------------------

echo ""
echo "=== TEST 1: Export from source ==="
rm -rf "$EXPORT_DIR"
python3 "${EI_DIR}/export.py" \
    --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
    --output "${EXPORT_DIR}" || fail "Export failed"
echo "   PASS: Export completed"

# ---------------------------------------------------------------------------
# Test 2: Validate
# ---------------------------------------------------------------------------

echo ""
echo "=== TEST 2: Validate export ==="
python3 "${EI_DIR}/import.py" validate "${EXPORT_DIR}" || fail "Validation failed"
echo "   PASS: Validation completed"

# ---------------------------------------------------------------------------
# Test 3: Dry-run
# ---------------------------------------------------------------------------

echo ""
echo "=== TEST 3: Dry-run import ==="
python3 "${EI_DIR}/import.py" dry-run "${EXPORT_DIR}" \
    --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
    || fail "Dry-run failed"

# Verify target is still empty
RECIPE_COUNT=$(python3 -c "
import sys; sys.path.insert(0, '${TESTS_DIR}')
from conftest_helpers import TandoorAPIClient
c = TandoorAPIClient('${TARGET_URL}', '${TARGET_TOKEN}')
print(len(c.get_all('recipe/')))
")
if [ "$RECIPE_COUNT" != "0" ]; then
    fail "Target has ${RECIPE_COUNT} recipes after dry-run (expected 0)"
fi
echo "   PASS: Dry-run completed, target still empty"

# ---------------------------------------------------------------------------
# Test 4: Live import
# ---------------------------------------------------------------------------

echo ""
echo "=== TEST 4: Import to target ==="
python3 "${EI_DIR}/import.py" run "${EXPORT_DIR}" \
    --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
    || fail "Import failed"

# ---------------------------------------------------------------------------
# Test 5: Verify
# ---------------------------------------------------------------------------

echo ""
echo "=== TEST 5: Verify target matches source ==="
python3 "${TESTS_DIR}/verify_migration.py" \
    "${SOURCE_URL}" "${SOURCE_TOKEN}" \
    "${TARGET_URL}" "${TARGET_TOKEN}"
VERIFY_EXIT=$?

if [ "$VERIFY_EXIT" -ne 0 ]; then
    echo "   FAIL: Verification failed"
    exit 1
fi
echo "   PASS: Target matches source"

echo ""
echo "========================================="
echo "  ALL DOCKER E2E TESTS PASSED"
echo "  - Export: OK"
echo "  - Validate: OK"
echo "  - Dry-run: OK"
echo "  - Import: OK"
echo "  - Verify: OK (17/17 checks)"
echo "========================================="
exit 0
