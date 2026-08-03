"""
human.py — Human-like HTTP session wrapper.
No external deps beyond aiohttp (no fake_useragent needed).
"""

import asyncio
import json as _json
import random
import time
from typing import Optional

import aiohttp


# ── realistic browser profiles ────────────────────────────────────────────────
_HEADER_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Google Chrome";v="130", "Chromium";v="130", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
    },
]


class HumanResponse:
    """Thin wrapper so callers can await .text() / .json() / .read()."""

    def __init__(self, status: int, headers: dict, body: bytes, url: str):
        self.status = status
        self.headers = headers
        self._body = body
        self.url = url

    async def text(self) -> str:
        return self._body.decode("utf-8", errors="ignore")

    async def json(self):
        return _json.loads(self._body.decode("utf-8", errors="ignore"))

    async def read(self) -> bytes:
        return self._body


class HumanSession:
    """
    Drop-in wrapper around aiohttp that adds human-like request behaviour:
    realistic headers, Sec-Fetch chain, referrer, random delays, persistent cookies.
    """

    def __init__(self, base_url: str = ""):
        self.base_url = base_url.rstrip("/")
        self.jar = aiohttp.CookieJar(unsafe=True)
        self._profile = random.choice(_HEADER_PROFILES)
        self._referer: Optional[str] = None
        self._session_start = time.time()
        self._request_count = 0
        # single persistent aiohttp session (keeps cookies, TCP connections)
        self._session = aiohttp.ClientSession(cookie_jar=self.jar)

    async def close(self):
        await self._session.close()

    # ── header builders ───────────────────────────────────────────────────────

    def _nav_headers(self, extra: Optional[dict] = None) -> dict:
        """Headers for a browser page-navigation GET."""
        h = {
            **self._profile,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
        }
        if self._referer:
            h["Referer"] = self._referer
            h["Sec-Fetch-Site"] = "same-origin"
        else:
            h["Sec-Fetch-Site"] = "none"
        if extra:
            h.update(extra)
        return h

    def _api_headers(self, origin: str, extra: Optional[dict] = None) -> dict:
        """Headers for an XHR / fetch API call."""
        h = {
            **self._profile,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": origin,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if self._referer:
            h["Referer"] = self._referer
        if extra:
            h.update(extra)
        return h

    def _cross_origin_api_headers(self, origin: str, extra: Optional[dict] = None) -> dict:
        """Headers for a cross-origin fetch (e.g. app.synthesia.io → login.synthesia.io)."""
        h = self._api_headers(origin, extra)
        h["Sec-Fetch-Site"] = "same-site"
        return h

    # ── delay helpers ─────────────────────────────────────────────────────────

    async def _think(self, lo: float = 0.8, hi: float = 2.5):
        """Simulate human reading / thinking time."""
        delay = random.uniform(lo, hi)
        if random.random() < 0.12:  # occasional longer pause
            delay += random.uniform(1.5, 4.0)
        await asyncio.sleep(delay)

    async def _type(self, text: str):
        """Simulate typing a field (0.06–0.14 s per char, min 0.4 s)."""
        delay = max(0.4, len(text) * random.uniform(0.06, 0.14))
        await asyncio.sleep(delay)

    async def _click(self):
        """Micro-delay for a mouse click."""
        await asyncio.sleep(random.uniform(0.15, 0.45))

    # ── request helpers ───────────────────────────────────────────────────────

    async def _raw_get(self, url: str, headers: dict, **kw) -> HumanResponse:
        await asyncio.sleep(random.uniform(0.05, 0.25))  # network jitter
        async with self._session.get(url, headers=headers, allow_redirects=True, **kw) as r:
            body = await r.read()
            self._referer = url
            self._request_count += 1
        return HumanResponse(r.status, dict(r.headers), body, str(r.url))

    async def _raw_post(self, url: str, headers: dict, payload=None, data=None, **kw) -> HumanResponse:
        await asyncio.sleep(random.uniform(0.05, 0.25))
        async with self._session.post(
            url, json=payload, data=data, headers=headers, allow_redirects=True, **kw
        ) as r:
            body = await r.read()
            self._request_count += 1
        return HumanResponse(r.status, dict(r.headers), body, str(r.url))

    # ── public API ────────────────────────────────────────────────────────────

    async def browse(self, url: str) -> HumanResponse:
        """Navigate to a page like a human (includes reading delay)."""
        await asyncio.sleep(random.uniform(0.4, 1.2))   # URL-bar typing
        resp = await self._raw_get(url, self._nav_headers(), timeout=aiohttp.ClientTimeout(total=30))
        await self._think(1.5, 4.0)   # read the landing page
        return resp

    async def navigate(self, url: str) -> HumanResponse:
        """Follow a link (quicker than initial browse)."""
        await self._click()
        resp = await self._raw_get(url, self._nav_headers(), timeout=aiohttp.ClientTimeout(total=30))
        await self._think(0.8, 2.5)
        return resp

    async def api_get(self, url: str, origin: str, extra: Optional[dict] = None, **kw) -> HumanResponse:
        """XHR/fetch GET (same-origin)."""
        return await self._raw_get(url, self._api_headers(origin, extra), **kw)

    async def api_post(
        self,
        url: str,
        origin: str,
        payload: dict,
        extra: Optional[dict] = None,
        cross_origin: bool = False,
        type_text: Optional[str] = None,
        **kw,
    ) -> HumanResponse:
        """XHR/fetch POST with human typing delay."""
        if type_text is not None:
            await self._type(type_text)
        elif payload:
            await self._type(_json.dumps(payload))
        await self._click()   # "click submit"
        headers = (
            self._cross_origin_api_headers(origin, extra)
            if cross_origin
            else self._api_headers(origin, extra)
        )
        return await self._raw_post(url, headers, payload=payload, **kw)

    async def form_post(
        self,
        url: str,
        data: dict,
        extra: Optional[dict] = None,
        **kw,
    ) -> HumanResponse:
        """HTML form POST."""
        for v in data.values():
            await self._type(str(v))
            await asyncio.sleep(random.uniform(0.1, 0.3))
        await self._click()
        h = {
            **self._profile,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        if self._referer:
            h["Referer"] = self._referer
        if extra:
            h.update(extra)
        return await self._raw_post(url, h, data=data, **kw)

    def stats(self) -> dict:
        return {
            "requests": self._request_count,
            "uptime_s": round(time.time() - self._session_start, 1),
            "ua": self._profile["User-Agent"][:60],
        }
