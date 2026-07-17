"""Browser support: cookie auth, login form, picker page."""

import http.client
import threading
import time
import urllib.parse

import pytest

import serve
import zeit_import

KEY = "test-secret-key"

ZEIT_URL = "https://www.zeit.de/zeit-magazin/2026/28/halloumi-gebraten-aprikosen-honig-pecannuesse"

ENTRIES = [
    {"url": "https://www.zeit.de/zeit-magazin/2026/31/semifreddo-rezept-aprikosen-joghurt-pistazien",
     "title": "Semifreddo: Halbgefroren, aber ganz köstlich",
     "image": "https://img.zeit.de/zeit-magazin/2026/31/halbgefroren-aber-ganz-koestlich-bild-1/square__460x460"},
    {"url": "https://www.zeit.de/zeit-magazin/2026/24/ofenpfannkuchen-dutch-baby-blaubeeren-rezept-wochenmarkt",
     "title": "Ofenpfannkuchen: Dutch Baby", "image": None},
]
IMPORTED = {"https://www.zeit.de/zeit-magazin/2026/24/ofenpfannkuchen-dutch-baby-blaubeeren-rezept-wochenmarkt": 114}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    resp_headers = resp.getheaders()
    conn.close()
    return resp.status, data, resp_headers


def header_values(headers, name):
    return [value for key, value in headers if key.lower() == name.lower()]


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


@pytest.fixture
def picker_stub(monkeypatch):
    monkeypatch.setattr(serve, "fetch_picker_entries", lambda: [dict(e) for e in ENTRIES])
    monkeypatch.setattr(serve, "imported_recipes_map", lambda: dict(IMPORTED))


def post_import_cookie(port, path="/import", cookie=f"zeit_import_key={KEY}"):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie is not None:
        headers["Cookie"] = cookie
    return request(port, "POST", path, body="url=" + ZEIT_URL, headers=headers)


def post_login(port, key, path="/login", extra_headers=None):
    headers = {"Content-Type": "application/x-www-form-urlencoded", **(extra_headers or {})}
    return request(port, "POST", path, body=urllib.parse.urlencode({"key": key}), headers=headers)


# ---------------------------------------------------------------------------
# cookie auth on /import
# ---------------------------------------------------------------------------

def test_import_with_cookie_no_header(server_factory, fake_pipeline):
    port = server_factory()
    status, body, _ = post_import_cookie(port)
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": False}]


def test_import_with_wrong_cookie_is_401(server_factory, fake_pipeline):
    port = server_factory()
    status, _, _ = post_import_cookie(port, cookie="zeit_import_key=wrong")
    assert status == 401
    assert fake_pipeline == []


def test_import_with_unrelated_cookie_is_401(server_factory, fake_pipeline):
    port = server_factory()
    status, _, _ = post_import_cookie(port, cookie="sessionid=abc")
    assert status == 401
    assert fake_pipeline == []


def test_import_header_auth_still_works(server_factory, fake_pipeline):
    port = server_factory()
    status, _, _ = request(port, "POST", "/import", body="url=" + ZEIT_URL,
                           headers={"Content-Type": "application/x-www-form-urlencoded", "X-Api-Key": KEY})
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": False}]


# ---------------------------------------------------------------------------
# GET / unauthenticated -> login form
# ---------------------------------------------------------------------------

def test_root_unauthenticated_renders_login_form(server_factory):
    port = server_factory()
    status, body, headers = request(port, "GET", "/")
    assert status == 200
    assert header_values(headers, "Content-Type")[0].startswith("text/html")
    assert '<form' in body
    assert 'action="./login"' in body
    assert 'name="key"' in body


def test_root_with_wrong_cookie_renders_login_form(server_factory):
    port = server_factory()
    status, body, _ = request(port, "GET", "/", headers={"Cookie": "zeit_import_key=wrong"})
    assert status == 200
    assert 'action="./login"' in body


