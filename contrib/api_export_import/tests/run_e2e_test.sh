#!/usr/bin/env bash
#
# E2E test for the two-step export/import workflow.
# Starts local Django dev servers, seeds data, then tests:
#   1. Export from source → local directory
#   2. Validate the export
#   3. Dry-run import against target
#   4. Live import to target 1
#   5. Verify target 1 matches source
#   6. Live import to target 2 (proves "export once, push many")
#   7. Verify target 2 matches source
#
# Usage:
#   ./run_e2e_test.sh
#
# Requires: TANDOOR_VENV env var pointing to a Python 3.12+ venv
#           with the project dependencies installed.
#
# Exit codes:
#   0 — all tests passed
#   1 — verification failed
#   2 — infrastructure error
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
EI_DIR="${REPO_DIR}/contrib/api_export_import"
TESTS_DIR="${REPO_DIR}/contrib/tests"
VENV="${TANDOOR_VENV:-${TESTS_DIR}/.venv}"
PY="${VENV}/bin/python"
EXPORT_DIR="/tmp/ei_e2e_export"

SOURCE_PORT=18080
TARGET1_PORT=18081
TARGET2_PORT=18082
SOURCE_URL="http://localhost:${SOURCE_PORT}"
TARGET1_URL="http://localhost:${TARGET1_PORT}"
TARGET2_URL="http://localhost:${TARGET2_PORT}"
SOURCE_DB="/tmp/tandoor_ei_e2e_source.sqlite3"
TARGET1_DB="/tmp/tandoor_ei_e2e_target1.sqlite3"
TARGET2_DB="/tmp/tandoor_ei_e2e_target2.sqlite3"

SOURCE_PID=""
TARGET1_PID=""
TARGET2_PID=""

cleanup() {
    echo ""
    echo "==> Cleaning up..."
    [ -n "$SOURCE_PID" ] && kill "$SOURCE_PID" 2>/dev/null || true
    [ -n "$TARGET1_PID" ] && kill "$TARGET1_PID" 2>/dev/null || true
    [ -n "$TARGET2_PID" ] && kill "$TARGET2_PID" 2>/dev/null || true
    rm -f "$SOURCE_DB" "$TARGET1_DB" "$TARGET2_DB"
    rm -rf "$EXPORT_DIR"
}
trap cleanup EXIT

fail() { echo "ERROR: $1" >&2; exit 2; }

wait_for_server() {
    local url="$1" name="$2" max_wait=30 elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        if "$PY" -c "import requests; requests.get('${url}/api/', timeout=2)" 2>/dev/null; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    fail "${name} did not become ready within ${max_wait}s"
}

parse_setup_output() {
    local output="$1" key="$2"
    echo "$output" | grep "^${key}=" | head -1 | cut -d= -f2
}

SETUP_SCRIPT='
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contrib.tests.e2e_settings")
django.setup()
from django.contrib.auth.models import User, Group
from cookbook.models import Space, UserSpace, Household
from oauth2_provider.models import AccessToken, Application
from django.utils import timezone
import uuid
user = User.objects.filter(username="admin").first()
if not user:
    user = User.objects.create_superuser("admin", "admin@test.local", "admin123")
space, _ = Space.objects.get_or_create(name="Test Space", defaults={"created_by": user})
household, _ = Household.objects.get_or_create(name="Default Household", space=space)
us, _ = UserSpace.objects.get_or_create(user=user, space=space, defaults={"active": True})
admin_group = Group.objects.filter(name="admin").first()
if admin_group and not us.groups.filter(pk=admin_group.pk).exists():
    us.groups.add(admin_group)
if not us.household:
    us.household = household
    us.save()
app, _ = Application.objects.get_or_create(name="migration-test", defaults={"client_type": Application.CLIENT_CONFIDENTIAL, "authorization_grant_type": Application.GRANT_CLIENT_CREDENTIALS, "user": user})
token_str = f"tda_{uuid.uuid4().hex}"
AccessToken.objects.create(user=user, application=app, token=token_str, expires=timezone.now() + timezone.timedelta(days=365), scope="read write app")
print(f"TOKEN={token_str}")
'

cd "$REPO_DIR"

# Check prerequisites
[ -x "$PY" ] || fail "Python not found at ${PY}. Set TANDOOR_VENV to your venv path."
"$PY" -c "import requests" 2>/dev/null || fail "requests not installed in venv"

# Clean previous state
rm -f "$SOURCE_DB" "$TARGET1_DB" "$TARGET2_DB"
rm -rf "$EXPORT_DIR"

# ---------------------------------------------------------------------------
# Start infrastructure
# ---------------------------------------------------------------------------
echo "==> Migrating databases (source + 2 targets)..."
for db_path in "$SOURCE_DB" "$TARGET1_DB" "$TARGET2_DB"; do
    instance=$(basename "$db_path" .sqlite3 | sed 's/tandoor_ei_e2e_//')
    TANDOOR_INSTANCE="$instance" TANDOOR_DB_PATH="$db_path" \
        DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
        "$PY" manage.py migrate --skip-checks --verbosity=0 2>&1
done

echo "==> Starting 3 servers..."
TANDOOR_INSTANCE=source TANDOOR_DB_PATH="$SOURCE_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py runserver "$SOURCE_PORT" --noreload 2>/dev/null &
SOURCE_PID=$!

TANDOOR_INSTANCE=target1 TANDOOR_DB_PATH="$TARGET1_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py runserver "$TARGET1_PORT" --noreload 2>/dev/null &
TARGET1_PID=$!

