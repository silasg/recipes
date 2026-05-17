"""Shared API client for Tandoor export/import tools."""

import time

import requests


_IMAGE_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # WEBP starts with RIFF....WEBP; RIFF prefix is enough here
]


def _sniff_image_content_type(data: bytes) -> str | None:
    """Return the MIME type implied by an image's magic bytes, or None."""
    for magic, ct in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ct
    return None


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

    def put_image(self, endpoint: str, pk: int, image_bytes: bytes, filename: str,
                  content_type: str | None = None) -> dict:
        url = f"{self._url(endpoint)}{pk}/image/"
        # Sniff content type from the actual bytes when not provided, so the
        # server stores the file with the correct extension and PIL re-encodes
        # to the correct format (avoids JPEG → PNG transcoding bloat).
        ct = content_type or _sniff_image_content_type(image_bytes) or "application/octet-stream"
        # Temporarily remove the session-level Content-Type header so that
        # requests can set the correct multipart/form-data boundary.
        # Session.request merges (not replaces) headers, so passing a custom
        # headers dict without Content-Type is not enough — the session's
        # default "application/json" still leaks through.
        saved_ct = self.s.headers.pop("Content-Type", None)
        try:
            resp = self._req(
                "PUT", url,
                files={"image": (filename, image_bytes, ct)},
            )
        finally:
            if saved_ct is not None:
                self.s.headers["Content-Type"] = saved_ct
        if resp.status_code not in (200, 201):
            raise APIError(f"PUT image {endpoint}/{pk}", resp)
        return resp.json()

    def download_image(self, url: str) -> bytes | None:
        """Download an image from an arbitrary URL.

        Uses a bare ``requests.get`` instead of the authenticated session: the
        session sets ``Authorization: Bearer ...`` and ``Content-Type:
        application/json`` on every request, which breaks S3 presigned URLs
        (S3 returns HTTP 400 when an Authorization header is present alongside
        the signature in the query string).
        """
        if not url:
            return None
        last_exc = None
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.content
                return None
            except requests.ConnectionError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if last_exc is not None:
            return None
        return None
