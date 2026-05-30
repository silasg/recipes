# CLAUDE.md — Tandoor Recipes

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Tandoor Recipes is a self-hosted recipe management application built with Django (backend) and Vue 3 (frontend). It supports multi-tenant "Spaces", meal planning, shopping lists, recipe import/export from 20+ formats, and AI-powered features.

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
cookbook/             # Main Django app
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
  │   │   ├── dialogs/    # Modal dialogs
  │   │   ├── display/    # Read-only display components
  │   │   ├── inputs/     # Form inputs and editors
  │   │   └── model_editors/  # CRUD editors for each model type
  │   ├── stores/         # Pinia state stores
  │   ├── pages/          # Page templates
  │   ├── composables/    # Vue composables
  │   ├── utils/          # Helper utilities
  │   ├── locales/        # i18n JSON translations
  │   ├── openapi/        # Auto-generated API client
  │   └── types/          # TypeScript type definitions
  ├── package.json
  └── vite.config.ts
contrib/              # Out-of-tree tooling (migration, AI provider bootstrap)
docs/                 # MkDocs documentation
```

## Development Commands

### Backend (Django)

```bash
pip install -r requirements.txt           # Install Python dependencies
python manage.py runserver                # Start dev server (port 8000)
python manage.py migrate                  # Run migrations
python manage.py makemigrations           # Create new migrations
python manage.py collectstatic --noinput  # Collect static files
```

### Frontend (Vue 3)

```bash
cd vue3
yarn install                              # Install dependencies
yarn dev                                  # Vite dev server with HMR (port 5173)
yarn build                                # Production build (outputs to cookbook/static/vue3/)
```

### Testing

```bash
# Run full test suite (pytest-django with parallel execution)
pytest

# Run a single test file
pytest cookbook/tests/api/test_api_recipe.py

# Run a specific test
pytest cookbook/tests/api/test_api_recipe.py::test_function_name -v

# Without coverage (faster)
pytest --no-cov

# Run tests in a specific directory
pytest cookbook/tests/other/
```

- Test settings: `recipes/test_settings.py` (uses `TEST_*` env vars for database)
- Config: `pytest.ini` (pytest-django, `-n auto` for parallel, coverage enabled)
- Factories: `cookbook/tests/factories/` using FactoryBoy + pytest-factoryboy
- Multi-tenant tests use `space_1` / `space_2` fixtures for isolation
- Test database uses MD5 password hasher for speed — don't rely on production-grade hashing in tests

### Linting & Formatting

```bash
flake8                                    # Python linting
yapf -i <file>                            # Python formatting
isort <file>                              # Import sorting
npx prettier --write <file>               # JS/Vue formatting
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

## Architecture

### Multi-Tenancy

The application uses "Spaces" for multi-tenancy. Most models have a `space` foreign key. The `django-scopes` library enforces tenant isolation at the ORM level. Key patterns:

- Models inherit from `PermissionModelMixin` for space-aware permissions
- Tree-based models (Food, Keyword, Supermarket) use `django-treebeard` with custom `TreeModel` base class
- User-space relationships are managed via `UserSpace` model
- User groups: admin, user, guest per space (see `cookbook/helper/permission_helper.py`)
- **Never bypass scope filtering** — unscoped queries will raise errors in production

### Django Patterns
- **Serializers**: Extend `WritableNestedModelSerializer` for nested creates/updates
- **Tree structures**: Foods and Keywords use `treebeard` MP_Node for hierarchical data
- **API views**: Use DRF ViewSets registered on `DefaultRouter` in `cookbook/urls.py`

### API
- REST API uses Django REST Framework with DRF Spectacular for OpenAPI docs
- API docs available at `/docs/api/` (Redoc) and `/docs/swagger/` (Swagger)
- Authentication: Session, OAuth2, Basic, and Token auth supported
- API versioned via `TANDOOR_VERSION` in `cookbook/version_info.py`

