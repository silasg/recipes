import http.client
import json
import threading

import pytest

import serve
import zeit_import

KEY = "test-secret-key"

ZEIT_URL = "https://www.zeit.de/zeit-magazin/2026/28/halloumi-gebraten-aprikosen-honig-pecannuesse"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()
    return resp.status, data


@pytest.fixture
def server_factory():
    servers = []

    def _make(api_key=KEY, base_path=""):
        srv = serve.create_server("127.0.0.1", 0, api_key=api_key, base_path=base_path)
        thread = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        servers.append(srv)
        return srv.server_address[1]

    yield _make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def fake_pipeline(monkeypatch):
    calls = []

    def _fake(url, dry_run):
        calls.append({"url": url, "dry_run": dry_run})
        return "created recipe #123 'Testrezept'\n", [123]

    monkeypatch.setattr(serve, "execute_import", _fake)
    return calls


def post_import(port, path="/import", body="url=" + ZEIT_URL, key=KEY,
                content_type="application/x-www-form-urlencoded"):
    headers = {"Content-Type": content_type}
    if key is not None:
        headers["X-Api-Key"] = key
    return request(port, "POST", path, body=body, headers=headers)


# ---------------------------------------------------------------------------
# base path normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("/", ""),
    ("/zeit", "/zeit"),
    ("zeit", "/zeit"),
    ("/zeit/", "/zeit"),
])
def test_normalize_base_path(raw, expected):
    assert serve.normalize_base_path(raw) == expected


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_health_needs_no_key(server_factory):
    port = server_factory()
    status, body = request(port, "GET", "/health")
    assert status == 200
    assert body.strip() == "ok"


def test_import_without_key_is_401(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, key=None)
    assert status == 401
    assert fake_pipeline == []


def test_import_with_wrong_key_is_401(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, key="wrong")
    assert status == 401
    assert fake_pipeline == []


def test_unknown_route_requires_key(server_factory):
    port = server_factory()
    status, _ = request(port, "GET", "/whatever")
    assert status == 401


# ---------------------------------------------------------------------------
# base path matching
# ---------------------------------------------------------------------------

def test_base_path_prefixed_routes(server_factory, fake_pipeline):
    port = server_factory(base_path="/zeit")
    assert request(port, "GET", "/zeit/health")[0] == 200
    assert post_import(port, path="/zeit/import")[0] == 200


def test_base_path_rejects_unprefixed(server_factory, fake_pipeline):
    port = server_factory(base_path="/zeit")
    assert request(port, "GET", "/health", headers={"X-Api-Key": KEY})[0] == 404
    assert post_import(port, path="/import")[0] == 404


def test_unknown_route_is_404(server_factory):
    port = server_factory()
    status, _ = request(port, "GET", "/nope", headers={"X-Api-Key": KEY})
    assert status == 404


# ---------------------------------------------------------------------------
# request body handling
# ---------------------------------------------------------------------------

def test_form_body_import(server_factory, fake_pipeline):
    port = server_factory()
    status, body = post_import(port)
    assert status == 200
    assert "created recipe #123" in body
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": False}]


def test_json_body_import(server_factory, fake_pipeline):
    port = server_factory()
    status, body = post_import(port, body=json.dumps({"url": ZEIT_URL}),
                               content_type="application/json")
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": False}]


def test_form_dry_run_flag(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, body=f"url={ZEIT_URL}&dry_run=1")
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": True}]


def test_json_dry_run_flag(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, body=json.dumps({"url": ZEIT_URL, "dry_run": True}),
                            content_type="application/json")
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": True}]


def test_missing_url_is_400(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, body="dry_run=1")
    assert status == 400
    assert fake_pipeline == []


def test_invalid_json_is_400(server_factory, fake_pipeline):
    port = server_factory()
    status, _ = post_import(port, body="{not json", content_type="application/json")
    assert status == 400
    assert fake_pipeline == []


def test_non_zeit_url_is_400(server_factory, fake_pipeline):
    port = server_factory()
    status, body = post_import(port, body="url=https://www.chefkoch.de/rezepte/123")
    assert status == 400
    assert "zeit.de" in body.lower()
    assert fake_pipeline == []


