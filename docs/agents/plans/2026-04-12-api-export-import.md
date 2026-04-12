---
date: 2026-04-12T09:30:50+00:00
git_commit: c73ccdc07ef9f685e52845698a5073ff6dfca079
branch: claude/migration
topic: "Two-step API export/import for space data migration"
tags: [plan, migration, api, export, import]
status: approved
---

# Two-Step API Export/Import Implementation Plan

## Overview

Split the current direct-pipe migration into two independent scripts in a new `contrib/api_export_import/` directory. `export.py` extracts all space data from a source Tandoor instance into a local directory of JSON files + images. `import.py` reads that directory and pushes data to any target instance. This enables extracting once from a source you don't control and importing repeatedly without hitting the source again.

## Current State Analysis

The existing `contrib/migrate_space.py` works as a direct pipe:
- Reads from source API and writes to target API in the same pass
- 10 phase functions interleave source reads with target writes
- Phase 5 fetches full recipe details (`get_one`) beyond list endpoints
- Phase 6 re-fetches foods and recipes for back-patches
- Recipe images are downloaded and re-uploaded inline
- `MigrationState` tracks source→target ID mappings

### Key Discoveries:
- All source data is accessed via `TandoorClient.get_all()` and `TandoorClient.get_one()` — clean separation point (`contrib/migrate_space.py:71-91`)
- The `_food_extra`, `_build_recipe_payload` helpers do both data extraction and ID remapping in one step — these need splitting
- Recipe detail fetches happen in phase 5 (line 523) and phase 6 (lines 580, 614) — export must capture full details upfront
- Images are binary data handled via `download_image()` (line 123) — stored as separate files in export directory
- The existing `MigrationState` class is only needed during import (ID remapping) — not during export

## Desired End State

Two standalone scripts in `contrib/api_export_import/`:

```
contrib/api_export_import/
├── export.py          # Source API → local directory
├── import.py          # Local directory → target API
├── client.py          # Shared TandoorClient + APIError
└── tests/
    ├── run_e2e_test.sh    # Export→import→verify cycle
    └── ...                # Reuse seed/verify/setup from contrib/tests/
```

Export output directory structure:
```
my_export/
├── manifest.json              # metadata: source URL, timestamp, version, counts
├── property_type.json
├── supermarket_category.json
├── unit.json
├── meal_type.json
├── custom_filter.json
├── keyword.json
├── food.json                  # includes properties, substitutes, recipe FK
├── unit_conversion.json
├── supermarket.json
├── supermarket_category_relation.json
├── automation.json
├── recipe.json                # full details: steps, ingredients, nutrition, keywords
├── recipe_book.json
├── recipe_book_entry.json
├── cook_log.json
├── view_log.json
├── meal_plan.json
├── shopping_list.json
├── shopping_list_recipe.json
├── shopping_list_entry.json
├── inventory_location.json
├── inventory_entry.json
└── images/
    ├── recipe_42.png
    └── recipe_87.png
```

### CLI Interface:

```bash
# Export from source
python export.py \
    --source-url http://source:8080 --source-token tda_xxx \
    --output ./my_export

# Validate export (check file structure and internal consistency)
python import.py validate ./my_export

# Dry-run import (connect to target, report what would be created, no writes)
python import.py dry-run ./my_export \
    --target-url http://target:8080 --target-token tda_yyy

# Live import
python import.py run ./my_export \
    --target-url http://target:8080 --target-token tda_yyy

# Import with resume support
python import.py run ./my_export \
    --target-url http://target:8080 --target-token tda_yyy \
    --save-state ./state.json --resume-from-phase 5
```

### Verification:

```bash
# After import, verify using existing verify_migration.py
python contrib/tests/verify_migration.py \
    <source_url> <source_token> \
    <target_url> <target_token>
```

## What We're NOT Doing

- NOT modifying the existing `contrib/migrate_space.py` — it stays as the direct-pipe tool
- NOT changing the E2E test infrastructure in `contrib/tests/` — we add a new test alongside it
- NOT adding a GUI or interactive mode
- NOT adding incremental/delta export — each export is a full snapshot
- NOT migrating models that the current script already skips (Comments, UserFiles, etc.)

## Implementation Approach

Copy the migration logic from `migrate_space.py` into the new directory, then refactor into export (source reads only) and import (target writes only with ID remapping). The `TandoorClient` and `APIError` classes are shared. The export captures raw API responses as-is; the import handles all the payload construction, ID remapping, and Tandoor serializer quirks.

