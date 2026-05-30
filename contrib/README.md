# contrib/

Out-of-tree tooling for Tandoor operators. None of this is part of the upstream Tandoor codebase — it lives here because this fork carries the migration and AI provider automation.

## What's here

| Tool | Purpose | When to use |
|---|---|---|
| [`migrate_space.py`](#migrate_spacepy-live-to-live-migration) | Migrate a Space from a live source instance to a live target instance via REST API. | Both instances reachable simultaneously. Fastest path. |
| [`api_export_import/`](#api_export_import-two-step-export--import) | Export a Space to a local directory; import that directory into any target. | Source must be shut down before target is ready, or you want a reviewable on-disk snapshot. |
| [`add_ai_providers.sh`](#add_ai_providerssh-bulk-create-ai-providers) | Bulk-create the standard OpenRouter-backed AI provider lineup in a Tandoor instance. | Standing up a new instance or rebuilding providers after a wipe. |

## Credentials

All tools read credentials from project-root txt files (gitignored — never commit):

| File | Contents |
|---|---|
| `tandoor_token.txt` | Tandoor API token (Bearer). Get one in Settings → API & Auth. |
| `tandoor_url.txt` | Tandoor instance base URL (e.g. `http://192.168.1.10:8080`). |
| `openrouter_api_key.txt` | OpenRouter API key. Only needed by `add_ai_providers.sh`. |

Source-and-target tools (the two migration flavors) take `--source-*` / `--target-*` flags directly rather than reading these files, since you almost always have two different instances.

## `migrate_space.py` — live-to-live migration

One-shot Space migration. Reads from source via API, writes to target via API. Handles dependency ordering (Spaces → Foods → Units → Recipes → MealPlans, etc.), ID remapping, tree structures (treebeard `path` rewrites), circular references, and pagination automatically.

```bash
python contrib/migrate_space.py \
    --source-url http://source:8080 --source-token tda_xxx \
    --target-url http://target:8080 --target-token tda_yyy

# Dry-run — reads source, plans target writes, but doesn't write
python contrib/migrate_space.py --dry-run \
    --source-url http://source:8080 --source-token tda_xxx \
    --target-url http://target:8080 --target-token tda_yyy
```

The script lives in a single file with a top-level docstring documenting every flag. E2E tests are in [`tests/`](tests/).

**Limitations:** assumes source and target run compatible Tandoor versions. For cross-version migrations, prefer `api_export_import/` so you can inspect/transform the on-disk payload between steps.

## `api_export_import/` — two-step export → import

Decouples export from import. Useful when:
- You need to retire the source before the target is online.
- You want to inspect or transform the data on disk.
- The source and target run different Tandoor versions and you may need to massage payloads.

See [`api_export_import/README.md`](api_export_import/README.md) for full flag tables.

```bash
# 1. Export source to a directory
python contrib/api_export_import/export.py \
    --source-url http://source:8080 --source-token tda_xxx \
    --output ./my_export

# 2. Import that directory into a target
python contrib/api_export_import/import.py \
    --target-url http://target:8080 --target-token tda_yyy \
    --input ./my_export
```

Output structure: one JSON file per model type, a `manifest.json` for the manifest of contents and version compatibility, and an `images/` subdirectory for recipe image files.

E2E tests live in [`api_export_import/tests/`](api_export_import/tests/) and run via docker-compose.

## `add_ai_providers.sh` — bulk-create AI providers

Creates the standard OpenRouter-backed lineup (Sonnet 4.5, Haiku 4.5, GPT-4o, DeepSeek V3, Gemini 3.5/3.1/2.5 variants) with task-specific descriptions. Each provider's description explains when to use that model in Tandoor's AI features (file import, step sort, food/recipe property extraction).

```bash
export TANDOOR_URL=$(cat tandoor_url.txt)
export TANDOOR_TOKEN=$(cat tandoor_token.txt)
export OR_KEY=$(cat openrouter_api_key.txt)
./contrib/add_ai_providers.sh
```

**Not idempotent** — re-running creates duplicates. Delete old ones in the UI first if you want a clean re-import. Edit the `create "..."` lines to change the lineup.

The script resolves your currently-active Tandoor Space at runtime and creates providers scoped to it. To target a different Space, switch your active Space in the Tandoor UI before running.

## Claude Code skills

Companion documentation for AI assistants (and humans who like a clear reference) lives in [`.claude/skills/`](../.claude/skills/):

- **`tandoor-ai-providers/`** — model-to-task recommendations, API quirks (notably the space-override on PATCH for superusers), POST/PATCH payload examples.
- **`tandoor-recipe-mapping/`** — first-mention ingredient↔step mapping algorithm, the M2M-PATCH-doesn't-auto-unlink quirk, German cooking-term synonym map, audit jq for finding unmapped recipes.

## Tests

- [`tests/`](tests/) — E2E tests for `migrate_space.py`. Docker-compose spins up two Tandoor instances; `seed_source.py` seeds the source, the migration runs, `verify_migration.py` confirms target state.
- [`api_export_import/tests/`](api_export_import/tests/) — E2E tests for the two-step flow. Same docker-compose pattern.

Both test suites assume a working Docker daemon and pull official Tandoor images.
