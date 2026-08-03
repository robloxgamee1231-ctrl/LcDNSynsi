"""
synthesia_api.py — Direct HTTP API approach for Synthesia.io /omni command.

No browser / Playwright needed. Uses aiohttp + plain regex only (no bs4).

Flow:
  1. mailticking.com  → regex-scrape Gmail address → activate
  2. POST /api/auth/email-check + /api/auth/signup
  3. Poll mailticking for 6-digit verification code
  4. POST /api/verify-email  → confirm account
  5. POST /api/auth/login    → bearer token
  6. POST /api/auth/onboarding + /api/billing/plan  (free tier)
  7. POST /api/playground/generate  (model=google_omni)
  8. Poll /api/playground/status/{job_id} until complete
  9. Download video bytes → return
"""

import asyncio
import random
import re
from typing import Callable, Awaitable, Optional

import aiohttp

# ── constants ─────────────────────────────────────────────────────────────────
_MAILTICKING_URL = "https://mailticking.com"
_SYNTHESIA_BASE  = "https://app.synthesia.io"
_LOGIN_BASE      = "https://login.synthesia.io"

_FIRST_NAME = "jjdott"
_LAST_NAME  = "jddoooot"
_PASSWORD   = "jodygzzzzz@W1"

_SYNTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Origin": _SYNTHESIA_BASE,
    "Referer": f"{_SYNTHESIA_BASE}/",
    "Accept": "application/json, text/plain, */*",
}

ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]   # kept for interface compat


# ── HTML helpers (no bs4 — pure regex) ───────────────────────────────────────

_GMAIL_RE    = re.compile(r'[a-zA-Z0-9._+]+@gmail\.com')
_TAG_RE      = re.compile(r'<[^>]+>')
_ENTITY_RE   = re.compile(r'&[a-z]+;|&#\d+;')
_SIX_DIGIT   = re.compile(r'\b(\d{6})\b')


def _strip_html(html: str) -> str:
    """Minimal HTML→text: strip tags and common entities."""
    text = _TAG_RE.sub(' ', html)
    text = _ENTITY_RE.sub(' ', text)
    return text


def _find_gmail(html: str) -> Optional[str]:
    """Return the first Gmail address found anywhere in HTML."""
    m = _GMAIL_RE.search(html)
    return m.group(0) if m else None


def _find_6digit_near_synthesia(html: str) -> Optional[str]:
    """Return a 6-digit code that appears near the word 'synthesia' or 'verification'."""
    text = _strip_html(html).lower()
    # Look for code near keywords
    for kw in ("synthesia", "verification", "verify"):
        idx = text.find(kw)
        if idx == -1:
            continue
        # search ±500 chars around the keyword
        window = text[max(0, idx - 200): idx + 300]
        m = _SIX_DIGIT.search(window)
        if m:
            return m.group(1)
    return None


# ── mailticking helpers ───────────────────────────────────────────────────────

