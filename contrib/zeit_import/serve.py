#!/usr/bin/env python3
"""
HTTP service wrapper around zeit_import (stdlib only).

Routes (prefixed by $BASE_PATH, default empty):
    GET  {BASE_PATH}/         browser: login form (unauthenticated) or recipe picker
    POST {BASE_PATH}/login    form (key=...) -> sets the zeit_import_key cookie
    POST {BASE_PATH}/import   form (url=...&dry_run=1) or JSON ({"url": ..., "dry_run": true})
    GET  {BASE_PATH}/health   no auth

Auth: X-Api-Key header OR zeit_import_key cookie on everything except /health
and the login form. Secret resolution:
$ZEIT_IMPORT_API_KEY > $ZEIT_IMPORT_API_KEY_FILE > <project-root>/zeit_import_api_key.txt.
Missing secret refuses to start (fail closed).

All URLs emitted in HTML are relative (./login, ./import) so the pages work
both behind Caddy handle_path (prefix stripped) and handle (BASE_PATH=/zeit).

Env: BIND_ADDR (0.0.0.0), PORT (8199), BASE_PATH (""), plus the zeit_import
config vars (TANDOOR_URL, TANDOOR_TOKEN_FILE, ZEIT_COOKIE_JAR).

Usage: python3 serve.py
"""

import concurrent.futures
import contextlib
import hmac
import html as html_lib
import http.cookies
import io
import json
import logging
import os
import re
import string
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import zeit_import

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("zeit_serve")

DEFAULT_API_KEY_FILE = os.path.join(zeit_import.PROJECT_ROOT, "zeit_import_api_key.txt")

IMPORT_LOCK = threading.Lock()  # Tandoor food get-or-create races on parallel imports

COOKIE_NAME = "zeit_import_key"
COOKIE_MAX_AGE = 31536000  # one year
WOCHENMARKT_INDEX_URL = "https://www.zeit.de/serie/wochenmarkt"
PICKER_LIMIT = 15
IMPORTED_CACHE_TTL = 600.0  # seconds; the recipe list is re-fetched at most this often


