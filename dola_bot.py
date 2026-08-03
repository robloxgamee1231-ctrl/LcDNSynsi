"""
dola_bot.py — Playwright automation for dola.com/chat (Seedance video generation).

Dola AI is a ByteDance product that exposes a Seedance 2.0 video model via its
chat interface.  Authentication is handled via saved session cookies.

One-time setup (no proxy needed — dola.com is globally accessible):
  1. Visit https://www.dola.com/chat in your browser and log in.
  2. Open DevTools → Application → Cookies → dola.com
  3. Right-click → "Export cookies as JSON" (or use the "EditThisCookie" extension).
  4. Save the JSON array to  <workspace>/.dola_session.json
  5. Subsequent bot runs auto-refresh the cookies after each successful generation.

Optional automatic login (email + OTP):
  • Set the  DOLA_EMAIL  secret to your dola.com email address.
  • The bot will try to enter the email; if an OTP is needed and cannot be
    auto-filled it raises DolaAuthError with clear next steps.

Public API:
  video_bytes = await generate_dola_video(prompt, progress_cb=..., screenshot_cb=...)
"""

import asyncio
import json
import os
import shutil
import tempfile
import random
from pathlib import Path
from typing import Callable, Awaitable, Optional

import aiohttp
from playwright.async_api import async_playwright, Page, BrowserContext

ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]

# ── Configuration ─────────────────────────────────────────────────────────────

_DOLA_URL     = "https://www.dola.com/chat"
_DOLA_EMAIL   = os.environ.get("DOLA_EMAIL", "").strip()
_COOKIES_FILE = Path(__file__).parent / ".dola_session.json"
_CHROMIUM_BIN = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
    or None
)