### Vue 3 Patterns
- **State management**: Pinia stores in `vue3/src/stores/`
- **UI framework**: Vuetify 3 (Material Design)
- **API client**: Auto-generated TypeScript client in `vue3/src/openapi/` — don't edit manually
- **i18n**: vue-i18n with locale JSON files in `vue3/src/locales/`

### Plugin System

Plugins are loaded from `recipes/plugins/` directory. Each plugin can:
- Register its own API routes via `api_router_name`
- Include frontend build inputs in `plugin.ts`
- Add custom Django apps

### Key Architecture Decisions
- **Space isolation**: Every model with user data belongs to a `Space`, enforced at ORM level via `django-scopes`.
- **Recipe import**: 25 format importers in `cookbook/integration/`. Each implements a consistent interface.
- **Storage backends**: Recipes can store media on local disk, S3, Dropbox, or Nextcloud.
- **AI features**: Integrated via `litellm` in `cookbook/helper/ai_helper.py`.

## contrib/ — Out-of-Tree Tooling

This fork carries operator tooling that isn't part of upstream Tandoor. See `contrib/README.md` for usage, but at a glance:

- **`contrib/migrate_space.py`** — One-shot space migration from a live source instance to a live target instance via REST API. Handles dependency ordering, ID remapping, tree structures. Use when both instances are reachable simultaneously.
- **`contrib/api_export_import/`** — Two-step migration: `export.py` writes all space data to a local directory; `import.py` reads that directory and POSTs into any target. Use when the source must be decommissioned before the target is ready, or when you want a reviewable on-disk snapshot. Has its own `README.md`.
- **`contrib/add_ai_providers.sh`** — Bulk-creates the standard OpenRouter-backed AI provider lineup (Sonnet, Haiku, GPT-4o, DeepSeek, Gemini variants) with task-specific descriptions. Resolves the user's active space at runtime.

E2E tests for the migration tools live in `contrib/tests/` (live-to-live) and `contrib/api_export_import/tests/` (export/import). Both use docker-compose to spin up source/target instances.

Credentials for all contrib/ tooling come from gitignored project-root files:
- `tandoor_token.txt` — Tandoor API token
- `tandoor_url.txt` — Tandoor instance base URL
- `openrouter_api_key.txt` — OpenRouter API key

Detailed Claude Code skills live in `.claude/skills/`:
- `tandoor-ai-providers/` — model-to-task recommendations, API quirks, payloads
- `tandoor-recipe-mapping/` — first-mention ingredient↔step mapping algorithm

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Python 3.12, Node 22. Builds Vue3 → collects Django static → runs pytest.
- **Docker build** (`.github/workflows/build-docker.yml`): Multi-platform (amd64/arm64), pushes to Docker Hub + GHCR.
- **CodeQL**: Security scanning via GitHub Actions.
- **Docs**: MkDocs auto-published.

## Environment Configuration

- Copy `.env.template` to `.env` for local development
- Key env vars: `SECRET_KEY`, `DEBUG`, `DB_ENGINE`, `POSTGRES_*`, `REDIS_HOST`, `SOCIAL_PROVIDERS`, `LOG_LEVEL`, `ENABLE_SIGNUP`
- Database: Set `DB_ENGINE=django.db.backends.postgresql` for PostgreSQL
- Redis cache: Set `CACHE_DEFAULT` and `CACHE_TIMEOUT`
- Test uses `TEST_DATABASE_URL` or `TEST_POSTGRES_*` vars

## Common Pitfalls

- Always scope database queries to a `Space` — unscoped queries will raise errors in production.
- The `serializer.py` and `views/api.py` files are very large (~97KB and ~175KB). Read only the relevant sections.
- Frontend API client is auto-generated from OpenAPI schema — don't edit `vue3/src/openapi/` files manually.
- Migrations must be created and committed when changing models.
- Test database uses MD5 password hasher for speed — don't rely on production-grade hashing in tests.

## Dev Container

A devcontainer configuration exists in `.devcontainer/` for VS Code. Ports 8000 (Django) and 8080 are forwarded.