def normalize_base_path(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if raw and not raw.startswith("/"):
        raw = "/" + raw
    return raw


def resolve_api_key() -> str:
    key = (os.environ.get("ZEIT_IMPORT_API_KEY") or "").strip()
    if key:
        return key
    path = os.environ.get("ZEIT_IMPORT_API_KEY_FILE") or DEFAULT_API_KEY_FILE
    if os.path.exists(path):
        key = zeit_import.read_file(path)
        if key:
            return key
    raise RuntimeError("no API key configured - set ZEIT_IMPORT_API_KEY, ZEIT_IMPORT_API_KEY_FILE "
                       f"or create {DEFAULT_API_KEY_FILE} (refusing to start without auth)")


def is_zeit_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and (host == "zeit.de" or host.endswith(".zeit.de"))


def load_settings() -> dict:
    """Resolve Tandoor base/token + cookie jar (env > project-root files), bootstrapping the jar if needed."""
    root = zeit_import.PROJECT_ROOT
    base = os.environ.get("TANDOOR_URL") or zeit_import.read_file(os.path.join(root, "tandoor_url.txt"))
    token_file = os.environ.get("TANDOOR_TOKEN_FILE") or os.path.join(root, "tandoor_token.txt")
    jar = os.environ.get("ZEIT_COOKIE_JAR") or os.path.join(root, "zeit_cookie_jar.txt")
    zeit_import.ensure_cookie_jar(jar, os.path.join(root, "zeit_cookies.txt"))
    return {"base": base.rstrip("/"), "token": zeit_import.read_file(token_file), "jar": jar}


def _step_distribution(recipe_id: int, *, base: str, token: str) -> str:
    _, recipe = zeit_import.tandoor_api("GET", f"/api/recipe/{recipe_id}/", base=base, token=token)
    counts = [len(s.get("ingredients", [])) for s in recipe.get("steps", [])]
    return f"ingredients: {sum(counts)} across {len(counts)} step(s) ({'/'.join(map(str, counts))})\n"


def execute_import(url: str, dry_run: bool) -> tuple[str, list[int]]:
    """Run the zeit_import pipeline for one URL. Returns (report_text, created_recipe_ids)."""
    settings = load_settings()
    base, token, jar = settings["base"], settings["token"], settings["jar"]
    out = io.StringIO()
    html = zeit_import.fetch_zeit_page(url, jar)
    for warning in zeit_import.check_page_access(html):
        out.write(f"WARNING: {warning}\n")
    recipe_ids: list[int] = []
    for ld_recipe in zeit_import.extract_recipes(html, url):
        parsed = zeit_import.parse_via_tandoor(ld_recipe, url, base=base, token=token)
        recipe, duplicates = parsed["recipe"], parsed.get("duplicates") or []
        if dry_run:
            with contextlib.redirect_stdout(out):
                zeit_import.print_parsed_recipe(recipe, duplicates)
            continue
        if duplicates:  # no --force over HTTP
            dup_list = ", ".join(f"#{d.get('id')} {d.get('name')}" for d in duplicates)
            out.write(f"already in Tandoor as {dup_list} - not imported\n")
            continue
        keyword = zeit_import.resolve_zeit_keyword(base=base, token=token)
        payload = zeit_import.transform_recipe(recipe, url, keyword, recipe_yield=ld_recipe.get("recipeYield"))
        recipe_id = zeit_import.persist_recipe(payload, base=base, token=token)
        recipe_ids.append(recipe_id)
        out.write(f"created recipe #{recipe_id} '{payload.get('name')}'\n")
        image_url = zeit_import.image_url_from_ld(ld_recipe)
        image_field = zeit_import.attach_image(recipe_id, image_url, base=base, token=token) if image_url else None
        out.write(f"image: {image_field or 'NOT attached'}\n")
        unmatched = zeit_import.distribute_ingredients(recipe_id, base=base, token=token)
        out.write(_step_distribution(recipe_id, base=base, token=token))
        if unmatched:
            out.write(f"unmatched ingredients (left on step 1): {', '.join(unmatched)}\n")
        out.write(zeit_import.audit_recipe(recipe_id, base=base, token=token) + "\n")
    return out.getvalue(), recipe_ids


# ---------------------------------------------------------------------------
# Wochenmarkt index teasers
# ---------------------------------------------------------------------------

TEASER_RE = re.compile(r'<article class="[^"]*\bwoma-teaser\b[^"]*"[^>]*>.*?</article>', re.S)
TEASER_HREF_RE = re.compile(r'<a\s+[^>]*?href="(https://www\.zeit\.de/[^"]+)"[^>]*class="woma-teaser__(?:faux-)?link"')
TEASER_KICKER_RE = re.compile(r'<span class="woma-teaser__kicker">(.*?)</span>', re.S)
TEASER_TITLE_RE = re.compile(r'<span class="woma-teaser__title">(.*?)</span>', re.S)
TEASER_IMG_RE = re.compile(r'<img class="woma-teaser__media-item"[^>]*?\ssrc="([^"]+)"')


def parse_teasers(html: str) -> list[dict]:
    """Extract the article teasers from the /serie/wochenmarkt index page (newest first, deduped)."""
    entries, seen = [], set()
    for block in TEASER_RE.finditer(html):
        text = block.group(0)
        href = TEASER_HREF_RE.search(text)
        if not href or href.group(1) in seen:
            continue
        kicker = TEASER_KICKER_RE.search(text)
        title = TEASER_TITLE_RE.search(text)
        parts = [zeit_import.strip_tags(m.group(1)) for m in (kicker, title) if m]
        image = TEASER_IMG_RE.search(text)
        seen.add(href.group(1))
        entries.append({"url": href.group(1),
                        "title": ": ".join(p for p in parts if p) or href.group(1),
                        "image": image.group(1) if image else None})
    return entries[:PICKER_LIMIT]


def fetch_picker_entries() -> list[dict]:
    """Fetch + parse the Wochenmarkt series index (public page; the jar is harmless here)."""
    settings = load_settings()
    return parse_teasers(zeit_import.fetch_zeit_page(WOCHENMARKT_INDEX_URL, settings["jar"]))


# ---------------------------------------------------------------------------
# Imported-recipes lookup (Tandoor has no ?source_url= filter and the recipe
# list serializer omits source_url, so: list ids via the ZEIT Magazin keyword,
# fetch each unknown id's detail once ever (id -> source_url is immutable),
# and refresh the id list at most every IMPORTED_CACHE_TTL seconds)
# ---------------------------------------------------------------------------

def normalize_source_url(url: str) -> str:
    """Canonical form for matching: scheme://host/path, no trailing /, /komplettansicht, query or fragment."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith("/komplettansicht"):
        path = path[:-len("/komplettansicht")]
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{path}"


def _fresh_imported_cache() -> dict:
    # -inf, not 0.0: time.monotonic() is seconds-since-boot on Linux, so shortly
    # after boot (CI runners, rebooted hosts) `monotonic() - 0.0` is below the TTL
    # and an empty cache would be served as if it were fresh
    return {"at": float("-inf"), "map": {}, "details": {}, "lock": threading.Lock()}


_IMPORTED_CACHE = _fresh_imported_cache()


def _zeit_keyword_id(*, base: str, token: str) -> int | None:
    query = urllib.parse.quote(zeit_import.ZEIT_KEYWORD_NAME)
    status, body = zeit_import.tandoor_api("GET", f"/api/keyword/?query={query}", base=base, token=token)
    if status != 200:
        raise zeit_import.ZeitImportError(f"keyword lookup returned HTTP {status}")
    for row in body.get("results", []):
        if row.get("name") == zeit_import.ZEIT_KEYWORD_NAME:
            return row["id"]
    return None


def _zeit_recipe_ids(keyword_id: int, *, base: str, token: str) -> list[int]:
    ids, path = [], f"/api/recipe/?keywords={keyword_id}&page_size=200"
    for _ in range(10):  # page cap
        status, body = zeit_import.tandoor_api("GET", path, base=base, token=token)
        if status != 200:
            raise zeit_import.ZeitImportError(f"recipe list returned HTTP {status}")
        ids += [row["id"] for row in body.get("results", []) if row.get("id")]
        next_url = body.get("next")
        if not next_url:
            break
        parts = urllib.parse.urlsplit(next_url)
        path = f"{parts.path}?{parts.query}"
    return ids


def _recipe_source_url(recipe_id: int, *, base: str, token: str) -> str:
    status, body = zeit_import.tandoor_api("GET", f"/api/recipe/{recipe_id}/", base=base, token=token)
    return (body.get("source_url") or "") if status == 200 else ""


def imported_recipes_map() -> dict[str, int]:
    """{normalized source_url: recipe id} for all ZEIT Magazin recipes. Serves stale/empty data on errors."""
    cache = _IMPORTED_CACHE
    with cache["lock"]:
        if time.monotonic() - cache["at"] < IMPORTED_CACHE_TTL:
            return dict(cache["map"])
        try:
            settings = load_settings()
            base, token = settings["base"], settings["token"]
            keyword_id = _zeit_keyword_id(base=base, token=token)
            ids = _zeit_recipe_ids(keyword_id, base=base, token=token) if keyword_id else []
            missing = [rid for rid in ids if rid not in cache["details"]]
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                urls = pool.map(lambda rid: _recipe_source_url(rid, base=base, token=token), missing)
                cache["details"].update(zip(missing, urls))
            cache["map"] = {normalize_source_url(cache["details"][rid]): rid
                            for rid in ids if cache["details"].get(rid)}
            cache["at"] = time.monotonic()
        except zeit_import.ZeitImportError as exc:
            log.warning("imported-recipes lookup failed: %s (serving %d cached entries)", exc, len(cache["map"]))
        return dict(cache["map"])


# ---------------------------------------------------------------------------
# HTML pages (relative URLs only - behind Caddy handle_path the browser path
# has a prefix this app never sees)
# ---------------------------------------------------------------------------

PAGE_STYLE = """
body{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#fafafa;color:#222}
main{max-width:640px;margin:0 auto;padding:16px}
h1{font-size:1.3rem}
ul.entries{list-style:none;padding:0;margin:0 0 12px}
ul.entries li{background:#fff;border:1px solid #ddd;border-radius:8px;margin:6px 0}
ul.entries label{display:flex;align-items:center;gap:10px;padding:8px;min-height:44px}
ul.entries input[type=checkbox]{width:22px;height:22px;flex:none}
.thumb{width:44px;height:44px;border-radius:6px;object-fit:cover;flex:none;background:#eee;display:inline-block}
li.done{opacity:.6}
.badge{color:#2a7;white-space:nowrap}
input[type=url],input[type=password]{width:100%;box-sizing:border-box;padding:10px;font-size:1rem;margin:6px 0;border:1px solid #ccc;border-radius:6px;background:#fff}
button{padding:12px 20px;font-size:1rem;border:0;border-radius:8px;background:#0b62d6;color:#fff;width:100%;margin-top:6px}
button:disabled{opacity:.5}
.error{color:#c00}
pre#log{white-space:pre-wrap;background:#111;color:#ddd;padding:10px;border-radius:8px;font-size:.75rem;min-height:2em}
"""

LOGIN_TEMPLATE = string.Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZEIT import - sign in</title>
<style>$style</style>
</head>
<body><main>
<h1>ZEIT &#8594; Tandoor</h1>
$error
<form method="post" action="./login">
<input type="password" name="key" placeholder="API key" autofocus autocomplete="current-password">
<button type="submit">Sign in</button>
</form>
</main></body></html>
""")

PICKER_TEMPLATE = string.Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZEIT import</title>
<style>$style</style>
</head>
<body><main>
<h1>ZEIT &#8594; Tandoor</h1>
$error
<ul class="entries">
$items
</ul>
<input type="url" id="url" placeholder="or paste a zeit.de URL" inputmode="url" autocapitalize="off">
<button id="go" type="button">Import</button>
<pre id="log"></pre>
<script>
const btn = document.getElementById('go');
btn.addEventListener('click', async () => {
  const urls = Array.from(document.querySelectorAll('input.pick:checked')).map((cb) => cb.value);
  const free = document.getElementById('url').value.trim();
  if (free) urls.push(free);
  if (!urls.length) return;
  const logEl = document.getElementById('log');
  btn.disabled = true;
  for (const url of urls) {
    logEl.textContent += '=== ' + url + '\\n';
    try {
      const resp = await fetch('./import', {method: 'POST', body: new URLSearchParams({url: url}), credentials: 'same-origin'});
      logEl.textContent += await resp.text() + '\\n';
    } catch (err) {
      logEl.textContent += 'ERROR: ' + err + '\\n';
    }
  }
  btn.disabled = false;
});
</script>
</main></body></html>
""")


def _error_html(error: str | None) -> str:
    return f'<p class="error">{html_lib.escape(error)}</p>' if error else ""


def render_login_form(error: str | None = None) -> str:
    return LOGIN_TEMPLATE.substitute(style=PAGE_STYLE, error=_error_html(error))


def render_picker(entries: list[dict], imported: dict[str, int], error: str | None = None) -> str:
    items = []
    for entry in entries:
        title = html_lib.escape(entry["title"])
        thumb = (f'<img class="thumb" src="{html_lib.escape(entry["image"])}" alt="" loading="lazy">'
                 if entry.get("image") else '<span class="thumb"></span>')
        recipe_id = imported.get(normalize_source_url(entry["url"]))
        if recipe_id:
            items.append(f'<li class="done"><label><input type="checkbox" disabled>{thumb}'
                         f'<span>{title} <span class="badge">#{recipe_id} ✓</span></span></label></li>')
        else:
            items.append(f'<li><label><input type="checkbox" class="pick" value="{html_lib.escape(entry["url"])}">'
                         f'{thumb}<span>{title}</span></label></li>')
    return PICKER_TEMPLATE.substitute(style=PAGE_STYLE, error=_error_html(error), items="\n".join(items))


class ZeitImportHandler(BaseHTTPRequestHandler):
    api_key: str = ""
    base_path: str = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # replaced by the one-line-per-request log below
        pass

    def _handle(self):
        start = time.monotonic()
        status, body, note, extra_headers = self._route()
        data = body.encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        headers = {"Content-Type": "text/plain; charset=utf-8", **extra_headers}
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        log.info("%s %s -> %d (%.2fs)%s", self.command, self.path, status, time.monotonic() - start, note)

    do_GET = do_POST = _handle

    def _is_authenticated(self) -> bool:
        supplied = self.headers.get("X-Api-Key") or ""
        if supplied and hmac.compare_digest(supplied.encode(), self.api_key.encode()):
            return True
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return False
        morsel = cookies.get(COOKIE_NAME)
        return morsel is not None and hmac.compare_digest(morsel.value.encode(), self.api_key.encode())

    def _route(self) -> tuple[int, str, str, dict]:
        path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and path == f"{self.base_path}/health":
            return 200, "ok\n", "", {}
        if self.command == "GET" and self.base_path and path == self.base_path:
            # only reachable in the non-stripping (BASE_PATH=/zeit) mode, where the
            # app-visible path equals the browser path - an absolute Location is safe
            return 301, "", "", {"Location": f"{self.base_path}/"}
        authed = self._is_authenticated()
        if self.command == "GET" and path == f"{self.base_path}/":
            return self._handle_root(authed)
        if self.command == "POST" and path == f"{self.base_path}/login":
            return self._handle_login()
        if not authed:
            return 401, "unauthorized\n", "", {}
        if self.command == "POST" and path == f"{self.base_path}/import":
            return self._handle_import()
        return 404, "not found\n", "", {}

    def _handle_root(self, authed: bool) -> tuple[int, str, str, dict]:
        html_headers = {"Content-Type": "text/html; charset=utf-8"}
        if not authed:
            return 200, render_login_form(), " login form", html_headers
        error = None
        try:
            entries = fetch_picker_entries()
        except zeit_import.ZeitImportError as exc:
            entries, error = [], f"could not load the Wochenmarkt index: {exc}"
        imported = imported_recipes_map()
        return 200, render_picker(entries, imported, error=error), f" picker entries={len(entries)}", html_headers

    def _handle_login(self) -> tuple[int, str, str, dict]:
        try:
            params = self._parse_body()
        except ValueError as exc:
            return 400, f"bad request: {exc}\n", "", {}
        key = str(params.get("key") or "")
        if not hmac.compare_digest(key.encode(), self.api_key.encode()):
            return 200, render_login_form(error="wrong key"), " login failed", {"Content-Type": "text/html; charset=utf-8"}
        cookie = f"{COOKIE_NAME}={key}; HttpOnly; SameSite=Lax; Path=/; Max-Age={COOKIE_MAX_AGE}"
        if (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https":
            cookie += "; Secure"
        return 303, "see ./\n", " login ok", {"Location": "./", "Set-Cookie": cookie}

    def _parse_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/json":
            try:
                data = json.loads(raw or "{}")
            except ValueError:
                raise ValueError("invalid JSON body")
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data
        return {key: values[-1] for key, values in urllib.parse.parse_qs(raw).items()}

    def _handle_import(self) -> tuple[int, str, str, dict]:
        try:
            params = self._parse_body()
        except ValueError as exc:
            return 400, f"bad request: {exc}\n", "", {}
        url = str(params.get("url") or "").strip()
        if not url:
            return 400, "missing 'url' parameter\n", "", {}
        if not is_zeit_url(url):
            return 400, "this service only imports zeit.de (Wochenmarkt) URLs\n", "", {}
        dry_run = _truthy(params.get("dry_run"))
        try:
            with IMPORT_LOCK:
                text, recipe_ids = execute_import(url, dry_run)
            return 200, text or "done\n", f" url={url} dry_run={dry_run} recipes={recipe_ids}", {}
        except zeit_import.ZeitImportError as exc:
            return 502, f"import failed: {exc}\n", f" url={url}", {}
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected error importing %s", url)
            return 502, f"import failed unexpectedly: {exc}\n", f" url={url}", {}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def create_server(host: str, port: int, api_key: str, base_path: str = "") -> ThreadingHTTPServer:
    handler = type("BoundZeitImportHandler", (ZeitImportHandler,),
                   {"api_key": api_key, "base_path": normalize_base_path(base_path)})
    return ThreadingHTTPServer((host, port), handler)


def main():
    try:
        api_key = resolve_api_key()
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(2)
    host = os.environ.get("BIND_ADDR", "0.0.0.0")
    port = int(os.environ.get("PORT", "8199"))
    base_path = normalize_base_path(os.environ.get("BASE_PATH", ""))
    server = create_server(host, port, api_key, base_path)
    log.info("zeit-import service on %s:%d, base_path=%r", host, port, base_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