_STEALTH_JS = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}};
"""


# ── Custom exceptions ──────────────────────────────────────────────────────────

class DolaAuthError(RuntimeError):
    """Authentication failed or no session cookies are present."""


class DolaError(RuntimeError):
    """Video generation failed."""


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _snap(page: Page, label: str, cb: Optional[ScreenshotCB]) -> None:
    if not cb:
        return
    try:
        img = await page.screenshot(type="jpeg", quality=65)
        await cb(f"[dola] {label}", img)
    except Exception as e:
        print(f"[dola] screenshot({label}): {e}")


async def _pause(lo: int = 300, hi: int = 700) -> None:
    await asyncio.sleep(random.uniform(lo, hi) / 1_000)


async def _save_cookies(ctx: BrowserContext) -> None:
    try:
        cookies = await ctx.cookies()
        _COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        print(f"[dola] saved {len(cookies)} cookies → {_COOKIES_FILE.name}")
    except Exception as e:
        print(f"[dola] could not save cookies: {e}")


async def _load_cookies(ctx: BrowserContext) -> bool:
    if not _COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(_COOKIES_FILE.read_text())
        await ctx.add_cookies(cookies)
        print(f"[dola] loaded {len(cookies)} cookies from {_COOKIES_FILE.name}")
        return True
    except Exception as e:
        print(f"[dola] could not load cookies: {e}")
        return False


async def _new_browser(pw):
    """Launch a stealth Chromium browser context."""
    kwargs: dict = dict(
        headless=True,
        args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-setuid-sandbox", "--no-zygote",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    if _CHROMIUM_BIN:
        kwargs["executable_path"] = _CHROMIUM_BIN
    browser = await pw.chromium.launch(**kwargs)
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "sec-ch-ua": '"Chromium";v="138", "Google Chrome";v="138", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    # Apply stealth
    try:
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(ctx)
    except Exception:
        try:
            from playwright_stealth import stealth
            _s = stealth()
            if isinstance(_s, str):
                await ctx.add_init_script(_s)
            else:
                await ctx.add_init_script(_STEALTH_JS)
        except Exception:
            await ctx.add_init_script(_STEALTH_JS)
    return browser, ctx


async def _is_logged_in(page: Page) -> bool:
    """Returns True when the page shows the dola.com chat UI."""
    try:
        url = page.url
        if "/login" in url or "/signin" in url or "/register" in url:
            return False
        result = await page.evaluate("""() => {
            // A signed-in chat page has a text input and does NOT have a login form
            const hasLogin = !!document.querySelector(
                'button:not([disabled])'
            ) && /sign.?in|log.?in|get started/i.test(
                document.body.innerText.slice(0, 1000)
            );
            if (hasLogin) return false;
            const inp = document.querySelector(
                'textarea, [contenteditable="true"], '
                'input[placeholder*="message" i], input[placeholder*="chat" i], '
                'input[placeholder*="type" i], [class*="input"][class*="chat"], '
                '[class*="chat-input"], [class*="message-input"]'
            );
            return !!inp;
        }""")
        return bool(result)
    except Exception:
        return False


async def _try_email_login(page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]) -> bool:
    """
    Attempt email login if DOLA_EMAIL is configured.
    Returns True on successful authentication.
    """
    if not _DOLA_EMAIL:
        return False

    print(f"[dola] attempting email login with {_DOLA_EMAIL!r}")
    await progress("🔐 Logging in to dola.com…")

    try:
        await page.goto(_DOLA_URL, wait_until="domcontentloaded", timeout=30_000)
        await _pause(2_000, 3_000)
        await _snap(page, "pre-login", snap)

        # Click "Sign in" / "Log in" button
        for sel in [
            "button:has-text('Sign in')", "a:has-text('Sign in')",
            "button:has-text('Log in')", "a:has-text('Log in')",
            "button:has-text('Get started')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2_000):
                    await el.click()
                    await _pause(1_000, 1_500)
                    print(f"[dola] clicked login trigger: {sel!r}")
                    break
            except Exception:
                continue

        await _snap(page, "login-modal", snap)

        # Click "Continue with Email" if shown
        for sel in [
            "button:has-text('Continue with Email')",
            "button:has-text('Email')",
            "button:has-text('Sign in with Email')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2_000):
                    await el.click()
                    await _pause(600, 900)
                    break
            except Exception:
                continue

        # Fill email input
        for sel in [
            "input[type='email']",
            "input[placeholder*='email' i]",
            "input[name='email']",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3_000):
                    await el.fill(_DOLA_EMAIL)
                    await _pause(300, 500)
                    for btn_sel in [
                        "button[type='submit']",
                        "button:has-text('Continue')",
                        "button:has-text('Send')",
                        "button:has-text('Next')",
                    ]:
                        try:
                            b = page.locator(btn_sel).first
                            if await b.is_visible(timeout=1_500):
                                await b.click()
                                break
                        except Exception:
                            continue
                    await _pause(2_000, 3_000)
                    break
            except Exception:
                continue

        await _snap(page, "post-email-submit", snap)

        # Check for OTP prompt — we can't auto-fill without an email inbox
        body_text = await page.evaluate("() => document.body.innerText")
        if any(w in body_text.lower() for w in ("verification code", "otp", "one-time", "check your email", "sent you")):
            print("[dola] OTP required — cannot auto-complete without email access")
            return False

        return await _is_logged_in(page)

    except Exception as e:
        print(f"[dola] email login error: {e}")
        return False


async def _open_video_generation(page: Page, snap: Optional[ScreenshotCB]) -> None:
    """
    Navigate to the dola.com chat page and activate the Video Generation skill.
    """
    await page.goto(_DOLA_URL, wait_until="domcontentloaded", timeout=30_000)
    await _pause(2_000, 3_000)
    await _snap(page, "chat-home", snap)

    # Try to find and click the video generation skill button
    video_clicked = False

    # Strategy 1: named buttons / menu items
    for sel in [
        "button:has-text('Video')",
        "[aria-label*='Video Generation' i]",
        "[aria-label*='Video' i]",
        "button[title*='Video' i]",
        "[class*='skill']:has-text('Video')",
        "button:has-text('Video Generation')",
        "li:has-text('Video Generation')",
        "button:has-text('Create Video')",
        "[data-skill='video']",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_200):
                await el.click()
                await _pause(800, 1_200)
                video_clicked = True
                print(f"[dola] video skill clicked: {sel!r}")
                break
        except Exception:
            continue

    # Strategy 2: JS evaluation for text-matching
    if not video_clicked:
        video_clicked = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('button,li,div,span,a')) {
                const t = (
                    el.innerText ||
                    el.getAttribute('aria-label') ||
                    el.getAttribute('title') || ''
                ).trim();
                if (/^video( generation)?$/i.test(t) || /^create video$/i.test(t)) {
                    if (el.offsetParent !== null && !el.disabled) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }""")
        if video_clicked:
            await _pause(800, 1_200)
            print("[dola] video skill clicked via evaluate")

    await _snap(page, "video-skill-activated", snap)

    if not video_clicked:
        print("[dola] ⚠️ video skill button not found — will attempt to trigger via prompt prefix")


async def _find_chat_input(page: Page) -> Optional[str]:
    """Return a Playwright selector for the visible chat input, or None."""
    for sel in [
        "textarea",
        "[contenteditable='true']:not([readonly])",
        "input[type='text']:not([readonly]):not([type='search'])",
        "[class*='chat-input'] input",
        "[class*='message-input'] input",
        "[class*='input-area'] textarea",
        "[placeholder*='message' i]",
        "[placeholder*='chat' i]",
        "[placeholder*='type' i]",
        "[placeholder*='ask' i]",
        "[placeholder*='send' i]",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_200):
                return sel
        except Exception:
            continue
    return None


