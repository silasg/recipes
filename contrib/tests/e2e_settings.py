"""Minimal settings for running E2E test instances with SQLite."""
import os
import sys

# Instance identity: 'source' or 'target'
INSTANCE = os.environ.get('TANDOOR_INSTANCE', 'source')
DB_PATH = os.environ.get('TANDOOR_DB_PATH', f'/tmp/tandoor_{INSTANCE}.sqlite3')

os.environ.setdefault('SECRET_KEY', f'{INSTANCE}-test-secret-key')
os.environ.setdefault('DEBUG', '1')
os.environ.setdefault('ALLOWED_HOSTS', '*')
os.environ.setdefault('ENABLE_SIGNUP', '1')

# Import everything from main settings
from recipes.settings import *  # noqa: F401,F403

# Override database to use SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DB_PATH,
    }
}
