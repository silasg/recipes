# ZEIT Wochenmarkt → Tandoor Import

Deterministic (no-AI) importer for ZEIT (Magazin) *Wochenmarkt* recipe articles into a
Tandoor Recipes instance. Single-file CLI, stdlib-only logic, all HTTP via `curl`
subprocesses.

## Usage

```bash
python3 zeit_import.py [--dry-run] [--force] [--keep-going] \
    [--tandoor-url URL] [--token-file FILE] URL [URL ...]
```

- `--dry-run` — fetch, extract and parse via Tandoor only; prints the parsed recipe
  (name, servings, ingredient table, step count) and any duplicates. Persists nothing.
- `--force` — import even when Tandoor reports the recipe as a duplicate.
- `--keep-going` — on a per-URL failure, continue with the next URL (exit code is still
  non-zero if any URL failed).

Config resolution (first hit wins):

| setting | CLI flag | env var | fallback (project root) |
|---|---|---|---|
| 1. Tandoor base URL | `--tandoor-url` | `TANDOOR_URL` | `tandoor_url.txt` |
| 2. Tandoor token file | `--token-file` | `TANDOOR_TOKEN_FILE` | `tandoor_token.txt` |
| 3. ZEIT cookie jar | — | `ZEIT_COOKIE_JAR` | `zeit_cookie_jar.txt` |

## ZEIT authentication

Most Wochenmarkt articles are paywalled. The script needs a logged-in browser cookie
export at `<project-root>/zeit_cookies.txt` — a **single line in Cookie-header format**
(`name1=value1; name2=value2; ...`), e.g. copied from the browser dev tools request
headers of a logged-in zeit.de page.

On first run the script bootstraps a Netscape-format cookie jar from it at
`<project-root>/zeit_cookie_jar.txt` (gitignored) and from then on always fetches with
`curl -b jar -c jar`. This matters: ZEIT's short-lived session JWTs expire quickly, but
the long-lived `zeit_sso_201501` SSO token in the jar lets ZEIT re-issue them on each
request — curl writes the refreshed cookies back to the jar. A static `Cookie:` header
dead-ends on the login page once the session JWTs expire.

If you ever get the error *"got the ZEIT login page"*, the SSO token itself has expired:
re-export `zeit_cookies.txt` from a logged-in browser and delete the stale
`zeit_cookie_jar.txt`.

## Why all HTTP goes through curl

The sandbox this was developed in blocks Python `urllib`/sockets against private IPs
(the Tandoor instance) and forces public egress through a proxy. `curl` handles both
cases cleanly:

- Tandoor (private IP): `curl --noproxy '*' ...`
- zeit.de / img.zeit.de (public): plain `curl` (proxy applies where configured; a
  homelab deployment without a proxy is unaffected)

The single `run_curl()` wrapper is also the seam that unit tests monkeypatch, so the
design is kept even where `urllib` would work.

## Pipeline

1. **Fetch** the article through the cookie jar. Login wall (`Bei DIE ZEIT anmelden`
   title) aborts with a clear error; a `data-is-truncated-by-paywall` teaser only warns.
2. **Extract** the `ItemList` JSON-LD (every Wochenmarkt article has one; its
   `itemListElement[].item` entries are `@type: Recipe` with populated
   `recipeIngredient`, but **never** `recipeInstructions`). Instructions are taken from
   the article body: `<p class="paragraph article__item">` elements after the recipe's
   `<h2>`. Multi-recipe articles are split by their respective `<h2>` positions. The h2
   is found by matching the JSON-LD recipe name (some pages lack the
   `article__subheading` class), with a fallback to unused subheading-h2s in document
   order (also covers the generic JSON-LD name "Wochenmarkt", where the name is taken
   from the h2 instead). Credit paragraphs (`Übersetzung:`, `©`, Guardian News & Media)
   are stripped; Unicode dashes in duration ranges are normalized to ASCII (`2–3
   Minuten` → `2-3 Minuten`) because the kitshn iOS auto-timer breaks on them.
3. **Parse via Tandoor** (non-mutating): `POST /api/recipe-from-source/` with the
   recipe dict as `data`. The `url` key is always stripped from the dict first —
   if present, Tandoor re-scrapes the page with a host-specific parser and returns
   empty ingredients. The endpoint returns HTTP 200 with a body-level `error`/`msg` on
   soft failures, and a `duplicates` list (non-empty → skip unless `--force`).
4. **Persist**: keywords are reduced to `import_keyword` entries plus a `ZEIT Magazin`
   keyword (resolved to its id if it exists), `order: null` is replaced with sequential
   ints on steps and ingredients (the API 400s on null order), `source_url` and
   `servings`/`servings_text` are set, then `POST /api/recipe/`.
5. **Image**: the JSON-LD `image.url` is downloaded to a temp file *with a `.jpg`
   extension* (extensionless names are rejected by Tandoor) and `PUT` to
   `/api/recipe/{id}/image/`. Failure warns but doesn't abort.
6. **Ingredient → step mapping**: Tandoor puts all ingredients on the first step.
   Each ingredient is moved to the earliest step whose text mentions its food name
   (case-insensitive; decreasing-length prefixes down to 4 chars for inflections,
   first word of compound names). Every step is PATCHed with its full disjoint
   ingredient list (a step PATCH does not unlink from other steps). Unmatched
   ingredients stay on step 1 and are listed in the log.
7. **Audit** (read-only): per-property `missing value` / `missing conversion` entries
   from `food_properties`, plus per-food/per-unit `numrecipe` and nutrition coverage —
   `numrecipe=1` is flagged as NEW (needs nutrition backfill).