async def _submit_prompt(page: Page, prompt: str, snap: Optional[ScreenshotCB]) -> None:
    """Type the prompt into the chat input and submit it."""
    input_sel = await _find_chat_input(page)
    if not input_sel:
        await _snap(page, "no-chat-input", snap)
        raise DolaError(
            "Could not find the dola.com chat input field. "
            "Session may have expired — delete .dola_session.json and log in again."
        )

    await page.locator(input_sel).first.click()
    await _pause(300, 500)

    # If video skill wasn't explicitly clicked, prepend a generation request
    full_prompt = prompt

    await page.locator(input_sel).first.fill(full_prompt)
    await _pause(400, 700)
    await _snap(page, "prompt-typed", snap)

    # Submit
    submitted = False
    for btn_sel in [
        "button[type='submit']:visible",
        "button[aria-label*='send' i]:visible",
        "button[aria-label*='submit' i]:visible",
        "[class*='send-btn']:visible",
        "[class*='submit']:visible",
    ]:
        try:
            btn = page.locator(btn_sel).first
            if await btn.is_visible(timeout=1_500):
                await btn.click()
                submitted = True
                print(f"[dola] prompt submitted via {btn_sel!r}")
                break
        except Exception:
            continue

    if not submitted:
        await page.keyboard.press("Enter")
        print("[dola] prompt submitted via Enter")

    await _pause(1_000, 1_500)
    await _snap(page, "prompt-submitted", snap)


