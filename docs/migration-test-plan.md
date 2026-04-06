# Migration Script: E2E Test Plan

## Overview

Automated E2E tests for `contrib/migrate_space.py` using two Dockerized Tandoor instances (source + target). Designed for regression testing across multiple test runs.

## File Structure

```
contrib/
  migrate_space.py                  # The migration script (main deliverable)
  tests/
    docker-compose.yml              # Source + target Tandoor with PostgreSQL
    conftest_helpers.py             # Shared API client, comparison utilities
    setup_instance.py               # Creates superuser, space, household, API token via docker exec
    seed_source.py                  # Populates source with realistic test data via API
    verify_migration.py             # Compares source vs target data, reports pass/fail
    run_e2e_test.sh                 # Orchestrator: start → seed → migrate → verify → cleanup
```

## 1. Docker Compose Setup (`docker-compose.yml`)

Two Tandoor instances, each with its own PostgreSQL database. Both build from the repo root Dockerfile (the fork).

```yaml
services:
  db_source:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: djangodb
      POSTGRES_USER: djangouser
      POSTGRES_PASSWORD: source_test_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U djangouser -d djangodb"]
      interval: 3s
      timeout: 3s
      retries: 10

  db_target:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: djangodb
      POSTGRES_USER: djangouser
      POSTGRES_PASSWORD: target_test_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U djangouser -d djangodb"]
      interval: 3s
      timeout: 3s
      retries: 10

  source:
    build:
      context: ../../
      dockerfile: Dockerfile
    environment:
      SECRET_KEY: source-test-secret-key-not-for-production
      DB_ENGINE: django.db.backends.postgresql
      POSTGRES_HOST: db_source
      POSTGRES_DB: djangodb
      POSTGRES_PORT: 5432
      POSTGRES_USER: djangouser
      POSTGRES_PASSWORD: source_test_password
      ALLOWED_HOSTS: "*"
      ENABLE_SIGNUP: 1
      TANDOOR_PORT: 80
    ports:
      - "18080:80"
    depends_on:
      db_source:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8080/openapi"]
      interval: 5s
      timeout: 5s
      start_period: 30s
      retries: 20

  target:
    build:
      context: ../../
      dockerfile: Dockerfile
    environment:
      SECRET_KEY: target-test-secret-key-not-for-production
      DB_ENGINE: django.db.backends.postgresql
      POSTGRES_HOST: db_target
      POSTGRES_DB: djangodb
      POSTGRES_PORT: 5432
      POSTGRES_USER: djangouser
      POSTGRES_PASSWORD: target_test_password
      ALLOWED_HOSTS: "*"
      ENABLE_SIGNUP: 1
      TANDOOR_PORT: 80
    ports:
      - "18081:80"
    depends_on:
      db_target:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8080/openapi"]
      interval: 5s
      timeout: 5s
      start_period: 30s
      retries: 20
```

**Why PostgreSQL over SQLite**: The codebase uses PostgreSQL-specific features (`django.contrib.postgres.search`, `pg_isready` in `boot.sh`). SQLite would risk masking real issues.

**Health checks**: `boot.sh` runs migrations and collectstatic on startup. The `wget` health check against `/openapi` confirms the app is fully ready.

## 2. Instance Setup (`setup_instance.py`)

Uses `docker exec` to run Django shell commands inside each container. This avoids fragile HTML form scraping for user/space creation.

Creates:
- Superuser `admin` / `admin123`
- Space `Test Space`
- Household `Default Household`
- UserSpace with admin group
- OAuth2 AccessToken (`tda_<uuid>`, scope `read write app`)

Returns the API token on stdout for the shell runner to capture.

