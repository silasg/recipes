# Tandoor Recipes: Space Data Migration Research

## 1. Problem Statement

The built-in Default export (`cookbook/integration/default.py`) only exports recipe data via `RecipeExportSerializer`. It includes recipe name, description, steps, ingredients (food name, unit name, amount), keywords (name only), and nutrition. It does **not** export any "setup data" — foods with hierarchy/properties, units, unit conversions, property types, automations, supermarket mappings, meal plans, shopping lists, inventory, etc.

Goal: Transfer **all** space data from one Tandoor instance to another using only the REST API, with no direct server/DB access.

---

## 2. Complete Model Relationship Inventory

### 2.1 Core Models

#### Space
- **ForeignKeys**: `created_by` → User, `image`/`custom_space_theme`/`nav_logo`/`logo_color_*` → UserFile (all nullable, SET_NULL), `ai_default_provider` → AiProvider (nullable), `default_unit` → Unit (nullable, SET_NULL) *(fork addition: fallback unit for property calculations)*
- **ManyToMany**: `food_inherit` → FoodInheritField

#### UserSpace
- **ForeignKeys**: `user` → User, `space` → Space, `household` → Household (nullable, PROTECT), `invite_link` → InviteLink (nullable)
- **ManyToMany**: `groups` → Group (Django auth)

#### Household
- **ForeignKeys**: `space` → Space (CASCADE)

#### FoodInheritField
- No ForeignKeys — standalone reference table

#### SearchFields
- No ForeignKeys — standalone reference table

### 2.2 Recipe Ecosystem

#### Keyword (treebeard MP_Node)
- **ForeignKeys**: `space` → Space
- **Tree**: Self-referential via treebeard (path, depth, numchild)
- **Unique constraint**: `(space, name)`

#### Unit
- **ForeignKeys**: `space` → Space
- **Unique constraint**: `(space, name)`

#### Food (treebeard MP_Node)
- **ForeignKeys**: `recipe` → Recipe (nullable, SET_NULL), `supermarket_category` → SupermarketCategory (nullable), `properties_food_unit` → Unit (nullable), `preferred_unit` → Unit (nullable), `preferred_shopping_unit` → Unit (nullable), `space` → Space
- **ManyToMany**: `shopping_lists` → ShoppingList, `onhand_users` → User, `inherit_fields` → FoodInheritField, `child_inherit_fields` → FoodInheritField, `properties` → Property (through='FoodProperty'), `substitute` → self
- **Tree**: Self-referential via treebeard (path, depth, numchild)
- **Unique constraint**: `(space, name)`

#### NutritionInformation
- **ForeignKeys**: `space` → Space

#### Ingredient
- **ForeignKeys**: `food` → Food (nullable), `unit` → Unit (nullable), `space` → Space
- **Note**: `created_by` → User (PROTECT)

#### Step
- **ForeignKeys**: `file` → UserFile (nullable), `step_recipe` → Recipe (nullable), `space` → Space
- **ManyToMany**: `ingredients` → Ingredient

#### Recipe
- **ForeignKeys**: `storage` → Storage (nullable), `nutrition` → NutritionInformation (nullable), `created_by` → User, `space` → Space
- **ManyToMany**: `keywords` → Keyword, `steps` → Step, `properties` → Property, `shared` → User

### 2.3 Properties System

#### PropertyType
- **ForeignKeys**: `space` → Space
- **Unique constraint**: `(space, name)`
- **Categories**: NUTRITION, ALLERGEN, PRICE, GOAL, OTHER

#### Property
- **ForeignKeys**: `property_type` → PropertyType, `space` → Space

#### FoodProperty (through table)
- **ForeignKeys**: `food` → Food, `property` → Property
- **Unique constraint**: `(food, property)`

### 2.4 Unit Conversions

#### UnitConversion
- **ForeignKeys**: `base_unit` → Unit (PROTECT), `converted_unit` → Unit (PROTECT), `food` → Food (nullable), `created_by` → User, `space` → Space
- **Unique constraint**: `(space, base_unit, converted_unit, food)`