async def _wait_and_download(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> bytes:
    """Poll until a video appears in the chat, then download and return it."""
    intercepted_urls: list[str] = []

    async def _capture_video(response):
        ct = response.headers.get("content-type", "")
        url = response.url
        if ("video/" in ct or ".mp4" in url or ".webm" in url) and url not in intercepted_urls:
            intercepted_urls.append(url)
            print(f"[dola] 🎥 video URL intercepted: {url[:80]}")

    page.on("response", _capture_video)

    try:
        for tick in range(240):  # up to 20 min
            await asyncio.sleep(5)
            elapsed = (tick + 1) * 5

            state = await page.evaluate(r"""() => {
                const body = document.body.innerText || '';

                // Check for a ready video element
                const vids = Array.from(document.querySelectorAll('video'));
                for (const v of vids) {
                    if (v.offsetParent !== null) {
                        return {
                            status: 'video',
                            url: (!v.src || v.src.startsWith('blob:')) ? '' : v.src
                        };
                    }
                }

                // Download button visible?
                for (const el of document.querySelectorAll('button,a')) {
                    const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                    if (el.offsetParent && (t.includes('download') || t.includes('save video'))) {
                        return {status: 'download-btn'};
                    }
                }

                // Progress indicators
                const m = body.match(/(\d{1,3})\s*%/);
                const pct = m ? m[1] : '';
                if (/generating|processing|creating|loading/i.test(body) || pct) {
                    return {status: 'progress', pct};
                }

                // Error states
                if (/failed|error|couldn.t|could not generate/i.test(body)) {
                    return {status: 'error'};
                }

                return {status: 'waiting'};
            }""")

            status = state.get("status", "waiting")

            if status == "progress":
                pct = state.get("pct", "")
                await progress(f"⏳ Generating… {pct + '%' if pct else ''} ({elapsed}s)")

            elif status == "video":
                url = state.get("url", "")
                print(f"[dola] ✅ video element detected at ~{elapsed}s (src={'set' if url else 'blob'})")
                await _snap(page, f"video-ready-{elapsed}s", snap)
                if url:
                    intercepted_urls.insert(0, url)
                break

            elif status == "download-btn":
                print(f"[dola] ✅ download button detected at ~{elapsed}s")
                await _snap(page, f"download-btn-{elapsed}s", snap)
                break

            elif status == "error":
                await _snap(page, "dola-generation-error", snap)
                raise DolaError("dola.com reported a generation error — please try again.")

            else:
                await progress(f"⏳ Waiting for video… ({elapsed}s)")

            if tick % 6 == 5:
                await _snap(page, f"dola-tick-{elapsed}s", snap)

        else:
            await _snap(page, "dola-timeout", snap)
            raise DolaError("Video generation timed out after 20 minutes on dola.com")

    finally:
        try:
            page.remove_listener("response", _capture_video)
        except Exception:
            pass

    await _pause(1_500, 2_500)

    # ── Download attempt 1: click download button and capture file ────────────
    dl_future: asyncio.Future = asyncio.get_event_loop().create_future()

    def _on_dl(dl):
        if not dl_future.done():
            dl_future.set_result(dl)

    page.context.on("download", _on_dl)

    for dl_sel in [
        "button:has-text('Download')",
        "a:has-text('Download')",
        "button[aria-label*='download' i]",
        "a[download]",
        "[class*='download']:visible",
    ]:
        try:
            el = page.locator(dl_sel).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                print(f"[dola] download clicked via {dl_sel!r}")
                break
        except Exception:
            continue

    for _ in range(24):  # wait up to 12 s
        if dl_future.done():
            page.context.remove_listener("download", _on_dl)
            dl = dl_future.result()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
            await dl.save_as(tmp_path)
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            print(f"[dola] ✅ downloaded {len(data) // 1024 // 1024} MB via download event")
            return data
        await asyncio.sleep(0.5)

    page.context.remove_listener("download", _on_dl)

    # ── Download attempt 2: intercepted video URLs ────────────────────────────
    if intercepted_urls:
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        for url in intercepted_urls:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(
                        url,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/138.0.0.0 Safari/537.36",
                            "Referer": "https://www.dola.com/",
                            "Cookie": cookie_str,
                        },
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            print(f"[dola] ✅ downloaded {len(data) // 1024 // 1024} MB from intercepted URL")
                            return data
                        print(f"[dola] intercepted URL returned HTTP {resp.status}: {url[:60]}")
            except Exception as e:
                print(f"[dola] intercepted URL download failed: {e}")

    # ── Download attempt 3: extract src from <video> element ─────────────────
    video_src = await page.evaluate("""() => {
        for (const v of document.querySelectorAll('video')) {
            if (v.src && !v.src.startsWith('blob:')) return v.src;
            for (const s of v.querySelectorAll('source')) {
                if (s.src && !s.src.startsWith('blob:')) return s.src;
            }
        }
        return null;
    }""")

    if video_src:
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                video_src,
                headers={"Cookie": cookie_str, "Referer": "https://www.dola.com/"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    print(f"[dola] ✅ downloaded {len(data) // 1024 // 1024} MB from video.src")
                    return data

    await _snap(page, "dola-download-failed", snap)
    raise DolaError(
        "Video was generated but could not be downloaded from dola.com.\n"
        "The video may be ready at dola.com/chat — please download it manually."
    )


# ── Public entry point ─────────────────────────────────────────────────────────

async def generate_dola_video(
    prompt: str,
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    """
    Generate a Seedance video via dola.com/chat.

    Returns raw MP4 bytes.

    Raises DolaAuthError if not authenticated (see module docstring for setup).
    Raises DolaError on generation or download failure.
    """
    async def _noop(_): pass
    progress = progress_cb or _noop
    snap     = screenshot_cb

    async with async_playwright() as pw:
        browser, ctx = await _new_browser(pw)
        try:
            page = await ctx.new_page()

            # ── Step 1: authenticate ──────────────────────────────────────────
            cookies_loaded = await _load_cookies(ctx)
            logged_in = False

            if cookies_loaded:
                await progress("🔐 Checking dola.com session…")
                await page.goto(_DOLA_URL, wait_until="domcontentloaded", timeout=30_000)
                await _pause(2_000, 3_000)
                logged_in = await _is_logged_in(page)
                print(f"[dola] cookie session: {'✅ valid' if logged_in else '❌ expired'}")

            if not logged_in and _DOLA_EMAIL:
                logged_in = await _try_email_login(page, progress, snap)

            if not logged_in:
                raise DolaAuthError(
                    "Not logged in to dola.com.\n\n"
                    "One-time setup:\n"
                    "  1. Visit dola.com/chat in your browser and log in.\n"
                    "  2. DevTools → Application → Cookies → copy all dola.com cookies as JSON.\n"
                    "  3. Save to `.dola_session.json` in the workspace root.\n"
                    "  OR set the DOLA_EMAIL secret for automatic email-login attempts."
                )

            # ── Step 2: open video generation ─────────────────────────────────
            await progress("🎬 Opening video generation on dola.com…")
            await _open_video_generation(page, snap)

            # ── Step 3: submit prompt ─────────────────────────────────────────
            await progress("✍️ Entering prompt…")
            await _submit_prompt(page, prompt, snap)

            # ── Step 4: wait and download ─────────────────────────────────────
            await progress("⏳ Your video is generating on dola.com…")
            video_bytes = await _wait_and_download(page, progress, snap)

            # Persist refreshed cookies
            await _save_cookies(ctx)

            await progress("✅ Done!")
            return video_bytes

        finally:
            try:
                await ctx.close()
                await browser.close()
            except Exception:
                pass