## Phase 1: Project Structure and Shared Client

### Overview
Create the new directory, extract the shared `TandoorClient` and `APIError` into `client.py`, and set up the module structure.

### Changes Required:

#### [x] 1. Create directory structure
```bash
contrib/api_export_import/
├── __init__.py
├── client.py
├── export.py
├── import.py
└── tests/
    └── run_e2e_test.sh
```

#### [x] 2. `client.py` — shared API client
**File**: `contrib/api_export_import/client.py`
**Changes**: Copy `TandoorClient` and `APIError` from `migrate_space.py` (lines 42-139). No modifications needed — these classes are already cleanly separated.

### Success Criteria:

#### Automated Verification:
- [ ] `python -c "from contrib.api_export_import.client import TandoorClient, APIError"` succeeds

#### Manual Verification:
- [ ] Directory structure matches the plan

---

## Phase 2: Export Script

### Overview
Implement `export.py` that reads all space data from a source instance and writes it to a local directory. Each model type gets its own JSON file. Recipe images are downloaded into an `images/` subdirectory.

### Changes Required:

#### [x] 1. Export data fetching logic
**File**: `contrib/api_export_import/export.py`
**Changes**: Implement the export in dependency order (matching the existing 10 phases). For each model:
- Fetch via `get_all()` (and `get_one()` for recipe details)
- Write raw API response to `<model>.json`
- For recipes: fetch full details per recipe, download images

Key differences from `migrate_space.py`:
- No target client, no `MigrationState`, no ID remapping
- Store raw source API responses as-is (preserving all fields including `id`)
- Recipe details fetched upfront and stored complete (no re-fetch during back-patches)
- Images saved as `images/recipe_<id>.png` with the source ID

#### [x] 2. Manifest file
**File**: (generated at export time)
**Changes**: Write `manifest.json` containing:
```json
{
  "version": 1,
  "source_url": "http://...",
  "exported_at": "2026-04-12T...",
  "counts": {
    "property_type": 5,
    "recipe": 42,
    ...
  }
}
```

#### [x] 3. CLI interface
**File**: `contrib/api_export_import/export.py`
**Changes**: argparse with:
- `--source-url` (required)
- `--source-token` (required)
- `--output` (required, directory path — created if not exists)
- `--timeout` (optional, default 60)
- `-v` / `--verbose`

### Success Criteria:

#### Automated Verification:
- [ ] Running export against a seeded test instance produces the expected directory with all JSON files
- [ ] `manifest.json` contains correct counts matching seeded data
- [ ] All JSON files are valid JSON and contain lists of objects with `id` fields

#### Manual Verification:
- [ ] Exported JSON files are human-readable and inspectable
- [ ] Recipe images are saved as valid image files

---

## Phase 3: Import Script

### Overview
Implement `import.py` with three subcommands: `validate`, `dry-run`, and `run`. The import reads from the local export directory, constructs API payloads (handling all the Tandoor serializer quirks like `name` in nested objects, `shared: []` for recipe books, `household` for inventory), and pushes to the target.

### Changes Required:

#### [x] 1. Import data loading
**File**: `contrib/api_export_import/import.py`
**Changes**: Load all JSON files from the export directory into memory. Validate file structure against expected models.

#### [x] 2. `validate` subcommand
**File**: `contrib/api_export_import/import.py`
**Changes**: Check the export directory for:
- `manifest.json` exists and is valid
- All expected model JSON files exist
- Internal consistency: FK references (e.g., recipe ingredients reference food IDs that exist in `food.json`)
- Image files referenced by recipes exist in `images/`
- Report summary of what's in the export

#### [x] 3. `dry-run` subcommand
**File**: `contrib/api_export_import/import.py`
**Changes**: Connect to target, run through all import phases without writing. Report what would be created. Uses the same phase logic as `run` but with `dry_run=True`.

#### [x] 4. `run` subcommand — import phases
**File**: `contrib/api_export_import/import.py`
**Changes**: Port the 10 phase functions from `migrate_space.py`, but reading from local files instead of source API. Key adaptations:

