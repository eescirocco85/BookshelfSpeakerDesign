"""Small dependency-free HTTP client for the REW API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class RewApiError(RuntimeError):
    """Raised when REW responds with an HTTP error."""


class RewConnectionError(RuntimeError):
    """Raised when the REW API cannot be reached."""


class RewClient:
    def __init__(self, base_url: str = "http://127.0.0.1:4735", timeout: float = 4.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, data: dict[str, Any]) -> Any:
        return self._request("POST", path, data)

    def put(self, path: str, data: dict[str, Any]) -> Any:
        return self._request("PUT", path, data)

    def _request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        url = self.base_url + path
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                if not raw:
                    return None
                text = raw.decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type.lower() or text[:1] in "[{\"tfn-0123456789":
                    return json.loads(text)
                return text
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            message = raw_error or exc.reason or f"HTTP {exc.code}"
            raise RewApiError(f"{method} {path} failed: {message}") from exc
        except Exception as exc:
            raise RewConnectionError(f"Could not reach REW at {url}: {exc}") from exc