async def _get_temp_email(mail_session: aiohttp.ClientSession, progress: ProgressCB) -> str:
    """Scrape a Gmail address from mailticking and activate it."""
    await progress("📧 Opening mailticking.com…")

    html = ""
    for attempt in range(3):
        try:
            async with mail_session.get(
                _MAILTICKING_URL, timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                html = await r.text()
            break
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"mailticking unreachable: {e}") from e
            await asyncio.sleep(4)

    email = _find_gmail(html)
    if not email:
        raise RuntimeError("mailticking: no Gmail address found on page")

    # Find and POST the activate form
    form_action_m = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', html, re.IGNORECASE)
    activate_url: Optional[str] = None
    if form_action_m:
        action = form_action_m.group(1)
        activate_url = action if action.startswith("http") else f"{_MAILTICKING_URL}{action}"

    # Collect form inputs (skip known alias values)
    skip_vals = {"abc@domain.com", "abc+d@gmail.com", "abc@googlemail.com"}
    form_data: dict = {}
    for inp_m in re.finditer(r'<input([^>]*)>', html, re.IGNORECASE):
        attrs = inp_m.group(1)
        name_m  = re.search(r'name=["\']([^"\']+)["\']',  attrs)
        value_m = re.search(r'value=["\']([^"\']*)["\']', attrs)
        if name_m:
            val = value_m.group(1) if value_m else ""
            if val not in skip_vals:
                form_data[name_m.group(1)] = val

    if activate_url:
        try:
            async with mail_session.post(
                activate_url,
                data=form_data,
                headers={"Referer": _MAILTICKING_URL, "X-Requested-With": "XMLHttpRequest"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                print(f"[synth/api] mailticking activate: {r.status}")
        except Exception as e:
            print(f"[synth/api] mailticking activate error (non-fatal): {e}")

    await progress(f"📧 Got email: `{email}`")
    return email


async def _poll_mailticking_code(mail_session: aiohttp.ClientSession, progress: ProgressCB) -> str:
    """Poll mailticking until a 6-digit Synthesia verification code appears."""
    await progress("📬 Polling inbox for verification code…")
    for attempt in range(72):   # 6 minutes max (5s × 72)
        await asyncio.sleep(5)
        try:
            async with mail_session.get(
                _MAILTICKING_URL, timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                html = await r.text()

            code = _find_6digit_near_synthesia(html)
            if code:
                await progress(f"✅ Got verification code: `{code}`")
                return code

            # Try /refresh endpoint
            try:
                async with mail_session.get(
                    f"{_MAILTICKING_URL}/refresh",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r2:
                    html2 = await r2.text()
                code = _find_6digit_near_synthesia(html2)
                if code:
                    await progress(f"✅ Got verification code: `{code}`")
                    return code
            except Exception:
                pass

        except Exception as e:
            print(f"[synth/api] poll error (attempt {attempt + 1}): {e}")

    raise RuntimeError("Timed out waiting for Synthesia verification code")


# ── account creation ──────────────────────────────────────────────────────────

async def _create_account(progress: ProgressCB) -> str:
    """Create a fresh Synthesia free account. Returns a bearer token."""
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(cookie_jar=jar) as mail_session:
        email = await _get_temp_email(mail_session, progress)

        async with aiohttp.ClientSession() as synth_sess:
            # 1. email-check (fire-and-forget, non-critical)
            try:
                await synth_sess.post(
                    f"{_SYNTHESIA_BASE}/api/auth/email-check",
                    json={"email": email},
                    headers=_SYNTH_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=15),
                )
            except Exception:
                pass

            # 2. signup
            await progress("📝 Creating account…")
            async with synth_sess.post(
                f"{_SYNTHESIA_BASE}/api/auth/signup",
                json={
                    "email":     email,
                    "firstName": _FIRST_NAME,
                    "lastName":  _LAST_NAME,
                    "password":  _PASSWORD,
                },
                headers=_SYNTH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                print(f"[synth/api] signup: {r.status}")

            # 3. get verification code from mailticking
            code = await _poll_mailticking_code(mail_session, progress)

            # 4. verify email
            await progress("🔑 Verifying email…")
            async with synth_sess.post(
                f"{_LOGIN_BASE}/api/verify-email",
                json={"code": code, "email": email},
                headers=_SYNTH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                print(f"[synth/api] verify-email: {r.status}")

            # 5. login → token
            await progress("🔓 Logging in…")
            async with synth_sess.post(
                f"{_SYNTHESIA_BASE}/api/auth/login",
                json={"email": email, "password": _PASSWORD},
                headers=_SYNTH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                login_data = await r.json()

            token = (
                login_data.get("token")
                or login_data.get("accessToken")
                or login_data.get("sessionToken")
                or login_data.get("jwt")
            )
            if not token:
                # last-ditch: look in cookies
                for cookie in synth_sess.cookie_jar:
                    if "auth" in cookie.key.lower() or "token" in cookie.key.lower():
                        token = cookie.value
                        break
            if not token:
                raise RuntimeError(f"No bearer token in login response: {login_data}")

            print(f"[synth/api] token acquired: {str(token)[:20]}…")
            auth_headers = {**_SYNTH_HEADERS, "Authorization": f"Bearer {token}"}

            # 6. onboarding
            await progress("📋 Completing onboarding…")
            try:
                await synth_sess.post(
                    f"{_SYNTHESIA_BASE}/api/auth/onboarding",
                    json={
                        "department":    random.choice(["marketing", "sales", "engineering", "design"]),
                        "videoType":     random.choice(["social", "training", "explainer", "presentation"]),
                        "companySize":   random.choice(["1-10", "11-50", "51-200"]),
                        "source":        random.choice(["google", "linkedin", "twitter", "youtube"]),
                        "website":       "",
                        "skipTeammates": True,
                    },
                    headers=auth_headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                )
            except Exception:
                pass

            # 7. free plan
            await progress("🆓 Selecting Free plan…")
            try:
                await synth_sess.post(
                    f"{_SYNTHESIA_BASE}/api/billing/plan",
                    json={"plan": "free"},
                    headers=auth_headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                )
            except Exception:
                pass

            return token


# ── video generation ──────────────────────────────────────────────────────────

async def _generate_with_token(token: str, prompt: str, progress: ProgressCB) -> bytes:
    """Submit a generation job and poll until complete. Returns raw video bytes."""
    auth_headers = {**_SYNTH_HEADERS, "Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession() as session:
        await progress("🎬 Submitting generation job…")
        async with session.post(
            f"{_SYNTHESIA_BASE}/api/playground/generate",
            json={"type": "video", "model": "google_omni", "prompt": prompt},
            headers=auth_headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            result = await r.json()

        job_id = result.get("jobId") or result.get("job_id") or result.get("id")
        print(f"[synth/api] job submitted: {job_id}  raw={result}")

        if not job_id:
            raise RuntimeError(f"No job_id in generate response: {result}")

        await progress("⏳ Waiting for render (~2–4 min)…")

        for attempt in range(120):   # 10 minutes max
            await asyncio.sleep(5)
            try:
                async with session.get(
                    f"{_SYNTHESIA_BASE}/api/playground/status/{job_id}",
                    headers=auth_headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    status_data = await r.json()
            except Exception as poll_e:
                print(f"[synth/api] poll {attempt + 1} error: {poll_e}")
                continue

            status = status_data.get("status", "")
            print(f"[synth/api] poll {attempt + 1}: {status}")

            if status in ("complete", "completed"):
                video_url = (
                    status_data.get("url")
                    or status_data.get("cdnUrl")
                    or status_data.get("videoUrl")
                )
                if not video_url:
                    async with session.get(
                        f"{_SYNTHESIA_BASE}/api/playground/result/{job_id}",
                        headers=auth_headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        final = await r.json()
                    video_url = final.get("url") or final.get("cdnUrl")

                if not video_url:
                    raise RuntimeError(f"Generation complete but no video URL: {status_data}")

                await progress("⬇️ Downloading video…")
                async with session.get(
                    video_url, timeout=aiohttp.ClientTimeout(total=300)
                ) as r:
                    if r.status != 200:
                        raise RuntimeError(f"Video download failed: HTTP {r.status}")
                    video_bytes = await r.read()

                await progress(f"✅ Done ({len(video_bytes) // 1024} KB)")
                return video_bytes

            if status in ("failed", "error"):
                raise RuntimeError(f"Generation failed: {status_data}")

        raise RuntimeError("Timed out waiting for video render (10 min)")


# ── public entry point ────────────────────────────────────────────────────────

async def generate_synthesia_video(
    prompt: str,
    progress_cb: ProgressCB,
    screenshot_cb: Optional[ScreenshotCB] = None,   # unused; kept for interface compat
) -> bytes:
    """
    Full flow: fresh Synthesia account → generate Google Omni video → return bytes.
    Raises RuntimeError on failure.
    """
    token = await _create_account(progress_cb)
    return await _generate_with_token(token, prompt, progress_cb)
