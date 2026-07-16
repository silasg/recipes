#!/usr/bin/env python3
"""
HTTP service wrapper around zeit_import (stdlib only).

Routes (prefixed by $BASE_PATH, default empty):
    POST {BASE_PATH}/import   form (url=...&dry_run=1) or JSON ({"url": ..., "dry_run": true})
    GET  {BASE_PATH}/health   no auth

Auth: X-Api-Key header on everything except /health. Secret resolution:
$ZEIT_IMPORT_API_KEY > $ZEIT_IMPORT_API_KEY_FILE > <project-root>/zeit_import_api_key.txt.
Missing secret refuses to start (fail closed).

Env: BIND_ADDR (0.0.0.0), PORT (8199), BASE_PATH (""), plus the zeit_import
config vars (TANDOOR_URL, TANDOOR_TOKEN_FILE, ZEIT_COOKIE_JAR).

Usage: python3 serve.py
"""

import contextlib
import hmac
import io
import json
import logging
import os
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


class ZeitImportHandler(BaseHTTPRequestHandler):
    api_key: str = ""
    base_path: str = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # replaced by the one-line-per-request log below
        pass

    def _handle(self):
        start = time.monotonic()
        status, body, note = self._route()
        data = body.encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        log.info("%s %s -> %d (%.2fs)%s", self.command, self.path, status, time.monotonic() - start, note)

    do_GET = do_POST = _handle

    def _route(self) -> tuple[int, str, str]:
        path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and path == f"{self.base_path}/health":
            return 200, "ok\n", ""
        supplied = self.headers.get("X-Api-Key") or ""
        if not hmac.compare_digest(supplied.encode(), self.api_key.encode()):
            return 401, "unauthorized\n", ""
        if self.command == "POST" and path == f"{self.base_path}/import":
            return self._handle_import()
        return 404, "not found\n", ""

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

    def _handle_import(self) -> tuple[int, str, str]:
        try:
            params = self._parse_body()
        except ValueError as exc:
            return 400, f"bad request: {exc}\n", ""
        url = str(params.get("url") or "").strip()
        if not url:
            return 400, "missing 'url' parameter\n", ""
        if not is_zeit_url(url):
            return 400, "this service only imports zeit.de (Wochenmarkt) URLs\n", ""
        dry_run = _truthy(params.get("dry_run"))
        try:
            with IMPORT_LOCK:
                text, recipe_ids = execute_import(url, dry_run)
            return 200, text or "done\n", f" url={url} dry_run={dry_run} recipes={recipe_ids}"
        except zeit_import.ZeitImportError as exc:
            return 502, f"import failed: {exc}\n", f" url={url}"
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected error importing %s", url)
            return 502, f"import failed unexpectedly: {exc}\n", f" url={url}"


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