```python
def setup_instance(container_name: str) -> dict:
    """
    Runs Django management shell inside container.
    Returns: {"token": str, "space_id": int, "household_id": int}
    """
    script = '''
from django.contrib.auth.models import User, Group
from cookbook.models import Space, UserSpace, Household
from oauth2_provider.models import AccessToken
from django.utils import timezone
import uuid

user = User.objects.create_superuser('admin', 'admin@test.local', 'admin123')
space = Space.objects.create(name='Test Space', created_by=user)
household = Household.objects.create(name='Default Household', space=space)
us = UserSpace.objects.create(user=user, space=space, active=True)
us.groups.add(Group.objects.get(name='admin'))
token = AccessToken.objects.create(
    user=user,
    token=f'tda_{uuid.uuid4().hex}',
    expires=timezone.now() + timezone.timedelta(days=365),
    scope='read write app'
)
print(f'TOKEN={token.token}')
print(f'SPACE_ID={space.pk}')
print(f'HOUSEHOLD_ID={household.pk}')
'''
    # subprocess.run(['docker', 'exec', '-T', container_name, ...])
```

## 3. Seed Data (`seed_source.py`)

Populates source instance via REST API. Creates data in dependency order matching the 10 migration phases. Returns a manifest dict for verification.

### Seed Data Inventory

| Phase | Model | Count | Edge Cases Covered |
|-------|-------|-------|--------------------|
| 1 | PropertyType | 5 | One per category (NUTRITION, ALLERGEN, PRICE, GOAL, OTHER) |
| 1 | SupermarketCategory | 4 | "Produce", "Dairy", "Meat", "Bakery" |
| 1 | Unit | 6 | g, kg, ml, L, cups, pieces |
| 1 | MealType | 3 | Breakfast, Lunch, Dinner |
| 2 | Keyword | 8 | Tree depth=3: Cuisine→European→Italian/French, Cuisine→Asian, Dietary→Vegetarian/Vegan, Quick Meals (root leaf) |
| 2 | Food | 12 | Tree depth=3: Protein→Meat→Chicken Breast/Ground Beef, Protein→Fish→Salmon, Dairy→Milk/Cheese, Vegetables→Tomato/Onion, Salt (root leaf, no category) |
| 3 | FoodProperty | ~8 | Tomato with 3 properties, Chicken Breast with 2, Milk with 1, some foods with 0 |
| 4 | Food.substitute | 3 | Chicken↔Ground Beef, Milk↔Cheese |
| 4 | UnitConversion | 4 | 2 generic (food=null): g↔kg, ml↔L; 2 food-specific |
| 4 | Supermarket | 2 | Each with 2 category relations |
| 4 | SupermarketCategoryRelation | 4 | Linking supermarkets to categories with order |
| 4 | Automation | 5 | Mix: FOOD_ALIAS, UNIT_ALIAS, KEYWORD_ALIAS, DESCRIPTION_REPLACE, NEVER_UNIT |
| 5 | Recipe | 5 | See recipe details below |
| 6 | Food.recipe back-patch | 1 | One food linked to a recipe |
| 6 | Step.step_recipe | 1 | One step referencing a sub-recipe |
| 7 | RecipeBook | 2 | "Favorites", "Weeknight Dinners" |
| 7 | RecipeBookEntry | 4 | Distributed across both books |
| 7 | CookLog | 3 | Different recipes, ratings, servings |
| 8 | MealPlan | 4 | Future dates; one with recipe=null (note-only); varying meal types |
| 9 | ShoppingList | 2 | "Weekly", "Party" |
| 9 | ShoppingListEntry | 6 | One with unit=null, varying amounts |
| 10 | InventoryLocation | 3 | "Fridge", "Pantry", "Freezer" (is_freezer=true) |
| 10 | InventoryEntry | 5 | One with unit=null, one with amount=0 |

### Recipe Details

| # | Name | Steps | Ingredients | Keywords | Nutrition | Notes |
|---|------|-------|-------------|----------|-----------|-------|
| 1 | Simple Salad | 1 | 3 | 2 | No | Basic recipe |
| 2 | Complex Pasta | 3 | 0+2+5 | 3 | Yes | Multiple steps, step with 0 ingredients, properties |
| 3 | Untitled Test | 1 | 1 | 0 | No | description="", source_url=null |
| 4 | Sub-Recipe Container | 2 | 1+0 | 1 | No | Step 2 has step_recipe → recipe #1 |
| 5 | Chicken Dinner | 1 | 4 | 2 | Yes | Uses Chicken Breast food |

