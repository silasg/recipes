#!/usr/bin/env bash
#
# Run E2E migration test locally without Docker.
# Starts two Django dev servers with separate SQLite databases.
#
# Usage:
#   ./run_e2e_local.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MIGRATE_SCRIPT="${SCRIPT_DIR}/../migrate_space.py"
VENV="${TANDOOR_VENV:-${SCRIPT_DIR}/.venv}"
PY="${VENV}/bin/python"

SOURCE_PORT=18080
TARGET_PORT=18081
SOURCE_URL="http://localhost:${SOURCE_PORT}"
TARGET_URL="http://localhost:${TARGET_PORT}"
SOURCE_DB="/tmp/tandoor_source.sqlite3"
TARGET_DB="/tmp/tandoor_target.sqlite3"

SOURCE_PID=""
TARGET_PID=""

cleanup() {
    echo ""
    echo "==> Cleaning up..."
    [ -n "$SOURCE_PID" ] && kill "$SOURCE_PID" 2>/dev/null || true
    [ -n "$TARGET_PID" ] && kill "$TARGET_PID" 2>/dev/null || true
    rm -f "$SOURCE_DB" "$TARGET_DB"
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

cd "$REPO_DIR"

# Clean any previous DBs
rm -f "$SOURCE_DB" "$TARGET_DB"

# ---------------------------------------------------------------------------
# Migrate both databases
# ---------------------------------------------------------------------------
echo "==> Migrating source database..."
TANDOOR_INSTANCE=source TANDOOR_DB_PATH="$SOURCE_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py migrate --skip-checks --verbosity=0 2>&1

echo "==> Migrating target database..."
TANDOOR_INSTANCE=target TANDOOR_DB_PATH="$TARGET_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py migrate --skip-checks --verbosity=0 2>&1

# ---------------------------------------------------------------------------
# Start dev servers
# ---------------------------------------------------------------------------
echo "==> Starting source server on port ${SOURCE_PORT}..."
TANDOOR_INSTANCE=source TANDOOR_DB_PATH="$SOURCE_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py runserver "$SOURCE_PORT" --noreload 2>/dev/null &
SOURCE_PID=$!

echo "==> Starting target server on port ${TARGET_PORT}..."
TANDOOR_INSTANCE=target TANDOOR_DB_PATH="$TARGET_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" manage.py runserver "$TARGET_PORT" --noreload 2>/dev/null &
TARGET_PID=$!

echo "==> Waiting for servers..."
wait_for_server "$SOURCE_URL" "Source"
echo "   Source ready"
wait_for_server "$TARGET_URL" "Target"
echo "   Target ready"

# ---------------------------------------------------------------------------
# Setup instances (create users + tokens via Django shell)
# ---------------------------------------------------------------------------
echo ""
echo "==> Setting up source instance..."
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

app, _ = Application.objects.get_or_create(
    name="migration-test",
    defaults={
        "client_type": Application.CLIENT_CONFIDENTIAL,
        "authorization_grant_type": Application.GRANT_CLIENT_CREDENTIALS,
        "user": user,
    }
)

token_str = f"tda_{uuid.uuid4().hex}"
AccessToken.objects.create(
    user=user, application=app, token=token_str,
    expires=timezone.now() + timezone.timedelta(days=365),
    scope="read write app",
)
print(f"TOKEN={token_str}")
print(f"SPACE_ID={space.pk}")
'

SOURCE_SETUP=$(TANDOOR_INSTANCE=source TANDOOR_DB_PATH="$SOURCE_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" -c "$SETUP_SCRIPT" 2>&1) || fail "Source setup failed: $SOURCE_SETUP"
SOURCE_TOKEN=$(parse_setup_output "$SOURCE_SETUP" "TOKEN")
echo "   Source token: ${SOURCE_TOKEN:0:20}..."

echo ""
echo "==> Setting up target instance..."
TARGET_SETUP=$(TANDOOR_INSTANCE=target TANDOOR_DB_PATH="$TARGET_DB" \
    DJANGO_SETTINGS_MODULE=contrib.tests.e2e_settings \
    "$PY" -c "$SETUP_SCRIPT" 2>&1) || fail "Target setup failed: $TARGET_SETUP"
TARGET_TOKEN=$(parse_setup_output "$TARGET_SETUP" "TOKEN")
echo "   Target token: ${TARGET_TOKEN:0:20}..."

# ---------------------------------------------------------------------------
# Seed, migrate, verify
# ---------------------------------------------------------------------------
echo ""
echo "==> Seeding source instance with test data..."
"$PY" "${SCRIPT_DIR}/seed_source.py" "${SOURCE_URL}" "${SOURCE_TOKEN}" || fail "Seeding failed"

echo ""
echo "==> Running migration..."
"$PY" "${MIGRATE_SCRIPT}" \
    --source-url "${SOURCE_URL}" --source-token "${SOURCE_TOKEN}" \
    --target-url "${TARGET_URL}" --target-token "${TARGET_TOKEN}" \
    || fail "Migration failed"

echo ""
echo "==> Verifying migration..."
"$PY" "${SCRIPT_DIR}/verify_migration.py" \
    "${SOURCE_URL}" "${SOURCE_TOKEN}" \
    "${TARGET_URL}" "${TARGET_TOKEN}"
VERIFY_EXIT=$?

if [ "$VERIFY_EXIT" -ne 0 ]; then
    echo ""
    echo "MIGRATION VERIFICATION FAILED"
    exit 1
fi

echo ""
echo "========================================="
echo "  ALL E2E TESTS PASSED"
echo "========================================="
exit 0
