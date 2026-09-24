"""HTTP with the retries this network path needs - and none a billed request cannot afford."""

from __future__ import annotations

import time
from typing import Any

import requests
from urllib3.exceptions import NewConnectionError

# ------------------------------------------------------------------ http ----

def ensure_ok(response: requests.Response, what: str) -> requests.Response:
    """Raise with the response body attached; a bare status code is useless."""
    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"{what} failed with HTTP {response.status_code}: {body}")
    return response


def never_sent(exc: Exception) -> bool:
    """Whether a failed request provably never reached the server.

    A connect timeout, or a connection refused or unresolvable, fails before a
    byte of the request is written. Anything later - a read timeout, a
    connection dropped mid-response, the "SSL EOF" this network path produces
    - can come after the server has accepted the work and billed it.
    """
    if isinstance(exc, requests.ConnectTimeout):
        return True
    if not isinstance(exc, requests.ConnectionError):
        return False
    reason = exc.args[0] if exc.args else None
    # requests wraps urllib3's MaxRetryError, which carries the real cause.
    return isinstance(getattr(reason, "reason", reason), NewConnectionError)


def request_with_retry(method: str, url: str, *, idempotent: bool = True, **kwargs: Any) -> requests.Response:
    """Send a request, retrying rate limits, server errors and network failures.

    `idempotent=False` is for a request that creates billed work. A timed-out
    POST may already have been accepted upstream, and sending it again pays a
    second time and orphans the first job - so such a request is retried only
    when the failure proves the first attempt never arrived, and otherwise
    fails for --resume to make up. Paying once more for one missing frame is
    the recoverable mistake; paying twice for every slow one is not.
    """
    kwargs.setdefault("timeout", 60)
    for attempt in range(3):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == 2:
                return response
            wait = retry_after_seconds(response) or (attempt + 1) * 3
            print(f"HTTP {response.status_code}; retrying in {wait}s ({attempt + 1}/3)...", flush=True)
            time.sleep(wait)
            continue
        except (requests.Timeout, requests.ConnectionError) as exc:
            if not idempotent and not never_sent(exc):
                raise RuntimeError(
                    "Network request failed after it may have reached the server, so it was not "
                    f"sent again (a second attempt could be billed twice): {exc}"
                ) from exc
            if attempt == 2:
                raise RuntimeError(f"Network request failed after 3 attempts: {exc}") from exc
            print(f"Network timeout; retrying ({attempt + 1}/3)...", flush=True)
            time.sleep((attempt + 1) * 3)
    raise RuntimeError("Request failed")


def retry_after_seconds(response: requests.Response) -> int | None:
    raw = response.headers.get("Retry-After", "").strip()
    if not raw.isdigit():
        return None
    return min(int(raw), 60)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("POST", url, **kwargs)


def get(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("GET", url, **kwargs)