### 2.5 Supermarket System

#### SupermarketCategory
- **ForeignKeys**: `space` → Space
- **Unique constraint**: `(space, name)`

#### Supermarket
- **ForeignKeys**: `space` → Space
- **ManyToMany**: `categories` → SupermarketCategory (through='SupermarketCategoryRelation'), `shopping_lists` → ShoppingList

#### SupermarketCategoryRelation (through table)
- **ForeignKeys**: `supermarket` → Supermarket, `category` → SupermarketCategory

### 2.6 Automation

#### Automation
- **ForeignKeys**: `created_by` → User, `space` → Space
- **10 types**: FOOD_ALIAS, UNIT_ALIAS, KEYWORD_ALIAS, DESCRIPTION_REPLACE, INSTRUCTION_REPLACE, NEVER_UNIT, FOOD_REPLACE, UNIT_REPLACE, NAME_REPLACE, TRANSPOSE_WORDS
- **Note**: References Food/Unit/Keyword by **name strings** in `param_1`/`param_2`/`param_3` fields — no FK constraints, but names must match migrated objects

### 2.7 Meal Planning

#### MealType
- **ForeignKeys**: `created_by` → User, `space` → Space

#### MealPlan
- **ForeignKeys**: `recipe` → Recipe (nullable), `created_by` → User, `meal_type` → MealType, `space` → Space
- **ManyToMany**: `shared` → User

### 2.8 Shopping Lists

#### ShoppingList
- **ForeignKeys**: `space` → Space

#### ShoppingListRecipe
- **ForeignKeys**: `recipe` → Recipe (nullable), `mealplan` → MealPlan (nullable), `created_by` → User, `space` → Space

#### ShoppingListEntry
- **ForeignKeys**: `list_recipe` → ShoppingListRecipe (nullable), `food` → Food, `unit` → Unit (nullable), `ingredient` → Ingredient (nullable), `created_by` → User, `space` → Space
- **ManyToMany**: `shopping_lists` → ShoppingList

### 2.9 Inventory System

#### InventoryLocation
- **ForeignKeys**: `household` → Household (PROTECT), `created_by` → User, `space` → Space

#### InventoryEntry
- **ForeignKeys**: `inventory_location` → InventoryLocation (CASCADE), `unit` → Unit (nullable), `food` → Food (nullable), `created_by` → User, `space` → Space

#### InventoryLog
- **ForeignKeys**: `entry` → InventoryEntry, `old_inventory_location` → InventoryLocation, `new_inventory_location` → InventoryLocation, `space` → Space

### 2.10 Collections & Sharing

#### RecipeBook
- **ForeignKeys**: `created_by` → User, `filter` → CustomFilter (nullable), `space` → Space
- **ManyToMany**: `shared` → User

#### RecipeBookEntry
- **ForeignKeys**: `recipe` → Recipe, `book` → RecipeBook

#### CustomFilter
- **ForeignKeys**: `created_by` → User, `space` → Space
- **ManyToMany**: `shared` → User

#### Comment
- **ForeignKeys**: `recipe` → Recipe, `created_by` → User

#### ShareLink
- **ForeignKeys**: `recipe` → Recipe, `created_by` → User, `space` → Space

### 2.11 Logging

#### CookLog
- **ForeignKeys**: `recipe` → Recipe, `created_by` → User, `space` → Space

#### ViewLog
- **ForeignKeys**: `recipe` → Recipe, `created_by` → User, `space` → Space

### 2.12 Storage & Sync

#### Storage
- **ForeignKeys**: `created_by` → User, `space` → Space

#### Sync
- **ForeignKeys**: `storage` → Storage, `space` → Space

#### UserFile
- **ForeignKeys**: `created_by` → User, `space` → Space

---

## 3. API Endpoints & Serializer Behavior

### 3.1 Pagination Defaults
- **DefaultPagination**: page_size=50, page_size_query_param='page_size', max_page_size=200
- **RecipePagination** (recipes only): page_size=25, max_page_size=100

