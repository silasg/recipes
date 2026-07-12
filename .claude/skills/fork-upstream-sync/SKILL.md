---
name: fork-upstream-sync
description: Bump this fork to a new upstream Tandoor release while preserving the custom "space default_unit" feature, then cut the fork release tag that builds the ghcr image. Use when upstream (TandoorRecipes/recipes) publishes a new version and the fork needs to catch up, or when the user asks to update/rebase/merge the fork onto a newer Tandoor release.
---

# Fork ⇄ Upstream Sync

Keeps `silasg/recipes` in step with upstream `TandoorRecipes/recipes` without losing the fork's
one custom feature: a **`default_unit` field on `Space`**, used in property calculations when an
ingredient has no unit (see `cookbook/helper/property_helper.py`).

## Conventions (do not guess these)

- **Upstream**: `TandoorRecipes/recipes`, releases tagged as plain versions (`2.6.12`).
- **Fork trunk**: branch `fork/default-unit`.
- **Release tag format**: `fork/<upstream-version>/default-unit` (e.g. `fork/2.6.12/default-unit`).
  Pushing such a tag triggers `.github/workflows/build-fork-docker.yml`, which publishes
  `ghcr.io/silasg/recipes-fork-default-unit:<version>` and `:latest` (multi-arch).
- **Current fork version** = the highest existing `fork/*/default-unit` tag.
- **Python 3.12+ is required** to even import the app — upstream uses PEP 701 f-strings
  (nested same-quote in f-strings). 3.11 raises `SyntaxError` in `cookbook/serializer.py`.
  Dockerfile uses 3.13; CI uses 3.12.

## The custom surface (all the fork touches on top of upstream)

Only these files carry fork changes. Everything else is pristine upstream — if a conflict lands
outside this list, upstream refactored something the fork depends on; read carefully.

| File | Fork change |
|---|---|
| `cookbook/models.py` | `default_unit` FK on `Space` (`related_name='space_default_unit'`) |
| `cookbook/helper/property_helper.py` | `_try_calculate_property` helper; falls back to `space.default_unit` when an ingredient has no unit |
| `cookbook/serializer.py` | `UnitSerializer` **hoisted above** `SpaceSerializer`; `SpaceSerializer` gains `default_unit = UnitSerializer(...)` and `'default_unit'` in `fields` |
| `cookbook/migrations/0234a_fork_space_default_unit.py` | the fork migration, inserted between `0234` and `0235` |
| `cookbook/tests/other/test_food_property.py` | 4 `test_no_unit_*` cases |
| `vue3/src/components/model_editors/SpaceEditor.vue` | default-unit picker |
| `vue3/src/openapi/models/Space.ts` | generated `default_unit` field |
| `vue3/src/locales/{en,de}.json` | keys `DefaultUnit`, `DefaultUnitHelp` |
| `.github/workflows/build-fork-docker.yml`, `CLAUDE.md` | fork infra (rarely conflict) |

## Procedure

### 1. Discover
```bash
git remote add upstream https://github.com/TandoorRecipes/recipes.git 2>/dev/null || true
git fetch upstream --tags
LATEST=$(git ls-remote --tags upstream | grep -oE 'refs/tags/[0-9]+\.[0-9]+\.[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1)
CURRENT=$(git tag -l 'fork/*/default-unit' | sed -E 's|fork/(.*)/default-unit|\1|' | sort -V | tail -1)
echo "current=$CURRENT latest=$LATEST"
```
If `LATEST == CURRENT`, stop — nothing to do.

### 2. Merge
```bash
git fetch upstream tag "$LATEST"
git checkout -B sync/$LATEST origin/fork/default-unit
git merge --no-commit --no-ff "$LATEST"
```

### 3. Resolve conflicts (by known pattern)
- **Locale files** (`vue3/src/locales/*.json`) — almost always a whole-file reindent by upstream.
  Take upstream's file, then re-add the fork keys:
  ```bash
  git checkout --theirs vue3/src/locales/de.json vue3/src/locales/en.json
  ```
  Re-insert `DefaultUnit` / `DefaultUnitHelp` (alphabetical, matching upstream's indentation),
  and validate with `python3 -c "import json;json.load(open('vue3/src/locales/de.json'))"`.
- **Migrations** — only conflicts when upstream added migrations that collide with the fork's
  insertion point. If upstream's new migrations chain off `0234`, re-point `0234a`'s
  `dependencies` to sit after them, and re-point the first upstream migration that expected
  `0234` to depend on `0234a`. Goal: one linear chain, `0234a` still present exactly once.
  Verify with step 4's `makemigrations --check`.
- **`serializer.py` / `models.py` / `property_helper.py`** — real overlap. Preserve the table
  above: `default_unit` FK on `Space`, `UnitSerializer` defined before `SpaceSerializer`,
  `default_unit` in the serializer `fields`.

Commit the merge once `git diff --diff-filter=U` is empty.

### 4. Verify (Python 3.12+)
```bash
export DJANGO_SETTINGS_MODULE=recipes.test_settings SECRET_KEY=ci
python manage.py makemigrations --check --dry-run --skip-checks   # chain linear, nothing missing
# (--skip-checks avoids test_settings' admin.E406 system-check noise, unrelated to migrations)
pytest cookbook/tests/other/test_food_property.py -o addopts="" -p no:cacheprovider   # 5 pass
python manage.py spectacular --file /tmp/s.yaml --skip-checks   # then confirm Space has default_unit
grep -A3 'default_unit' /tmp/s.yaml | grep -q Unit && echo "API: default_unit present"
```
Sanity: the fork's only OpenAPI delta vs the raw upstream tag should be a nullable `default_unit`
on `Space` and `PatchedSpace` — nothing else.

### 5. Ship
- Push `sync/$LATEST`, open a PR into `fork/default-unit`. Confirm GitHub reports it mergeable.
- After merge, cut the release (this is what publishes the image):
  ```bash
  git tag fork/$LATEST/default-unit <merge-commit>
  git push origin fork/$LATEST/default-unit
  ```

## Notes
- If several upstream releases accumulated, merge straight to the newest tag — intermediate
  releases come along for free.
- "Lean" release = base the merge on the `fork/<prev>/default-unit` tag lineage (feature + docker
  workflow only). "Full" = base on `fork/default-unit` HEAD (also carries `contrib/` tooling).
- Tag pushes may be blocked in sandboxed/CI sessions; the maintainer runs step 5 locally.