- **Phase 1-2**: Read from `property_type.json`, `unit.json`, etc. instead of `source.get_all()`
- **Phase 3**: Food properties are already in `food.json` (exported with full detail) — no re-fetch needed
- **Phase 4**: Substitutes from `food.json`, conversions from `unit_conversion.json`
- **Phase 5**: Full recipe details already in `recipe.json` — no per-recipe `get_one()` needed. Upload images from `images/` directory
- **Phase 6**: Back-patch data (food.recipe, step.step_recipe) already in the export files — no re-fetch
- **Phase 7-10**: Same payload construction with ID remapping, reading from local JSON

All the Tandoor serializer quirks (name in nested objects, shared=[], household, omitting null food on unit conversions) carry over from the fixes in `migrate_space.py`.

#### [x] 5. `MigrationState` and resume support
**File**: `contrib/api_export_import/import.py`
**Changes**: Copy `MigrationState` from `migrate_space.py`. Support `--save-state` and `--resume-from-phase` flags on the `run` subcommand.

#### [x] 6. CLI interface
**File**: `contrib/api_export_import/import.py`
**Changes**: argparse with subcommands:
- `validate <export_dir>`
- `dry-run <export_dir> --target-url ... --target-token ...`
- `run <export_dir> --target-url ... --target-token ... [--save-state ...] [--resume-from-phase N]`

### Success Criteria:

#### Automated Verification:
- [ ] `validate` passes on a valid export directory
- [ ] `validate` fails with clear error on a broken export (missing files, broken references)
- [ ] `dry-run` connects to target and reports counts without writing
- [ ] `run` imports all data and `verify_migration.py` passes comparing source to target

#### Manual Verification:
- [ ] Error messages from `validate` are clear and actionable
- [ ] `dry-run` output is useful for planning

---

## Phase 4: E2E Test

### Overview
Add an E2E test that exercises the full export→import→verify cycle using the local Django test servers (same approach as `run_e2e_local.sh`).

### Changes Required:

#### [x] 1. E2E test script
**File**: `contrib/api_export_import/tests/run_e2e_test.sh`
**Changes**: Script that:
1. Starts source + target Django dev servers (SQLite, reusing `e2e_settings.py`)
2. Sets up instances (reusing `setup_instance.py` inline approach from `run_e2e_local.sh`)
3. Seeds source (reusing `seed_source.py`)
4. Runs `export.py` → local directory
5. Runs `import.py validate` on the export
6. Runs `import.py dry-run` against target
7. Runs `import.py run` against target
8. Runs `verify_migration.py` comparing source and target
9. Cleans up

Should also test: export once, import to two separate targets (proving the "extract once, push many" use case).

### Success Criteria:

#### Automated Verification:
- [ ] `run_e2e_test.sh` passes end-to-end in the sandbox (Python 3.12 venv)
- [ ] All verification checks pass (17/17)

#### Manual Verification:
- [ ] Test output is clear and shows each phase progressing

---

## Manual Confirmation Points

Pause for manual confirmation after:
1. **Phase 2** (export) — inspect exported directory structure and JSON content before building import
2. **Phase 4** (E2E test) — final check that everything works end-to-end

## Testing Strategy

### Unit Tests:
- Validate subcommand catches missing files, broken JSON, dangling FK references
- Export produces correct file count matching source data

### Integration Tests:
- Full export→import→verify cycle (the E2E test in Phase 4)
- Export once, import to two different targets
- Resume from mid-phase after simulated failure

### Manual Testing Steps:
1. Run export against the Docker-based source instance (upstream 2.6.4 image)
2. Inspect exported JSON files — verify emoji keywords, all automation types present
3. Run import against the Docker-based target instance (fork image)
4. Browse target instance in browser, verify recipes and data look correct

## Performance Considerations

- Export fetches each recipe individually (`get_one`) for full details — same as current script. For large instances (1000+ recipes), this could be slow. Acceptable for v1.
- Import is write-heavy but sequential — same as current script. No parallelism needed for v1.
- The export directory could be large if many recipe images exist. JSON files themselves will be small.

## Migration Notes

- The existing `contrib/migrate_space.py` is untouched — users can continue using it for direct migration
- The new tool is an alternative workflow for the same data, not a replacement
- Export format version (`"version": 1` in manifest) allows future evolution

## References

- Current migration script: `contrib/migrate_space.py`
- E2E test infrastructure: `contrib/tests/`
- Local test runner: `contrib/tests/run_e2e_local.sh`
- Tandoor serializer quirks documented through fixes in commit `f1773bfb4`