### 3.2 Auto-Creation (get_or_create) on Write

These models use `get_or_create` with case-insensitive name matching when written via API, preventing duplicates:

| Model | Serializer Line | Match Field |
|---|---|---|
| Food | serializer.py:953 | `name` |
| Unit | serializer.py:751 | `name__iexact` |
| Keyword | serializer.py:724 | `name` |
| PropertyType | serializer.py:815 | `name__iexact` |
| SupermarketCategory | serializer.py:772 | `name__iexact` |
| Supermarket | serializer.py:799 | `name__iexact` |
| ShoppingList | serializer.py:528 | `name__iexact` |
| MealType | serializer.py:543 | `name__iexact` |

### 3.3 Nested Object Auto-Creation Cascade

When POSTing a **Recipe**, the WritableNestedModelSerializer triggers:

```
Recipe.create()
  ├─ Keyword.get_or_create()        [for each keyword]
  ├─ NutritionInformation.create()  [if provided]
  ├─ Property.create()              [for each property]
  │    └─ PropertyType.get_or_create()
  └─ Step.create()                  [for each step]
       └─ Ingredient.create()       [for each ingredient]
            ├─ Food.get_or_create()
            │    └─ SupermarketCategory.get_or_create()
            └─ Unit.get_or_create()
```

When POSTing a **Food**, auto-creates:
- SupermarketCategory (via get_or_create)
- Property objects (manual create, linked via FoodProperty)
- Unit (for properties_food_unit)

When POSTing a **MealPlan**, auto-creates:
- MealType (via get_or_create)
- ShoppingListRecipe (if `addshopping=true`)

### 3.4 API Endpoint Reference

| Data | Endpoint | Methods |
|---|---|---|
| Food | `/api/food/` | GET, POST, PUT, PATCH, DELETE |
| Unit | `/api/unit/` | GET, POST, PUT, PATCH, DELETE |
| Keyword | `/api/keyword/` | GET, POST, PUT, PATCH, DELETE |
| Recipe | `/api/recipe/` | GET, POST, PUT, PATCH, DELETE |
| Step | `/api/step/` | GET, POST, PUT, PATCH, DELETE |
| Ingredient | `/api/ingredient/` | GET, POST, PUT, PATCH, DELETE |
| PropertyType | `/api/property-type/` | GET, POST, PUT, PATCH, DELETE |
| Property | `/api/property/` | GET, POST, PUT, PATCH, DELETE |
| UnitConversion | `/api/unit-conversion/` | GET, POST, PUT, PATCH, DELETE |
| SupermarketCategory | `/api/supermarket-category/` | GET, POST, PUT, PATCH, DELETE |
| Supermarket | `/api/supermarket/` | GET, POST, PUT, PATCH, DELETE |
| SupermarketCategoryRelation | `/api/supermarket-category-relation/` | GET, POST, PUT, PATCH, DELETE |
| Automation | `/api/automation/` | GET, POST, PUT, PATCH, DELETE |
| MealType | `/api/meal-type/` | GET, POST, PUT, PATCH, DELETE |
| MealPlan | `/api/meal-plan/` | GET, POST, PUT, PATCH, DELETE |
| ShoppingList | `/api/shopping-list/` | GET, POST, PUT, PATCH, DELETE |
| ShoppingListEntry | `/api/shopping-list-entry/` | GET, POST, PUT, PATCH, DELETE |
| ShoppingListRecipe | `/api/shopping-list-recipe/` | GET, POST, PUT, PATCH, DELETE |
| RecipeBook | `/api/recipe-book/` | GET, POST, PUT, PATCH, DELETE |
| RecipeBookEntry | `/api/recipe-book-entry/` | GET, POST, PUT, PATCH, DELETE |
| CookLog | `/api/cook-log/` | GET, POST, PUT, PATCH, DELETE |
| ViewLog | `/api/view-log/` | GET, POST, PUT, PATCH, DELETE |
| CustomFilter | `/api/custom-filter/` | GET, POST, PUT, PATCH, DELETE |
| InventoryLocation | `/api/inventory-location/` | GET, POST, PUT, PATCH, DELETE |
| InventoryEntry | `/api/inventory-entry/` | GET, POST, PUT, PATCH, DELETE |
| InventoryLog | `/api/inventory-log/` | GET, POST, PUT, PATCH, DELETE |
| UserFile | `/api/user-file/` | GET, POST, PUT, PATCH, DELETE |

