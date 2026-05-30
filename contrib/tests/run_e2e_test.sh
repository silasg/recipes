#!/usr/bin/env bash
#
# E2E test runner for migrate_space.py
#
# Orchestrates: Docker start → setup → seed → migrate → verify → cleanup
#
# Usage:
#   ./run_e2e_test.sh                # Basic: migrate + verify
#   ./run_e2e_test.sh --keep         # Leave containers running for debugging
#   ./run_e2e_test.sh --dry-run      # Also test dry-run mode
#   ./run_e2e_test.sh --idempotent   # Run migration twice, verify no duplicates
#   ./run_e2e_test.sh --all          # Run all test modes
#
# Exit codes:
#   0 — all assertions passed
#   1 — one or more verifications failed
#   2 — infrastructure error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
MIGRATE_SCRIPT="${SCRIPT_DIR}/../migrate_space.py"

SOURCE_URL="http://localhost:18080"
TARGET_URL="http://localhost:18081"

# Parse CLI flags
KEEP_RUNNING=false
TEST_DRY_RUN=false
TEST_IDEMPOTENT=false
for arg in "$@"; do
    case $arg in
        --keep)         KEEP_RUNNING=true ;;
        --dry-run)      TEST_DRY_RUN=true ;;
        --idempotent)   TEST_IDEMPOTENT=true ;;
        --all)          TEST_DRY_RUN=true; TEST_IDEMPOTENT=true ;;
        *)              echo "Unknown flag: $arg"; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

cleanup() {
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
    # Parse KEY=VALUE lines from setup_instance.py output
    local output="$1"
    local key="$2"
    echo "$output" | grep "^${key}=" | head -1 | cut -d= -f2
}

# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

# Check host prerequisites
command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 not found in PATH"

# Set up venv with dependencies
VENV_DIR="${SCRIPT_DIR}/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "${VENV_DIR}/bin/activate"
pip install -q -r "${SCRIPT_DIR}/requirements.txt"

trap cleanup EXIT

echo "==> Starting Docker containers..."
docker compose -f "${COMPOSE_FILE}" down -v 2>/dev/null || true
docker compose -f "${COMPOSE_FILE}" up -d --wait 2>&1 || fail "Docker compose up failed"

echo ""
echo "==> Setting up source instance..."
# Determine container names (docker compose prefixes with directory name)
SOURCE_CONTAINER=$(docker compose -f "${COMPOSE_FILE}" ps -q source)
TARGET_CONTAINER=$(docker compose -f "${COMPOSE_FILE}" ps -q target)

if [ -z "$SOURCE_CONTAINER" ] || [ -z "$TARGET_CONTAINER" ]; then
    fail "Could not find source/target containers"
fi

SOURCE_SETUP=$(python3 "${SCRIPT_DIR}/setup_instance.py" "$SOURCE_CONTAINER") || fail "Source setup failed"
SOURCE_TOKEN=$(parse_setup_output "$SOURCE_SETUP" "TOKEN")
echo "   Source token: ${SOURCE_TOKEN:0:20}..."

echo ""
echo "==> Setting up target instance..."
TARGET_SETUP=$(python3 "${SCRIPT_DIR}/setup_instance.py" "$TARGET_CONTAINER") || fail "Target setup failed"
TARGET_TOKEN=$(parse_setup_output "$TARGET_SETUP" "TOKEN")
echo "   Target token: ${TARGET_TOKEN:0:20}..."

echo ""
echo "==> Seeding source instance with test data..."
python3 "${SCRIPT_DIR}/seed_source.py" "${SOURCE_URL}" "${SOURCE_TOKEN}" || fail "Seeding failed"

# ---------------------------------------------------------------------------
# Optional: Dry-run test
# ---------------------------------------------------------------------------

if [ "$TEST_DRY_RUN" = true ]; then
    echo ""
    echo "==> Testing dry-run mode (should make no writes)..."
    python3 "${MIGRATE_SCRIPT}" \
        --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
        --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
        --dry-run || fail "Dry-run migration failed"

    echo ""
    echo "==> Verifying target is still empty after dry-run..."
    # Quick check: target should have 0 recipes
    RECIPE_COUNT=$(python3 -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
from conftest_helpers import TandoorAPIClient
c = TandoorAPIClient('${TARGET_URL}', '${TARGET_TOKEN}')
print(len(c.get_all('recipe/')))
")
    if [ "$RECIPE_COUNT" != "0" ]; then
        echo "FAIL: Target has ${RECIPE_COUNT} recipes after dry-run (expected 0)"
        exit 1
    fi
    echo "   PASS: Target still empty after dry-run"
fi

# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------

echo ""
echo "==> Running migration..."
python3 "${MIGRATE_SCRIPT}" \
    --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
    --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
    || fail "Migration failed"

echo ""
echo "==> Verifying migration..."
python3 "${SCRIPT_DIR}/verify_migration.py" \
    "${SOURCE_URL}" "${SOURCE_TOKEN}" \
    "${TARGET_URL}" "${TARGET_TOKEN}"
VERIFY_EXIT=$?

if [ "$VERIFY_EXIT" -ne 0 ]; then
    echo ""
    echo "MIGRATION VERIFICATION FAILED"
    exit 1
fi

# ---------------------------------------------------------------------------
# Optional: Idempotency test
# ---------------------------------------------------------------------------

if [ "$TEST_IDEMPOTENT" = true ]; then
    echo ""
    echo "==> Running migration again (idempotency test)..."
    python3 "${MIGRATE_SCRIPT}" \
        --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
        --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
        || fail "Second migration run failed"

    echo ""
    echo "==> Verifying no duplicates after second run..."
    python3 "${SCRIPT_DIR}/verify_migration.py" \
        "${SOURCE_URL}" "${SOURCE_TOKEN}" \
        "${TARGET_URL}" "${TARGET_TOKEN}"
    VERIFY_EXIT=$?

    if [ "$VERIFY_EXIT" -ne 0 ]; then
        echo ""
        echo "IDEMPOTENCY VERIFICATION FAILED"
        exit 1
    fi
    echo "   PASS: No duplicates after second migration run"
fi

echo ""
echo "========================================="
echo "  ALL E2E TESTS PASSED"
echo "========================================="
exit 0