Exit code 0 if everything imported; non-zero otherwise.

## Deployment notes

- Requires only Python 3.10+ and `curl` on `PATH` — no third-party Python packages.
- The script never merges, aliases, edits or deletes foods/units; it only creates a
  recipe (plus whatever foods/units/keyword Tandoor itself creates while persisting).
  Cleanup of new foods (nutrition backfill) remains a manual follow-up, guided by the
  audit report.
- Multi-recipe articles (e.g. 2011/40 Quitte) produce one Tandoor recipe per JSON-LD
  recipe, each with its own body-paragraph slice.
- Verified page-structure assumptions are based on a 41-article sample (2011–2026).
- Some pages ship malformed JSON-LD (stray leading comma in `recipeIngredient`, seen
  live on zeit-magazin/2026/26); a targeted comma repair runs before giving up on a
  block.

## HTTP service

`serve.py` wraps the pipeline in a stdlib-only HTTP service (`python3 serve.py`):

| route | method | auth | behavior |
|---|---|---|---|
| `{BASE_PATH}/import` | POST | `X-Api-Key` | run the pipeline for one URL, return a plain-text report |
| `{BASE_PATH}/health` | GET | none | `200 ok` |
| anything else | — | `X-Api-Key` | 404 |

`POST /import` accepts `application/x-www-form-urlencoded` (`url=...&dry_run=1`) or
`application/json` (`{"url": ..., "dry_run": true}`). Responses (always
`text/plain; charset=utf-8`):

- **200** — import report: recipe id/name, image, ingredient count + step
  distribution, and the full audit (new foods, missing values/conversions). With
  `dry_run` only fetch→extract→parse run and the parsed summary is returned
  (name, servings, ingredient table, step count, duplicates).
- **200** `already in Tandoor as #<id> <name>` — duplicate detected, nothing imported
  (there is deliberately no `--force` over HTTP).
- **400** — missing/invalid `url`, or a non-zeit.de URL (the service is ZEIT-only).
- **502** — pipeline failure with the reason (login wall → re-export cookies; fetch
  or Tandoor errors).

Auth: every route except `/health` requires the `X-Api-Key` header. The secret is
resolved at startup — env `ZEIT_IMPORT_API_KEY` > file named by
`ZEIT_IMPORT_API_KEY_FILE` > `<project-root>/zeit_import_api_key.txt` — and the
service **refuses to start without one** (fail closed).

Env vars: `BIND_ADDR` (default `0.0.0.0`), `PORT` (default `8199`), `BASE_PATH`
(default empty), plus the CLI's `TANDOOR_URL` / `TANDOOR_TOKEN_FILE` /
`ZEIT_COOKIE_JAR`.

`BASE_PATH` semantics: with `BASE_PATH=""` routes live at `/import` and `/health`
(for a proxy that strips the public prefix, e.g. Caddy `handle_path`); with
`BASE_PATH=/zeit` the service itself matches `/zeit/import` (for Caddy `handle`,
which does not strip). Imports are serialized by a global lock (parallel imports
race Tandoor's food get-or-create); `/health` stays lock-free.

## Deployment

`Dockerfile` (build context = this directory) builds a `python:3.12-alpine` + curl
image running `serve.py` as a non-root user. `docker-compose.example.yml` is a
commented reference service to copy into your infra repo: read-only mounts for
`tandoor_token.txt` and `zeit_import_api_key.txt`, and the cookie jar in a
**writable, persistent** mount — ZEIT's session JWTs expire quickly and curl
rewrites the jar with refreshed cookies on every fetch; a read-only or throwaway
jar dead-ends on the login page (see "ZEIT authentication"). The container must
share a docker network with Tandoor and your reverse proxy.

Caddy, prefix-stripping variant (service runs with `BASE_PATH=""`):

```
handle_path /zeit/* {
    reverse_proxy zeit-import:8199
}
```

Alternatively use a plain `handle /zeit/*` block (no stripping) and run the service
with `BASE_PATH=/zeit`.

## iOS Shortcut

`shortcut/tandoor-zeit-import.shortcut` is an unsigned share-sheet shortcut:
VPN on → wait 1 s → POST the shared URL to the import service → show the report →
VPN off.

Install (unsigned shortcuts must be signed before iOS accepts them):

```bash
# on a Mac
shortcuts sign -m anyone -i shortcut/tandoor-zeit-import.shortcut -o /tmp/signed.shortcut
```

AirDrop `/tmp/signed.shortcut` to the iPhone (or transfer any other way) and open
it — Shortcuts offers to add it.

After import, customize:

1. In **Get Contents of URL**: replace `https://CHANGE-ME.example/zeit/import` with
   your public endpoint and set the `X-Api-Key` header value to the contents of
   your `zeit_import_api_key.txt`.
2. In the **two Set VPN actions** (first and last): tap the empty VPN field and
   select your WireGuard tunnel (any VPN configuration registered in iOS Settings
   shows up there). The first action connects, the last disconnects.
3. Enable it in the share sheet: the shortcut already accepts URL input
   (Details → "Show in Share Sheet" should be on). Sharing a zeit.de article from
   Safari then runs the import and shows the report.

## Tests

```bash
cd contrib/zeit_import
python3 -m pytest tests/ -v
```

The local `pytest.ini` keeps the repo-root Django-wired pytest config out of the way.
Fixtures are trimmed real pages (`halloumi.html`, `quitte_multi.html`) plus two
synthetic variants derived from them (`generic_name.html`, `no_subheading.html`).
All HTTP is stubbed by monkeypatching `run_curl`.