TANDOOR_INSTANCE=target2 TANDOOR_DB_PATH="$TARGET2_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py runserver "$TARGET2_PORT" --noreload 2>/dev/null &
TARGET2_PID=$!

wait_for_server "$SOURCE_URL" "Source"
wait_for_server "$TARGET1_URL" "Target1"
wait_for_server "$TARGET2_URL" "Target2"
echo "   All 3 servers ready"

# ---------------------------------------------------------------------------
# Setup instances
# ---------------------------------------------------------------------------
echo ""
echo "==> Setting up instances..."
setup_instance() {
    local instance="$1" db_path="$2"
    TANDOOR_INSTANCE="$instance" TANDOOR_DB_PATH="$db_path" \
        DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
        "$PY" -c "$SETUP_SCRIPT" 2>&1
}

SOURCE_SETUP=$(setup_instance source "$SOURCE_DB") || fail "Source setup failed"
SOURCE_TOKEN=$(parse_setup_output "$SOURCE_SETUP" "TOKEN")
TARGET1_SETUP=$(setup_instance target1 "$TARGET1_DB") || fail "Target1 setup failed"
TARGET1_TOKEN=$(parse_setup_output "$TARGET1_SETUP" "TOKEN")
TARGET2_SETUP=$(setup_instance target2 "$TARGET2_DB") || fail "Target2 setup failed"
TARGET2_TOKEN=$(parse_setup_output "$TARGET2_SETUP" "TOKEN")
echo "   Source token:  ${SOURCE_TOKEN:0:20}..."
echo "   Target1 token: ${TARGET1_TOKEN:0:20}..."
echo "   Target2 token: ${TARGET2_TOKEN:0:20}..."

# ---------------------------------------------------------------------------
# Seed source
# ---------------------------------------------------------------------------
echo ""
echo "==> Seeding source with test data..."
"$PY" "${TESTS_DIR}/seed_source.py" "${SOURCE_URL}" "${SOURCE_TOKEN}" || fail "Seeding failed"

# ---------------------------------------------------------------------------
# Test 1: Export
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 1: Export from source ==="
"$PY" "${EI_DIR}/export.py" \
    --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
    --output "${EXPORT_DIR}" || fail "Export failed"
echo "   PASS: Export completed"

# ---------------------------------------------------------------------------
# Test 2: Validate
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 2: Validate export ==="
"$PY" "${EI_DIR}/import.py" validate "${EXPORT_DIR}" || fail "Validation failed"
echo "   PASS: Validation completed"

# ---------------------------------------------------------------------------
# Test 3: Dry-run
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 3: Dry-run import ==="
"$PY" "${EI_DIR}/import.py" dry-run "${EXPORT_DIR}" \
    --target-url "${TARGET1_URL}" --target-token "${TARGET1_TOKEN}" \
    || fail "Dry-run failed"

# Verify target1 is still empty after dry-run
RECIPE_COUNT=$("$PY" -c "
import sys; sys.path.insert(0, '${TESTS_DIR}')
from conftest_helpers import TandoorAPIClient
c = TandoorAPIClient('${TARGET1_URL}', '${TARGET1_TOKEN}')
print(len(c.get_all('recipe/')))
")
if [ "$RECIPE_COUNT" != "0" ]; then
    fail "Target1 has ${RECIPE_COUNT} recipes after dry-run (expected 0)"
fi
echo "   PASS: Dry-run completed, target still empty"

# ---------------------------------------------------------------------------
# Test 4: Live import to target 1
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 4: Import to target 1 ==="
"$PY" "${EI_DIR}/import.py" run "${EXPORT_DIR}" \
    --target-url "${TARGET1_URL}" --target-token "${TARGET1_TOKEN}" \
    || fail "Import to target1 failed"

echo ""
echo "==> Verifying target 1..."
"$PY" "${TESTS_DIR}/verify_migration.py" \
    "${SOURCE_URL}" "${SOURCE_TOKEN}" \
    "${TARGET1_URL}" "${TARGET1_TOKEN}"
if [ $? -ne 0 ]; then
    echo "   FAIL: Target 1 verification failed"
    exit 1
fi
echo "   PASS: Target 1 matches source"

# ---------------------------------------------------------------------------
# Test 5: Import same export to target 2 ("export once, push many")
# ---------------------------------------------------------------------------
echo ""
echo "=== TEST 5: Import to target 2 (export once, push many) ==="
"$PY" "${EI_DIR}/import.py" run "${EXPORT_DIR}" \
    --target-url "${TARGET2_URL}" --target-token "${TARGET2_TOKEN}" \
    || fail "Import to target2 failed"

echo ""
echo "==> Verifying target 2..."
"$PY" "${TESTS_DIR}/verify_migration.py" \
    "${SOURCE_URL}" "${SOURCE_TOKEN}" \
    "${TARGET2_URL}" "${TARGET2_TOKEN}"
if [ $? -ne 0 ]; then
    echo "   FAIL: Target 2 verification failed"
    exit 1
fi
echo "   PASS: Target 2 matches source"

# ---------------------------------------------------------------------------
echo ""
echo "========================================="
echo "  ALL EXPORT/IMPORT E2E TESTS PASSED"
echo "  - Export: OK"
echo "  - Validate: OK"
echo "  - Dry-run: OK"
echo "  - Import to target 1: OK (17/17 checks)"
echo "  - Import to target 2: OK (17/17 checks)"
echo "========================================="
exit 0
