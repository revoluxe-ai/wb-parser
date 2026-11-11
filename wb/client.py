"""HTTP-клиент: ретраи, паузы, вежливый User-Agent, опционально curl_cffi."""

from __future__ import annotations

import random
import time
from typing import Any, Optional

import requests

try:
    from curl_cffi import requests as cf_requests

    HAS_CURL_CFFI = True
except Exception:  # pragma: no cover
    cf_requests = None
    HAS_CURL_CFFI = False

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)

RETRYABLE = {429, 500, 502, 503, 504}


class WbClient:
    def __init__(
        self,
        delay: float = 0.45,
        timeout: float = 20.0,
        max_retries: int = 4,
        user_agent: Optional[str] = None,
    ) -> None:
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or DEFAULT_UA
        self._last_ts = 0.0
        self.session = requests.Session()
        self._cf_session = None
        if HAS_CURL_CFFI:
            self._cf_session = cf_requests.Session()

    def _headers(self, referer: Optional[str] = None, json_accept: bool = True) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*" if json_accept else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://www.wildberries.ru",
            "Referer": referer or "https://www.wildberries.ru/",
        }
        return headers

    def _sleep(self) -> None:
        if self.delay <= 0:
            return
        wait = self.delay + random.uniform(0, min(0.25, self.delay))
        elapsed = time.monotonic() - self._last_ts
        if elapsed < wait:
            time.sleep(wait - elapsed)

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        *,
        impersonate: bool = False,
        referer: Optional[str] = None,
        json_accept: bool = True,
        allow_status: tuple[int, ...] = (200,),
    ) -> requests.Response:
        """GET с паузой и повторами. impersonate=True пробует TLS-отпечаток Chrome."""
        headers = self._headers(referer=referer, json_accept=json_accept)
        last_exc: Optional[Exception] = None
        last_resp = None

        for attempt in range(self.max_retries + 1):
            self._sleep()
            try:
                if impersonate and self._cf_session is not None:
                    resp = self._cf_session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=self.timeout,
                        impersonate="chrome131",
                        allow_redirects=True,
                    )
                else:
                    resp = self.session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=self.timeout,
                        allow_redirects=True,
                    )
                self._last_ts = time.monotonic()
                last_resp = resp
                if resp.status_code in allow_status:
                    return resp
                if resp.status_code in RETRYABLE or resp.status_code in (403, 498):
                    last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
                    # 403/498 на WBAAS обычно не лечатся паузой, но 429 — да
                    if resp.status_code in (403, 498) and attempt >= 1:
                        return resp
                    time.sleep(min(8, 1.5 ** attempt) + random.uniform(0, 0.4))
                    continue
                return resp
            except (requests.RequestException, OSError) as exc:
                last_exc = exc
                self._last_ts = time.monotonic()
                time.sleep(min(8, 1.5 ** attempt) + random.uniform(0, 0.4))

        if last_resp is not None:
            return last_resp
        raise RuntimeError(f"Не удалось запросить {url}: {last_exc}") from last_exc

    def get_json(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        resp = self.get(url, params=params, **kwargs)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} for {url}")
        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Ответ не JSON: {url}") from exc