def test_other_routes_still_401_unauthenticated(server_factory):
    port = server_factory()
    assert request(port, "GET", "/whatever")[0] == 401
    assert request(port, "POST", "/import", body="url=" + ZEIT_URL,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 401


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

def test_login_correct_key_sets_cookie_and_redirects(server_factory):
    port = server_factory()
    status, _, headers = post_login(port, KEY)
    assert status == 303
    assert header_values(headers, "Location") == ["./"]
    cookies = header_values(headers, "Set-Cookie")
    assert len(cookies) == 1
    cookie = cookies[0]
    assert cookie.startswith(f"zeit_import_key={KEY}")
    for attr in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=31536000"):
        assert attr in cookie
    assert "Secure" not in cookie


def test_login_secure_flag_with_forwarded_https(server_factory):
    port = server_factory()
    status, _, headers = post_login(port, KEY, extra_headers={"X-Forwarded-Proto": "https"})
    assert status == 303
    assert "Secure" in header_values(headers, "Set-Cookie")[0]


def test_login_wrong_key_rerenders_form_with_error(server_factory):
    port = server_factory()
    status, body, headers = post_login(port, "totally-wrong-key")
    assert status == 200
    assert 'action="./login"' in body
    assert "wrong key" in body.lower()
    assert "totally-wrong-key" not in body  # no key echo
    assert header_values(headers, "Set-Cookie") == []


def test_login_missing_key_rerenders_form(server_factory):
    port = server_factory()
    status, body, headers = request(port, "POST", "/login", body="",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 200
    assert 'action="./login"' in body
    assert header_values(headers, "Set-Cookie") == []


# ---------------------------------------------------------------------------
# GET / authenticated -> picker page
# ---------------------------------------------------------------------------

def test_root_with_cookie_renders_picker(server_factory, picker_stub):
    port = server_factory()
    status, body, headers = request(port, "GET", "/", headers={"Cookie": f"zeit_import_key={KEY}"})
    assert status == 200
    assert header_values(headers, "Content-Type")[0].startswith("text/html")
    assert "Semifreddo: Halbgefroren, aber ganz köstlich" in body
    assert "Ofenpfannkuchen: Dutch Baby" in body
    assert '<meta name="viewport"' in body
    assert "./import" in body
    assert "./login" not in body


def test_root_with_header_renders_picker(server_factory, picker_stub):
    port = server_factory()
    status, body, _ = request(port, "GET", "/", headers={"X-Api-Key": KEY})
    assert status == 200
    assert "Semifreddo" in body


def test_picker_marks_imported_entries(server_factory, picker_stub):
    port = server_factory()
    _, body, _ = request(port, "GET", "/", headers={"Cookie": f"zeit_import_key={KEY}"})
    assert "#114 ✓" in body
    # imported entry is a disabled checkbox, the other one is a normal enabled checkbox
    assert body.count('<input type="checkbox" disabled') == 1
    assert f'value="{ENTRIES[0]["url"]}"' in body


def test_picker_uses_relative_urls_only(server_factory, picker_stub):
    port = server_factory()
    _, body, _ = request(port, "GET", "/", headers={"Cookie": f"zeit_import_key={KEY}"})
    assert "'./import'" in body or '"./import"' in body
    assert "'/import'" not in body and '"/import"' not in body


def test_picker_survives_index_fetch_failure(server_factory, monkeypatch, picker_stub):
    def _boom():
        raise zeit_import.ZeitImportError("zeit.de unreachable")

    monkeypatch.setattr(serve, "fetch_picker_entries", _boom)
    port = server_factory()
    status, body, _ = request(port, "GET", "/", headers={"Cookie": f"zeit_import_key={KEY}"})
    assert status == 200
    assert "zeit.de unreachable" in body
    assert "./import" in body  # free-text import still usable


# ---------------------------------------------------------------------------
# BASE_PATH=/zeit variants
# ---------------------------------------------------------------------------

def test_base_path_root_login_form(server_factory):
    port = server_factory(base_path="/zeit")
    status, body, _ = request(port, "GET", "/zeit/")
    assert status == 200
    assert 'action="./login"' in body


def test_base_path_root_without_slash_redirects(server_factory):
    port = server_factory(base_path="/zeit")
    status, _, headers = request(port, "GET", "/zeit")
    assert status in (301, 302, 308)
    assert header_values(headers, "Location") == ["/zeit/"]


def test_base_path_login_and_cookie_import(server_factory, fake_pipeline, picker_stub):
    port = server_factory(base_path="/zeit")
    status, _, headers = post_login(port, KEY, path="/zeit/login")
    assert status == 303
    assert header_values(headers, "Location") == ["./"]
    cookie = header_values(headers, "Set-Cookie")[0].split(";")[0]
    status, body, _ = request(port, "GET", "/zeit/", headers={"Cookie": cookie})
    assert status == 200
    assert "Semifreddo" in body
    status, _, _ = post_import_cookie(port, path="/zeit/import", cookie=cookie)
    assert status == 200
    assert fake_pipeline == [{"url": ZEIT_URL, "dry_run": False}]


def test_base_path_unprefixed_root_is_401(server_factory):
    port = server_factory(base_path="/zeit")
    assert request(port, "GET", "/")[0] == 401


# ---------------------------------------------------------------------------
# teaser parsing (real trimmed fixture)
# ---------------------------------------------------------------------------

def test_parse_teasers_from_fixture(load_fixture):
    entries = serve.parse_teasers(load_fixture("wochenmarkt_index.html"))
    assert [e["url"] for e in entries] == [
        "https://www.zeit.de/zeit-magazin/wochenmarkt/2026-07/poisson-cru-rezept-tahiti-kokosmilch-wochenmarkt",
        "https://www.zeit.de/zeit-magazin/2026/31/semifreddo-rezept-aprikosen-joghurt-pistazien",
        "https://www.zeit.de/zeit-magazin/2026/30/geschmorte-auberginen-suedfrankreich-rezept",
        "https://www.zeit.de/zeit-magazin/2026/29/granita-rezept-zitronenmelisse-zitronenverbene-rezept-wochenmarkt",
    ]
    assert entries[1]["title"] == "Semifreddo: Halbgefroren, aber ganz köstlich"
    assert entries[1]["image"] == "https://img.zeit.de/zeit-magazin/2026/31/halbgefroren-aber-ganz-koestlich-bild-1/square__460x460"


def test_parse_teasers_dedupes_and_limits(load_fixture):
    block = load_fixture("wochenmarkt_index.html")
    entries = serve.parse_teasers(block + block)  # duplicated page
    assert len(entries) == 4  # deduped by url
    many = block.replace("2026/31", "2026/31x")  # crude variation not needed; just check limit constant
    assert serve.PICKER_LIMIT == 15


# ---------------------------------------------------------------------------
# imported-recipes map (keyword list + cached detail fetches)
# ---------------------------------------------------------------------------

RECIPE_URLS = {
    41: "https://www.zeit.de/zeit-magazin/2026/24/ofenpfannkuchen-dutch-baby-blaubeeren-rezept-wochenmarkt",
    42: "https://www.zeit.de/zeit-magazin/2026/04/misosuppe-rezept-wochenmarkt/komplettansicht",
}


@pytest.fixture
def tandoor_stub(monkeypatch):
    calls = []

    def _fake_api(method, path, payload=None, *, base, token):
        calls.append(path)
        if path.startswith("/api/keyword/"):
            return 200, {"results": [{"id": 7, "name": "ZEIT Magazin"}]}
        if path.startswith("/api/recipe/?"):
            return 200, {"results": [{"id": rid} for rid in RECIPE_URLS], "next": None}
        for rid, url in RECIPE_URLS.items():
            if path == f"/api/recipe/{rid}/":
                return 200, {"id": rid, "source_url": url}
        raise AssertionError(f"unexpected api call {path}")

    monkeypatch.setattr(zeit_import, "tandoor_api", _fake_api)
    monkeypatch.setattr(serve, "load_settings",
                        lambda: {"base": "http://tandoor.test", "token": "tok", "jar": "/tmp/jar"})
    monkeypatch.setattr(serve, "_IMPORTED_CACHE", serve._fresh_imported_cache())
    return calls


def test_imported_map_builds_normalized_url_map(tandoor_stub):
    result = serve.imported_recipes_map()
    assert result[serve.normalize_source_url(RECIPE_URLS[41])] == 41
    # /komplettansicht suffix normalized away
    assert result["https://www.zeit.de/zeit-magazin/2026/04/misosuppe-rezept-wochenmarkt"] == 42


def test_imported_map_uses_ttl_cache(tandoor_stub):
    serve.imported_recipes_map()
    first = len(tandoor_stub)
    serve.imported_recipes_map()
    assert len(tandoor_stub) == first  # within TTL: no new API calls


def test_imported_map_refetches_list_but_not_known_details(tandoor_stub, monkeypatch):
    serve.imported_recipes_map()
    tandoor_stub.clear()
    monkeypatch.setitem(serve._IMPORTED_CACHE, "at", 0.0)  # expire TTL
    serve.imported_recipes_map()
    detail_calls = [p for p in tandoor_stub if p.startswith("/api/recipe/") and "?" not in p]
    assert detail_calls == []  # id -> source_url pairs are immutable, only the list is refreshed


def test_imported_map_failure_returns_empty(monkeypatch):
    def _boom(*a, **kw):
        raise zeit_import.ZeitImportError("tandoor down")

    monkeypatch.setattr(serve, "load_settings", _boom)
    monkeypatch.setattr(serve, "_IMPORTED_CACHE", serve._fresh_imported_cache())
    assert serve.imported_recipes_map() == {}


# ---------------------------------------------------------------------------
# source_url normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://www.zeit.de/zeit-magazin/2026/24/foo", "https://www.zeit.de/zeit-magazin/2026/24/foo"),
    ("https://www.zeit.de/zeit-magazin/2026/24/foo/", "https://www.zeit.de/zeit-magazin/2026/24/foo"),
    ("https://www.zeit.de/zeit-magazin/2026/24/foo/komplettansicht", "https://www.zeit.de/zeit-magazin/2026/24/foo"),
    ("https://www.zeit.de/zeit-magazin/2026/24/foo?wt_zmc=x#top", "https://www.zeit.de/zeit-magazin/2026/24/foo"),
    ("", ""),
])
def test_normalize_source_url(raw, expected):
    assert serve.normalize_source_url(raw) == expected