### Key Edge Cases

- Empty description, null source_url (recipe #3)
- Step with zero ingredients (recipe #2 step 1)
- Step referencing another recipe as sub-recipe (recipe #4)
- Food with no supermarket_category (Salt)
- Food with multiple properties (Tomato: 3 properties)
- Food with self-substitute M2M
- UnitConversion with food=null (generic) vs food-specific
- ShoppingListEntry with unit=null
- MealPlan with recipe=null (note-only plan)
- InventoryEntry with amount=0 (needs `?empty=true` to read)
- InventoryLocation with is_freezer=true

## 4. Verification (`verify_migration.py`)

Fetches all data from both instances via API and compares field-by-field.

### Matching Strategy

Objects are matched by **name or composite key**, never by ID:

| Model | Match Key |
|-------|-----------|
| PropertyType | `name` |
| SupermarketCategory | `name` |
| Unit | `name` |
| MealType | `name` |
| Keyword | `name` |
| Food | `name` |
| UnitConversion | `(base_unit.name, converted_unit.name, food.name or null)` |
| Supermarket | `name` |
| Automation | `(name, type)` |
| Recipe | `name` |
| RecipeBook | `name` |
| RecipeBookEntry | `(book.name, recipe.name)` |
| CookLog | `(recipe.name, rating, servings)` |
| MealPlan | `(title, meal_type.name, from_date)` |
| ShoppingList | `name` |
| ShoppingListEntry | `(food.name, amount, unit.name or null)` |
| InventoryLocation | `name` |
| InventoryEntry | `(food.name, inventory_location.name, amount)` |

### Fields Ignored Globally

`id`, `created_by`, `created_at`, `updated_at`, `space` — these are instance-specific.

### Comparison Rules Per Model

**Simple scalar fields**: direct equality check.

**Nested FK fields** (e.g., `food`, `unit`, `supermarket_category`): compare by `.name` only.

**M2M fields** (e.g., `keywords`, `substitute`): compare as sorted sets of names.

**Tree verification** (Keyword, Food): compare `depth` values; for each node with depth > 1, verify that the parent's name matches on both sides.

**Recipe nested structure**: match steps by `order`, within each step match ingredients by `order`. Compare instruction text, ingredient amounts, food/unit names.

### Output Format

```
=== Migration Verification Report ===

PropertyType .................. PASS (5/5 matched)
SupermarketCategory ........... PASS (4/4 matched)
Unit .......................... PASS (6/6 matched)
Keyword ....................... PASS (8/8 matched, tree structure verified)
Food .......................... PASS (12/12 matched, tree structure verified)
  - Properties: 8/8 matched
  - Substitutes: 3/3 matched
UnitConversion ................ PASS (4/4 matched)
Supermarket ................... PASS (2/2 matched)
  - CategoryRelations: 4/4 matched
Automation .................... PASS (5/5 matched)
Recipe ........................ PASS (5/5 matched)
  - Steps: 8/8 matched
  - Ingredients: 16/16 matched
  - Nutrition: 2/2 matched
RecipeBook .................... PASS (2/2 matched)
RecipeBookEntry ............... PASS (4/4 matched)
CookLog ....................... PASS (3/3 matched)
MealPlan ...................... PASS (4/4 matched)
ShoppingList .................. PASS (2/2 matched)
ShoppingListEntry ............. PASS (6/6 matched)
InventoryLocation ............. PASS (3/3 matched)
InventoryEntry ................ PASS (5/5 matched)

RESULT: ALL PASSED (20/20 checks)
```

On failure:
```
Food .......................... FAIL (11/12 matched, 1 missing)
  MISSING on target: "Salt"
  MISMATCH "Tomato": supermarket_category: source="Produce", target=null
```

### VerificationResult Data Structure

```python
@dataclass
class VerificationResult:
    model_name: str
    passed: bool
    source_count: int
    target_count: int
    matched: int
    mismatches: list[str]          # field-level mismatch descriptions
    missing_on_target: list[str]   # objects in source but not target
    extra_on_target: list[str]     # objects in target but not source
```

## 5. Test Runner (`run_e2e_test.sh`)

Orchestrates the full test lifecycle. Designed for repeated regression runs.

### Execution Flow

```
1. docker compose up -d --build --wait     (build images, start, wait for health)
2. setup_instance.py source                (create superuser/space/token)
3. setup_instance.py target                (create superuser/space/token)
4. seed_source.py                          (populate source with test data)
5. [optional] migrate --dry-run            (verify no writes)
6. [optional] verify --expect-empty-target (confirm target still empty)
7. migrate_space.py                        (run actual migration)
8. verify_migration.py                     (compare source vs target)
9. [optional] migrate_space.py again       (idempotency test)
10. [optional] verify again                (confirm no duplicates)
11. cleanup or keep running                (docker compose down -v)
```

### CLI Flags

```bash
./run_e2e_test.sh                # Basic: migrate + verify
./run_e2e_test.sh --keep         # Leave containers running for debugging
./run_e2e_test.sh --dry-run      # Also test dry-run mode
./run_e2e_test.sh --idempotent   # Run migration twice, verify no duplicates
./run_e2e_test.sh --all          # Run all test modes (dry-run + migrate + idempotency)
```

### Exit Codes

- `0`: All assertions passed
- `1`: One or more verifications failed
- `2`: Infrastructure error (containers failed to start, setup failed)

### Regression Testing

For repeated runs:
- `docker compose down -v` (the `-v` flag) destroys volumes, ensuring a clean slate
- The `--build` flag on `up` rebuilds images from current code
- The seed data is deterministic (no random values) so results are reproducible
- Verification output is machine-parseable (exit code + structured report)

## 6. Shared Utilities (`conftest_helpers.py`)

```python
class TandoorAPIClient:
    """HTTP client for Tandoor API with auth, pagination, retry."""
    def __init__(self, base_url: str, token: str)
    def get_all(self, endpoint: str, params: dict = None) -> list[dict]
    def get(self, endpoint: str, pk: int) -> dict
    def post(self, endpoint: str, data: dict) -> dict
    def patch(self, endpoint: str, pk: int, data: dict) -> dict
    def put_image(self, endpoint: str, pk: int, image_bytes: bytes, filename: str) -> dict

def match_by_name(source_list, target_list, key_fn=lambda x: x['name']):
    """Match objects between source and target by key function.
    Returns: (matched_pairs, missing_on_target, extra_on_target)"""

def compare_fields(source, target, fields, label=""):
    """Compare specific fields between two dicts.
    Returns: list of mismatch descriptions"""

def compare_nested_by_name(source_val, target_val, field_name, label=""):
    """Compare a nested FK field by .name only.
    Returns: list of mismatch descriptions"""
```

## 7. Known Gotchas

| Issue | Mitigation |
|-------|------------|
| ShoppingListEntry filters out amount=0 by default | Use `?empty=true` in both seed verification and migration |
| InventoryEntry filters out amount≤0 by default | Use `?empty=true` |
| MealPlan may filter by date range | Seed with future dates; verify fetches all with wide date range |
| API rate limiting (`AI_RATELIMIT=60/hour`) | Not relevant to migration endpoints; standard endpoints have no throttle |
| Recipe image comparison | Compare presence (non-null) not bytes (target may reprocess) |
| Keyword/Food tree `path` field | Internal to treebeard; compare by `depth` + parent name instead |
| `created_at` timestamps | Cannot be preserved; ignored in comparison |
| Comment model | No write API endpoint; excluded from seed and verification |

## 8. Future Extensions

- **Cross-version testing**: Change source service from `build:` to `image: vabene1111/recipes:latest` to test upstream→fork migration
- **Large dataset testing**: Add a `--scale` flag to seed_source.py to create 100+ recipes with proportional related data
- **CI integration**: Add to `.github/workflows/` as a separate workflow that runs on PRs touching `contrib/`
- **Resume testing**: Once `migrate_space.py` supports `--save-state` / `--resume-from-phase`, add a test that interrupts at phase 5 and resumes
