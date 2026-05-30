#!/usr/bin/env python3
"""
Set up a Tandoor instance inside a Docker container for E2E testing.

Creates superuser, space, household, and API token via `docker exec`.
Prints TOKEN=..., SPACE_ID=..., HOUSEHOLD_ID=... to stdout.

Usage:
    python setup_instance.py <container_name>
"""

import subprocess
import sys


SETUP_SCRIPT = r'''
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "recipes.settings")
django.setup()

from django.contrib.auth.models import User, Group
from cookbook.models import Space, UserSpace, Household
from oauth2_provider.models import AccessToken, Application
from django.utils import timezone
import uuid

# Create superuser
if User.objects.filter(username="admin").exists():
    user = User.objects.get(username="admin")
else:
    user = User.objects.create_superuser("admin", "admin@test.local", "admin123")

# Create space
space, _ = Space.objects.get_or_create(name="Test Space", defaults={"created_by": user})

# Create household
household, _ = Household.objects.get_or_create(name="Default Household", space=space)

# Create UserSpace with admin group
us, created = UserSpace.objects.get_or_create(user=user, space=space, defaults={"active": True})
admin_group = Group.objects.filter(name="admin").first()
if admin_group and not us.groups.filter(pk=admin_group.pk).exists():
    us.groups.add(admin_group)

# Ensure household is set
if not us.household:
    us.household = household
    us.save()

# Create OAuth2 application (needed for access tokens)
app, _ = Application.objects.get_or_create(
    name="migration-test",
    defaults={
        "client_type": Application.CLIENT_CONFIDENTIAL,
        "authorization_grant_type": Application.GRANT_CLIENT_CREDENTIALS,
        "user": user,
    }
)

# Create access token
token_str = f"tda_{uuid.uuid4().hex}"
token = AccessToken.objects.create(
    user=user,
    application=app,
    token=token_str,
    expires=timezone.now() + timezone.timedelta(days=365),
    scope="read write app",
)

print(f"TOKEN={token.token}")
print(f"SPACE_ID={space.pk}")
print(f"HOUSEHOLD_ID={household.pk}")
'''


def setup_instance(container_name: str) -> dict:
    """Run setup script inside container, return parsed results."""
    result = subprocess.run(
        [
            "docker", "exec", container_name,
            "/opt/recipes/venv/bin/python", "-c", SETUP_SCRIPT,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        print(f"STDERR: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"Setup failed for {container_name}: {result.stderr}")

    output = {}
    for line in result.stdout.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            output[key.strip()] = value.strip()

    if "TOKEN" not in output:
        raise RuntimeError(f"No TOKEN in output from {container_name}: {result.stdout}")

    return output


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <container_name>", file=sys.stderr)
        sys.exit(2)

    container_name = sys.argv[1]
    result = setup_instance(container_name)

    # Print results for shell script to capture
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
