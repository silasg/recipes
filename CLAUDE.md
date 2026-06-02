# CLAUDE.md — Tandoor Recipes

## Project Overview

Tandoor Recipes is a self-hosted recipe management application built with Django (backend) and Vue 3 (frontend). It supports multi-tenant spaces, recipe import/export from 20+ formats, meal planning, shopping lists, and AI-powered features.

**License**: AGPL-3.0

## Tech Stack

- **Backend**: Django 5.2, Django REST Framework 3.17, Python 3.12+
- **Frontend**: Vue 3.5, Vuetify 3, TypeScript, Vite
- **Database**: PostgreSQL (production), SQLite (dev default)
- **Cache**: Redis (optional) or LocMemCache
- **Package managers**: pip (Python), yarn (JS)
- **Containerization**: Docker (Alpine-based), nginx + gunicorn

## Repository Structure

```
recipes/              # Django project settings (settings.py, urls.py, wsgi.py)
cookbook/              # Main Django app
  ├── models.py       # 50+ models (Recipe, Food, Ingredient, Step, MealPlan, etc.)
  ├── serializer.py   # DRF serializers (~97KB)
  ├── urls.py         # URL routing (REST API under /api/)
  ├── admin.py        # Admin configuration
  ├── forms.py        # Django forms
  ├── views/
  │   ├── api.py      # DRF ViewSets (~175KB, 40+ endpoints)
  │   └── views.py    # Template views
  ├── helper/         # Business logic modules
  │   ├── permission_helper.py    # Multi-tenant space scoping
  │   ├── recipe_url_import.py    # URL scraping
  │   ├── ingredient_parser.py    # Ingredient parsing
  │   ├── recipe_search.py        # Full-text search
  │   ├── shopping_helper.py      # Shopping list logic
  │   └── ai_helper.py            # LiteLLM AI integration
  ├── connectors/     # External integrations (HomeAssistant, Nextcloud)
  ├── integration/    # Recipe import formats (25 importers)
  ├── provider/       # Storage backends (Local, Dropbox, Nextcloud, S3)
  ├── management/commands/  # Django management commands
  ├── tests/          # Test suite
  │   ├── api/        # API endpoint tests (38 files)
  │   ├── other/      # Integration/utility tests (21 files)
  │   ├── views/      # View tests
  │   └── factories/  # FactoryBoy model factories
  ├── locale/         # Backend translations (.po files)
  └── migrations/     # Database migrations
vue3/                 # Vue 3 frontend
  ├── src/
  │   ├── apps/tandoor/   # Main app entry point
  │   ├── components/     # UI components (buttons, dialogs, inputs, tables)
  │   ├── stores/         # Pinia state stores
  │   ├── pages/          # Page templates
  │   ├── composables/    # Vue composables
  │   ├── utils/          # Helper utilities
  │   ├── locales/        # i18n JSON translations
  │   ├── openapi/        # Auto-generated API client
  │   └── types/          # TypeScript type definitions
  ├── package.json
  └── vite.config.ts
docs/                 # MkDocs documentation
```

## Development Commands

### Backend (Django)

```bash
python manage.py runserver          # Start dev server
python manage.py migrate            # Run migrations
python manage.py makemigrations     # Create new migrations
python manage.py test               # Run tests (use pytest instead)
```

### Frontend (Vue 3)

```bash
cd vue3
yarn install                        # Install dependencies
yarn dev                            # Start Vite dev server (HMR)
yarn build                          # Production build
```

### Testing

```bash
# Run full test suite (uses pytest-django with parallel execution)
pytest

# Run specific test file
pytest cookbook/tests/api/test_api_recipe.py

# Run specific test
pytest cookbook/tests/api/test_api_recipe.py::test_name -k "test_name"

# Without coverage (faster)
pytest --no-cov
```

- Test settings: `recipes/test_settings.py`
- Config: `pytest.ini` (pytest-django, `-n auto` for parallel, coverage enabled)
- Factories: `cookbook/tests/factories/` using FactoryBoy + pytest-factoryboy
- Multi-tenant tests use `space_1` / `space_2` fixtures for isolation

### Linting & Formatting

```bash
flake8                              # Python linting
yapf -i <file>                      # Python formatting
isort <file>                        # Import sorting
npx prettier --write <file>         # JS/Vue formatting
```

## Code Style & Conventions

### Python
- **Max line length**: 179 characters (flake8, yapf, isort all aligned)
- **Formatter**: yapf (PEP8-based, see `pyproject.toml`)
- **Import sorting**: isort with multi_line_output=5
- **Ignored flake8 rules**: E203, W503, E712

### JavaScript/TypeScript/Vue
- **Max line length**: 179 characters (prettier)
- **Trailing commas**: es5 style
- **Indentation**: 2 spaces
- **Config**: `.prettierrc`

### Django Patterns
- **Multi-tenancy**: All queries must be scoped to a `Space` using `django-scopes`. Use `ScopedManager` and always pass `space` context.
- **Permissions**: Use `PermissionModelMixin` and check via `cookbook/helper/permission_helper.py`. User groups: admin, user, guest per space.
- **Serializers**: Extend `WritableNestedModelSerializer` for nested creates/updates.
- **Tree structures**: Foods and Keywords use `treebeard` MP_Node for hierarchical data.
- **API views**: Use DRF ViewSets registered on `DefaultRouter` in `cookbook/urls.py`.

### Vue 3 Patterns
- **State management**: Pinia stores in `vue3/src/stores/`
- **UI framework**: Vuetify 3 (Material Design)
- **API client**: Auto-generated TypeScript client in `vue3/src/openapi/`
- **i18n**: vue-i18n with locale JSON files in `vue3/src/locales/`

## Key Architecture Decisions

- **Space isolation**: Every model with user data belongs to a `Space`. This is enforced at the ORM level via `django-scopes`. Never bypass scope filtering.
- **Plugin system**: Optional plugins loaded from `recipes/plugins/`, configured in settings.
- **Recipe import**: 25 format importers in `cookbook/integration/`. Each implements a consistent interface.
- **Storage backends**: Recipes can store media on local disk, S3, Dropbox, or Nextcloud.
- **AI features**: Integrated via `litellm` in `cookbook/helper/ai_helper.py` for recipe-related AI tasks.

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Python 3.12, Node 22. Builds Vue3 → collects Django static → runs pytest.
- **Docker build** (`.github/workflows/build-docker.yml`): Multi-platform (amd64/arm64), pushes to Docker Hub + GHCR.
- **CodeQL**: Security scanning via GitHub Actions.
- **Docs**: MkDocs auto-published.

## Environment Configuration

- Copy `.env.template` to `.env` for local development
- Key env vars: `SECRET_KEY`, `DEBUG`, `DB_ENGINE`, `POSTGRES_*`, `SOCIAL_PROVIDERS`, `LOG_LEVEL`
- Database: Set `DB_ENGINE=django.db.backends.postgresql` for PostgreSQL
- Redis cache: Set `CACHE_DEFAULT` and `CACHE_TIMEOUT`

## Common Pitfalls

- Always scope database queries to a `Space` — unscoped queries will raise errors in production.
- The `serializer.py` and `views/api.py` files are very large (~97KB and ~175KB). Read only the relevant sections.
- Frontend API client is auto-generated from OpenAPI schema — don't edit `vue3/src/openapi/` files manually.
- Migrations must be created and committed when changing models.
- Test database uses MD5 password hasher for speed — don't rely on production-grade hashing in tests.