### 3.5 Special Query Parameters

| Endpoint | Parameter | Description |
|---|---|---|
| `/api/food/` | `?simple=true` | Returns FoodSimpleSerializer (less data) |
| `/api/food/` | `?query=text` | Name search (case-insensitive, with optional unaccent) |
| `/api/recipe/` | `?query=text` | Full-text recipe search |
| `/api/meal-plan/` | `?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD` | Date range filter |
| `/api/meal-plan/` | `?meal_type=ID` | Filter by meal type (supports repeat) |
| `/api/unit-conversion/` | `?food_id=ID` | Filter by food |
| `/api/shopping-list-entry/` | `?updated_after=ISO8601` | Filter by update time |
| `/api/shopping-list-entry/` | `?empty=true` | Include zero-amount entries |
| `/api/property-type/` | `?category=nutrient` | Filter by category |
| `/api/automation/` | `?type=food_alias` | Filter by automation type |
| `/api/custom-filter/` | `?type=recipe` | Filter by model type |
| `/api/inventory-entry/` | `?empty=true` | Include zero-amount entries |
| `/api/recipe-book-entry/` | `?book=ID` | Filter by book |
| `/api/cook-log/` | `?recipe=ID` | Filter by recipe |

---

## 4. Database Constraints

### 4.1 PROTECT Constraints (prevent deletion, affect migration rollback)
- `UnitConversion.base_unit` → Unit
- `UnitConversion.converted_unit` → Unit
- `UserSpace.household` → Household
- `InventoryLocation.household` → Household
- `Space.created_by` → User
- `Ingredient.created_by` → User
- `PropertyType.created_by` → User

### 4.2 Unique Constraints (affect deduplication)
- `(space, name)` on: Food, Unit, Keyword, MealType, PropertyType, SupermarketCategory, Supermarket
- `(space, open_data_slug)` on: Unit, Food, UnitConversion, PropertyType
- `(space, base_unit, converted_unit, food)` on: UnitConversion
- `(food, property)` on: FoodProperty
- `(space, code)` on: InventoryEntry

### 4.3 SET_NULL Constraints (nullable FKs that can be deferred)
- Food.recipe → Recipe
- Food.preferred_unit, preferred_shopping_unit, properties_food_unit → Unit
- Food.supermarket_category → SupermarketCategory
- Recipe.nutrition → NutritionInformation
- Recipe.storage → Storage
- MealPlan.recipe → Recipe
- ShoppingListRecipe.recipe, mealplan → Recipe, MealPlan
- ShoppingListEntry.list_recipe, unit, ingredient → various
- InventoryEntry.unit, food → Unit, Food
- Space.ai_default_provider → AiProvider

---

## 5. Mealie1 Importer: Reference Implementation

The Mealie1 backup importer (`cookbook/integration/mealie1.py`) is the most comprehensive existing import implementation and serves as a proven reference for migration ordering.

### 5.1 Creation Order Used

1. **Keywords** (lines 31-45) — from categories and tags
2. **SupermarketCategory** (lines 50-56) — from multi-purpose labels
3. **Food** (lines 61-77) — with supermarket_category references
4. **Unit** (lines 82-88) — standalone
5. **Recipe** (lines 94-134) — main recipe objects
6. **Step** (lines 144-171) — instructions and notes
7. **Ingredient** (lines 188-221) — with food/unit references
8. **PropertyType & Property** (lines 241-274) — nutrition data
9. **CookLog** (lines 286-308) — from comments and timeline events
10. **MealType & MealPlan** (lines 314-332) — conditional on flag
11. **ShoppingListEntry** (lines 338-360) — conditional on flag
12. **Recipe Images** (lines 364-369) — last, with error handling

