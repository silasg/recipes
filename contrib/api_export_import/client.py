"""Shared API client for Tandoor export/import tools."""

import time

import requests


class APIError(Exception):
    def __init__(self, action: str, resp: requests.Response):
        self.status_code = resp.status_code
        self.body = resp.text[:500]
        super().__init__(f"{action} failed ({self.status_code}): {self.body}")


class TandoorClient:
    """Minimal REST client for Tandoor with pagination and retry."""

    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = timeout

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/api/{endpoint.strip('/')}/"

    def _req(self, method: str, url: str, retries: int = 3, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        last_exc = None
        for attempt in range(retries):
            try:
                resp = self.s.request(method, url, **kwargs)
                return resp
            except requests.ConnectionError as e:
                last_exc = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise last_exc

    def get_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        results = []
        p = dict(params or {})
        p.setdefault("page_size", 200)
        p["page"] = 1
        while True:
            resp = self._req("GET", self._url(endpoint), params=p)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            results.extend(data.get("results", []))
            if not data.get("next"):
                break
            p["page"] += 1
        return results

    def get_one(self, endpoint: str, pk: int) -> dict:
        resp = self._req("GET", f"{self._url(endpoint)}{pk}/")
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint: str, data: dict) -> dict:
        resp = self._req("POST", self._url(endpoint), json=data)
        if resp.status_code not in (200, 201):
            raise APIError(f"POST {endpoint}", resp)
        return resp.json()

    def patch(self, endpoint: str, pk: int, data: dict) -> dict:
        resp = self._req("PATCH", f"{self._url(endpoint)}{pk}/", json=data)
        if resp.status_code not in (200, 201):
            raise APIError(f"PATCH {endpoint}/{pk}", resp)
        return resp.json()

    def put(self, endpoint: str, pk: int, data: dict) -> dict:
        resp = self._req("PUT", f"{self._url(endpoint)}{pk}/", json=data)
        if resp.status_code not in (200, 201):
            raise APIError(f"PUT {endpoint}/{pk}", resp)
        return resp.json()

    def put_image(self, endpoint: str, pk: int, image_bytes: bytes, filename: str) -> dict:
        url = f"{self._url(endpoint)}{pk}/image/"
        headers = {k: v for k, v in self.s.headers.items() if k.lower() != "content-type"}
        resp = self._req(
            "PUT", url,
            files={"image": (filename, image_bytes, "image/png")},
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise APIError(f"PUT image {endpoint}/{pk}", resp)
        return resp.json()

    def download_image(self, url: str) -> bytes | None:
        if not url:
            return None
        try:
            resp = self._req("GET", url)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
        return None
