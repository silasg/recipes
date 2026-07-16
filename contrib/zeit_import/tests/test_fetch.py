"""Tests for cookie-jar bootstrap, page access checks and the curl wrapper plumbing."""
import json

import pytest

import zeit_import as zi


class TestNetscapeJar:
    def test_bootstrap_from_cookie_header(self):
        jar = zi.build_netscape_jar("zeit_sso_201501=abc123; foo=bar=baz")
        lines = jar.strip().splitlines()
        assert lines[0].startswith("# Netscape HTTP Cookie File")
        assert ".zeit.de\tTRUE\t/\tTRUE\t2000000000\tzeit_sso_201501\tabc123" in lines
        # values containing '=' must survive (split on first '=' only)
        assert ".zeit.de\tTRUE\t/\tTRUE\t2000000000\tfoo\tbar=baz" in lines

    def test_ensure_jar_creates_file_from_cookie_export(self, tmp_path):
        cookies = tmp_path / "zeit_cookies.txt"
        cookies.write_text("a=1; b=2\n")
        jar = tmp_path / "zeit_cookie_jar.txt"
        zi.ensure_cookie_jar(str(jar), str(cookies))
        assert jar.exists()
        assert "\ta\t1" in jar.read_text()

    def test_ensure_jar_keeps_existing(self, tmp_path):
        jar = tmp_path / "jar.txt"
        jar.write_text("EXISTING")
        zi.ensure_cookie_jar(str(jar), str(tmp_path / "missing_cookies.txt"))
        assert jar.read_text() == "EXISTING"

    def test_ensure_jar_missing_both_raises(self, tmp_path):
        with pytest.raises(zi.ZeitImportError):
            zi.ensure_cookie_jar(str(tmp_path / "jar.txt"), str(tmp_path / "cookies.txt"))


class TestPageAccessChecks:
    def test_login_wall_raises(self):
        html = "<html><head><title>Bei DIE ZEIT anmelden</title></head><body></body></html>"
        with pytest.raises(zi.LoginWallError):
            zi.check_page_access(html)

    def test_paywall_teaser_warns(self):
        html = '<html><body><div data-is-truncated-by-paywall="true"></div></body></html>'
        warnings = zi.check_page_access(html)
        assert any("paywall" in w.lower() for w in warnings)

    def test_full_page_ok(self, load_fixture):
        assert zi.check_page_access(load_fixture("halloumi.html")) == []


class TestCurlPlumbing:
    def test_tandoor_api_goes_through_run_curl_with_noproxy(self, monkeypatch):
        captured = {}

        def fake_run_curl(args):
            captured["args"] = args
            return 200, json.dumps({"ok": True}).encode()

        monkeypatch.setattr(zi, "run_curl", fake_run_curl)
        status, body = zi.tandoor_api("GET", "/api/recipe/1/", base="http://10.0.0.1:8080", token="tok")
        assert status == 200
        assert body == {"ok": True}
        assert "--noproxy" in captured["args"]
        assert "http://10.0.0.1:8080/api/recipe/1/" in captured["args"]
        auth = captured["args"][captured["args"].index("Authorization: Bearer tok")]
        assert auth  # bearer header present

    def test_tandoor_api_post_sends_json(self, monkeypatch):
        captured = {}

        def fake_run_curl(args):
            captured["args"] = args
            return 201, b'{"id": 7}'

        monkeypatch.setattr(zi, "run_curl", fake_run_curl)
        status, body = zi.tandoor_api("POST", "/api/recipe/", payload={"name": "x"}, base="http://10.0.0.1:8080", token="tok")
        assert status == 201
        assert body == {"id": 7}
        assert "POST" in captured["args"]

    def test_fetch_zeit_page_uses_cookie_jar_no_noproxy(self, monkeypatch, tmp_path, load_fixture):
        jar = tmp_path / "jar.txt"
        jar.write_text("# Netscape HTTP Cookie File\n")
        html = load_fixture("halloumi.html")
        captured = {}

        def fake_run_curl(args):
            captured["args"] = args
            return 200, html.encode()

        monkeypatch.setattr(zi, "run_curl", fake_run_curl)
        result = zi.fetch_zeit_page("https://www.zeit.de/foo", str(jar))
        assert result == html
        assert "--noproxy" not in captured["args"]
        assert "-b" in captured["args"] and "-c" in captured["args"]
        ua = [a for a in captured["args"] if "Chrome" in a]
        assert ua, "desktop Chrome UA expected"
