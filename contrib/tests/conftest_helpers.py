"""Shared API client and comparison utilities for E2E migration tests."""

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests


class TandoorAPIClient:
    """HTTP client for Tandoor API with auth, pagination, and retry."""

    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = timeout

    def _url(self, endpoint: str) -> str:
        endpoint = endpoint.lstrip("/")
        return f"{self.base_url}/api/{endpoint}"

    def _request(self, method, url, retries=3, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(retries):
            try:
                resp = getattr(self.session, method)(url, **kwargs)
                return resp
            except requests.ConnectionError:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def get_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages from a paginated endpoint."""
        results = []
        p = dict(params or {})
        p.setdefault("page_size", 200)
        p["page"] = 1
        while True:
            resp = self._request("get", self._url(endpoint), params=p)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            results.extend(data.get("results", []))
            if not data.get("next"):
                break
            p["page"] += 1
        return results

    def get(self, endpoint: str, pk: int) -> dict:
        resp = self._request("get", f"{self._url(endpoint)}{pk}/")
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint: str, data: dict) -> dict:
        resp = self._request("post", self._url(endpoint), json=data)
        if resp.status_code not in (200, 201):
            raise Exception(f"POST {endpoint} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def patch(self, endpoint: str, pk: int, data: dict) -> dict:
        resp = self._request("patch", f"{self._url(endpoint)}{pk}/", json=data)
        if resp.status_code not in (200, 201):
            raise Exception(f"PATCH {endpoint}/{pk} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def put(self, endpoint: str, pk: int, data: dict) -> dict:
        resp = self._request("put", f"{self._url(endpoint)}{pk}/", json=data)
        if resp.status_code not in (200, 201):
            raise Exception(f"PUT {endpoint}/{pk} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def put_image(self, endpoint: str, pk: int, image_bytes: bytes, filename: str) -> dict:
        url = f"{self._url(endpoint)}{pk}/image/"
        headers = {k: v for k, v in self.session.headers.items() if k.lower() != "content-type"}
        resp = self._request(
            "put", url,
            files={"image": (filename, image_bytes, "image/png")},
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise Exception(f"PUT image {endpoint}/{pk} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json()

    def delete(self, endpoint: str, pk: int) -> None:
        resp = self._request("delete", f"{self._url(endpoint)}{pk}/")
        if resp.status_code not in (200, 204):
            raise Exception(f"DELETE {endpoint}/{pk} failed ({resp.status_code}): {resp.text[:500]}")


@dataclass
class VerificationResult:
    model_name: str
    passed: bool = True
    source_count: int = 0
    target_count: int = 0
    matched: int = 0
    mismatches: list[str] = field(default_factory=list)
    missing_on_target: list[str] = field(default_factory=list)
    extra_on_target: list[str] = field(default_factory=list)
    sub_results: dict[str, "VerificationResult"] = field(default_factory=dict)

    def add_mismatch(self, msg: str):
        self.passed = False
        self.mismatches.append(msg)

    def add_missing(self, key: str):
        self.passed = False
        self.missing_on_target.append(key)

    def add_extra(self, key: str):
        self.extra_on_target.append(key)


def match_by_key(source_list: list[dict], target_list: list[dict],
                 key_fn=lambda x: x["name"]) -> tuple[list[tuple], list[dict], list[dict]]:
    """Match objects between source and target by a key function.

    Returns (matched_pairs, missing_on_target, extra_on_target).
    """
    src_by_key = {}
    for obj in source_list:
        k = key_fn(obj)
        src_by_key[k] = obj

    tgt_by_key = {}
    for obj in target_list:
        k = key_fn(obj)
        tgt_by_key[k] = obj

    matched = []
    missing = []
    extra = []

    for k, src_obj in src_by_key.items():
        if k in tgt_by_key:
            matched.append((src_obj, tgt_by_key[k]))
        else:
            missing.append(src_obj)

    for k, tgt_obj in tgt_by_key.items():
        if k not in src_by_key:
            extra.append(tgt_obj)

    return matched, missing, extra


def compare_fields(source: dict, target: dict, fields: list[str], label: str = "") -> list[str]:
    """Compare specific scalar fields between two dicts."""
    mismatches = []
    prefix = f"{label}: " if label else ""
    for f in fields:
        sv = source.get(f)
        tv = target.get(f)
        if isinstance(sv, float) and isinstance(tv, float):
            if abs(sv - tv) > 0.001:
                mismatches.append(f"{prefix}{f}: source={sv!r}, target={tv!r}")
        elif str(sv) != str(tv):
            mismatches.append(f"{prefix}{f}: source={sv!r}, target={tv!r}")
    return mismatches


def compare_nested_name(source_val, target_val, field_name: str, label: str = "") -> list[str]:
    """Compare a nested FK field by .name only."""
    prefix = f"{label}: " if label else ""
    src_name = source_val.get("name") if isinstance(source_val, dict) else source_val
    tgt_name = target_val.get("name") if isinstance(target_val, dict) else target_val
    if src_name is None and tgt_name is None:
        return []
    if src_name != tgt_name:
        return [f"{prefix}{field_name}: source={src_name!r}, target={tgt_name!r}"]
    return []


def sorted_names(obj_list: list[dict] | None) -> list[str]:
    """Extract and sort names from a list of nested objects."""
    if not obj_list:
        return []
    return sorted(o.get("name", "") for o in obj_list)