### 5.2 ID Remapping Strategy

Uses dictionary-based maps per model type:

```python
keywords_categories_dict = {}   # old_id → new Keyword.pk
keywords_tags_dict = {}         # old_id → new Keyword.pk
supermarket_categories_dict = {}  # old_id → new SupermarketCategory.pk
foods_dict = {}                 # old_id → new Food.pk
units_dict = {}                 # old_id → new Unit.pk
recipes_dict = {}               # old_id → new Recipe.pk
step_id_dict = {}               # old_id → new Step.pk
first_step_of_recipe_dict = {}  # recipe_id → first Step.pk
recipe_ingredient_ref_link_dict = {}  # reference_id → instruction_id
```

### 5.3 Key Patterns

- **Deduplication**: Filter by `name + space` before creating; skip if exists
- **Bulk operations**: Uses `bulk_create(ignore_conflicts=True)` for M2M through tables
- **Graceful fallback**: Missing FK references set to `None` rather than failing
- **Atomic transactions**: Property creation wrapped in `transaction.atomic()`
- **Name truncation**: Recipe names capped at 128 chars
- **Image handling**: Done last with try/except per recipe

---

## 6. Circular Dependencies & Resolution Strategy

### 6.1 Food ↔ Recipe
- **Problem**: Food has nullable FK to Recipe; Recipe has Steps → Ingredients → Food
- **Resolution**: Create Food first with `recipe=null`. Create Recipe (which creates Ingredients referencing Food). Then PATCH Food.recipe if needed.

### 6.2 Food.substitute (M2M to self)
- **Problem**: Foods can reference other Foods as substitutes
- **Resolution**: Two-pass — create all Foods first, then PATCH substitute M2M relationships

### 6.3 Treebeard Trees (Keyword, Food)
- **Problem**: MP_Node manages internal `path`/`depth`/`numchild` fields. Raw inserts break the tree.
- **Resolution**: Create root nodes first via API, then children. The API calls treebeard's `add_child()` internally. Must process in breadth-first order (all depth-1, then depth-2, etc.)

### 6.4 Step.step_recipe (Step referencing Recipe)
- **Problem**: Steps can reference other recipes (sub-recipes/linked recipes)
- **Resolution**: Create all Recipes first (without step_recipe links), then PATCH steps that need step_recipe references

---

## 7. Data NOT Transferable via API

Some data is inherently instance-specific and cannot or should not be migrated:

| Data | Reason |
|---|---|
| User accounts | Auth system; users must exist on target |
| Space configuration | Created on target instance |
| Storage configurations | Credentials are instance-specific |
| Sync configurations | Tied to storage configs |
| API tokens | Security-sensitive |
| InviteLinks | Instance-specific |
| ShareLinks | URL-tied to source instance |
| ImportLog / ExportLog | Historical, not needed |
| AiProvider | API keys are sensitive |
| AiLog | Historical |
| BookmarkletImport | Transient |
| ConnectorConfig | Instance-specific credentials |
| TelegramBot | Instance-specific tokens |
| UserPreference | Partially transferable (display prefs yes, user FKs no) |
| SearchPreference | Tied to user |

---

## 8. Final Dependency-Ordered Migration Phases

