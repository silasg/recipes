# API Export/Import

Two-step space data migration: extract all data from a Tandoor instance via REST API into a local directory, then import it to any target instance — without needing the source to be online during import.

## Export

```bash
python contrib/api_export_import/export.py \
    --source-url http://source:8080 \
    --source-token tda_xxx \
    --output ./my_export
```

| Flag | Required | Description |
|------|----------|-------------|
| `--source-url` | yes | Source instance base URL |
| `--source-token` | yes | Source API token (Bearer) |
| `--output` | yes | Output directory (created if not exists) |
| `--timeout` | no | HTTP timeout in seconds (default: 60) |
| `-v` | no | Verbose/debug logging |

Produces a directory with one JSON file per model type, a `manifest.json`, and an `images/` subdirectory for recipe images.

## Import

### Validate export (offline, no target needed)

```bash
python contrib/api_export_import/import.py validate ./my_export
```

Checks file structure, JSON validity, and internal FK consistency.

### Dry-run (connects to target, no writes)

```bash
python contrib/api_export_import/import.py dry-run ./my_export \
    --target-url http://target:8080 \
    --target-token tda_yyy
```

### Live import

```bash
python contrib/api_export_import/import.py run ./my_export \
    --target-url http://target:8080 \
    --target-token tda_yyy
```

### Resume after failure

```bash
python contrib/api_export_import/import.py run ./my_export \
    --target-url http://target:8080 \
    --target-token tda_yyy \
    --save-state ./state.json \
    --resume-from-phase 5
```

| Flag | Required | Description |
|------|----------|-------------|
| `--target-url` | yes | Target instance base URL |
| `--target-token` | yes | Target API token (Bearer) |
| `--save-state` | no | Path to save/load ID mapping state for resume |
| `--resume-from-phase` | no | Resume from phase N (requires `--save-state`) |
| `--timeout` | no | HTTP timeout in seconds (default: 60) |
| `-v` | no | Verbose/debug logging |

## Tests

### Without Docker (local Django dev servers, SQLite)

Requires a Python 3.12+ venv with project dependencies installed.

```bash
TANDOOR_VENV=/path/to/venv bash contrib/api_export_import/tests/run_e2e_test.sh
```

This starts 3 Django dev servers (1 source + 2 targets), seeds test data, exports once, and imports to both targets to prove the "extract once, push many" workflow.

### With Docker

Requires Docker with compose v2.

```bash
contrib/api_export_import/tests/run_docker_e2e_test.sh
```

Uses upstream Tandoor 2.6.4 as source and the fork image as target. Add `--keep` to leave containers and export output in place for inspection:

```bash
contrib/api_export_import/tests/run_docker_e2e_test.sh --keep

# Browse source at http://localhost:18080 (admin / admin123)
# Browse target at http://localhost:18081 (admin / admin123)
# Inspect export at contrib/api_export_import/tests/export_output/

# Clean up when done:
docker compose -f contrib/api_export_import/tests/docker-compose.yml down -v
rm -rf contrib/api_export_import/tests/export_output
```