def test_pipeline_error_maps_to_502(server_factory, monkeypatch):
    def _boom(url, dry_run):
        raise zeit_import.LoginWallError("session cookies are dead; re-export zeit_cookies.txt")

    monkeypatch.setattr(serve, "execute_import", _boom)
    port = server_factory()
    status, body = post_import(port)
    assert status == 502
    assert "re-export" in body


# ---------------------------------------------------------------------------
# execute_import (pipeline orchestration, stages monkeypatched)
# ---------------------------------------------------------------------------

@pytest.fixture
def stubbed_stages(monkeypatch):
    monkeypatch.setattr(serve, "load_settings",
                        lambda: {"base": "http://tandoor.test", "token": "tok", "jar": "/tmp/jar"})
    monkeypatch.setattr(zeit_import, "fetch_zeit_page", lambda url, jar: "<html/>")
    monkeypatch.setattr(zeit_import, "check_page_access", lambda html: [])
    monkeypatch.setattr(zeit_import, "extract_recipes",
                        lambda html, url: [{"name": "Tomatensuppe", "recipeYield": "4 Portionen"}])


def test_execute_import_duplicate_short_circuit(stubbed_stages, monkeypatch):
    monkeypatch.setattr(zeit_import, "parse_via_tandoor",
                        lambda ld, url, base, token: {"recipe": {"name": "Tomatensuppe"},
                                                      "duplicates": [{"id": 113, "name": "Tomatensuppe"}]})

    def _no_persist(*a, **kw):
        raise AssertionError("persist_recipe must not be called for duplicates")

    monkeypatch.setattr(zeit_import, "persist_recipe", _no_persist)
    text, ids = serve.execute_import(ZEIT_URL, dry_run=False)
    assert "already in Tandoor as #113 Tomatensuppe" in text
    assert ids == []


def test_execute_import_dry_run_renders_parse_summary(stubbed_stages, monkeypatch):
    recipe = {"name": "Tomatensuppe", "servings": 4, "servings_text": "Portionen",
              "working_time": 20, "waiting_time": 0,
              "steps": [{"ingredients": [{"amount": 1, "unit": {"name": "kg"},
                                          "food": {"name": "Tomaten"}, "note": ""}]}]}
    monkeypatch.setattr(zeit_import, "parse_via_tandoor",
                        lambda ld, url, base, token: {"recipe": recipe, "duplicates": []})

    def _no_persist(*a, **kw):
        raise AssertionError("persist_recipe must not be called on dry_run")

    monkeypatch.setattr(zeit_import, "persist_recipe", _no_persist)
    text, ids = serve.execute_import(ZEIT_URL, dry_run=True)
    assert "Tomatensuppe" in text
    assert "Tomaten" in text
    assert ids == []


# ---------------------------------------------------------------------------
# url validation + api key resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://www.zeit.de/zeit-magazin/2026/28/foo", True),
    ("http://zeit.de/foo", True),
    ("https://evil.example/?www.zeit.de", False),
    ("https://notzeit.de/foo", False),
    ("ftp://www.zeit.de/foo", False),
    ("not a url", False),
])
def test_is_zeit_url(url, ok):
    assert serve.is_zeit_url(url) is ok


def test_resolve_api_key_env_wins(monkeypatch, tmp_path):
    keyfile = tmp_path / "key.txt"
    keyfile.write_text("from-file\n")
    monkeypatch.setenv("ZEIT_IMPORT_API_KEY", "from-env")
    monkeypatch.setenv("ZEIT_IMPORT_API_KEY_FILE", str(keyfile))
    assert serve.resolve_api_key() == "from-env"


def test_resolve_api_key_file_env(monkeypatch, tmp_path):
    keyfile = tmp_path / "key.txt"
    keyfile.write_text("from-file\n")
    monkeypatch.delenv("ZEIT_IMPORT_API_KEY", raising=False)
    monkeypatch.setenv("ZEIT_IMPORT_API_KEY_FILE", str(keyfile))
    assert serve.resolve_api_key() == "from-file"


def test_resolve_api_key_missing_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("ZEIT_IMPORT_API_KEY", raising=False)
    monkeypatch.delenv("ZEIT_IMPORT_API_KEY_FILE", raising=False)
    monkeypatch.setattr(serve, "DEFAULT_API_KEY_FILE", str(tmp_path / "does-not-exist.txt"))
    with pytest.raises(RuntimeError):
        serve.resolve_api_key()