```
Phase 1 — Standalone setup data (no FK dependencies)
  ├─ PropertyType           /api/property-type/
  ├─ SupermarketCategory    /api/supermarket-category/
  ├─ Unit                   /api/unit/
  ├─ MealType               /api/meal-type/
  └─ CustomFilter           /api/custom-filter/

Phase 2 — Tree structures (self-referential, parents before children)
  ├─ Keyword                /api/keyword/        (breadth-first by depth)
  └─ Food (basic)           /api/food/           (breadth-first by depth)
        ├─ FK → SupermarketCategory (from Phase 1)
        └─ FK → Unit for preferred_unit etc. (from Phase 1)

Phase 3 — Properties (depend on Phase 1 + 2)
  ├─ Property               /api/property/       (FK → PropertyType)
  └─ FoodProperty           (PATCH Food to attach properties)

Phase 4 — Remaining setup data (depend on Phase 1 + 2)
  ├─ Food.substitute M2M    (PATCH Food with substitute list)
  ├─ UnitConversion         /api/unit-conversion/ (FK → Unit ×2, FK → Food)
  ├─ Supermarket            /api/supermarket/
  ├─ SupermarketCategoryRelation  /api/supermarket-category-relation/
  └─ Automation             /api/automation/

Phase 5 — Recipes (depend on Phase 2)
  └─ Recipe                 /api/recipe/         (with inline Steps, Ingredients, Keywords, Nutrition)
       ├─ Ingredients reference Food + Unit from Phase 1-2
       ├─ Keywords from Phase 2
       └─ Recipe images uploaded separately via PUT /api/recipe/{id}/image/

Phase 6 — Post-recipe back-patches (depend on Phase 5)
  ├─ Food.recipe FK         (PATCH foods that link to recipes)
  ├─ Step.step_recipe FK    (PATCH steps that link to sub-recipes)
  └─ Recipe.properties M2M  (PATCH recipes with properties)

Phase 7 — Collections (depend on Phase 5)
  ├─ RecipeBook             /api/recipe-book/
  ├─ RecipeBookEntry        /api/recipe-book-entry/ (FK → Recipe + RecipeBook)
  ├─ Comment                (no dedicated write endpoint — may need workaround)
  ├─ CookLog                /api/cook-log/
  └─ ViewLog                /api/view-log/

Phase 8 — Meal planning (depend on Phase 5)
  └─ MealPlan               /api/meal-plan/      (FK → Recipe, FK → MealType)

Phase 9 — Shopping (depend on Phase 2 + 5 + 8)
  ├─ ShoppingList           /api/shopping-list/
  ├─ ShoppingListRecipe     /api/shopping-list-recipe/
  └─ ShoppingListEntry      /api/shopping-list-entry/

Phase 10 — Inventory (depend on Phase 1 + 2)
  ├─ InventoryLocation      /api/inventory-location/
  ├─ InventoryEntry         /api/inventory-entry/
  └─ InventoryLog           /api/inventory-log/
```

---

## 9. ID Remapping Strategy

Every model gets a new primary key on the target instance. The migration script must maintain mapping dictionaries:

```python
id_maps = {
    'property_type': {},      # source_id → target_id
    'supermarket_category': {},
    'unit': {},
    'meal_type': {},
    'custom_filter': {},
    'keyword': {},
    'food': {},
    'property': {},
    'unit_conversion': {},
    'supermarket': {},
    'automation': {},
    'recipe': {},
    'step': {},               # needed for step_recipe back-patches
    'ingredient': {},         # needed for shopping list entry references
    'nutrition': {},
    'recipe_book': {},
    'meal_plan': {},
    'shopping_list': {},
    'shopping_list_recipe': {},
    'inventory_location': {},
    'inventory_entry': {},
}
```

For each model:
1. GET all objects from source (paginated)
2. POST to target with remapped FK IDs
3. Store `source_id → target_id` in the map
4. Use the map when creating dependent objects

---

## 10. Pagination Strategy

All list endpoints use cursor/page-based pagination:

```
GET /api/food/?page=1&page_size=200
→ { "count": 500, "next": "...?page=2&page_size=200", "results": [...] }
```

The script should:
1. Start with `page=1&page_size=200` (max allowed)
2. Follow `next` URL until null
3. Collect all results before proceeding to the next phase

---

## 11. Image/File Transfer Strategy

Recipe images and user files require binary transfer:

1. **Download**: GET recipe detail → `image` field contains URL → download binary
2. **Upload**: PUT `/api/recipe/{id}/image/` with multipart form data containing the image file

Step files (UserFile) follow a similar pattern via `/api/user-file/`.
