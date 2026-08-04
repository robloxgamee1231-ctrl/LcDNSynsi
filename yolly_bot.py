"""
yolly_bot.py — Playwright automation for Yolly AI (Seedance 2.0) via smailpro.com temp Gmail.

Flow per generation:
  1. smailpro.com  — create a fresh temp Gmail alias
  2. yolly.ai      — sign in with that address → receive OTP
  3. smailpro.com  — grab the OTP from the inbox
  4. yolly.ai      — verify OTP → navigate to Seedance 2.0 page
  5. yolly.ai      — type prompt, pick duration, click Generate, wait
  6.               — download the finished video bytes and return them
"""

import asyncio
import os
import random
import re
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

# Use the same system Nix Chromium as artlist_bot.py — the Playwright-bundled
# Chromium crashes in this environment with "Target page, context or browser
# has been closed".
_CHROMIUM_BIN = (
    "None"
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]


# ---------------------------------------------------------------------------
# Human-like helpers (mirrors artlist_bot.py conventions)
# ---------------------------------------------------------------------------

async def _pause(min_ms: int = 300, max_ms: int = 900) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _snap(page: Page, label: str, cb: Optional[ScreenshotCB]) -> None:
    if cb:
        try:
            img = await page.screenshot(type="jpeg", quality=65, full_page=False)
            await cb(f"[yolly] {label}", img)
        except Exception as e:
            print(f"[yolly] screenshot({label}): {e}")


async def _human_type(page: Page, text: str) -> None:
    """Type text one character at a time with human-like delays."""
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(random.uniform(0.05, 0.14))
        if random.random() < 0.07:
            await _pause(150, 450)


async def _move_click(page: Page, selector: str, timeout: int = 10_000) -> bool:
    """Wait for element, hover + click naturally."""
    try:
        el = await page.wait_for_selector(selector, timeout=timeout, state="visible")
        if not el:
            return False
        box = await el.bounding_box()
        if box:
            x = box["x"] + box["width"]  * random.uniform(0.25, 0.75)
            y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
            await page.mouse.move(x + random.uniform(-4, 4), y + random.uniform(-4, 4))
            await _pause(80, 200)
            await page.mouse.click(x, y)
        else:
            await el.click()
        return True
    except Exception as exc:
        print(f"[yolly] _move_click({selector!r}): {exc}")
        return False


async def _click_text(page: Page, text: str, timeout: int = 10_000) -> bool:
    """Click the first visible element whose text matches (case-insensitive)."""
    try:
        el = await page.wait_for_selector(
            f"text={text}", timeout=timeout, state="visible"
        )
        if el:
            await el.click()
            return True
    except Exception:
        pass
    # JS fallback
    try:
        clicked = await page.evaluate(f"""() => {{
            const tl = {repr(text.lower())};
            for (const el of document.querySelectorAll('button,a,[role=button]')) {{
                if ((el.innerText || el.textContent || '').toLowerCase().trim().includes(tl)) {{
                    el.click(); return true;
                }}
            }}
            return false;
        }}""")
        return bool(clicked)
    except Exception:
        return False


async def _dismiss_ads(page: Page) -> None:
    """Close obvious advertisement overlays / popups."""
    close_selectors = [
        "button.close", "button[class*=close]", "[aria-label='Close']",
        "[aria-label='close']", ".modal-close", ".popup-close",
        "button:has-text('×')", "button:has-text('✕')",
    ]
    for sel in close_selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    await el.click()
                    await _pause(300, 600)
        except Exception:
            pass


async def _handle_cloudflare_challenge(page: Page, wait_s: int = 12) -> bool:
    """
    Detect and attempt to solve a Cloudflare Turnstile / 'Verify you are human'
    challenge.  Works by finding the CF iframe and clicking its checkbox.
    Returns True if a challenge was found (regardless of solve outcome).
    """
    try:
        # Quick check — is there any CF widget on the page?
        cf_present = await page.evaluate("""() => {
            for (const f of document.querySelectorAll('iframe')) {
                if (f.src && (f.src.includes('cloudflare.com') ||
                              f.src.includes('challenges.cloudflare'))) return true;
            }
            if (document.querySelector(
                '[class*="turnstile"],[id*="turnstile"],#cf-turnstile,.cf-turnstile,' +
                'div[class*="cf-chl"]')) return true;
            // Text probe
            const body = document.body.innerText || '';
            return body.includes('Verify you are human') || body.includes('cf-turnstile');
        }""")

        if not cf_present:
            return False

        print("[yolly] ⚠️  Cloudflare challenge detected — attempting to click through…")

        # Try each child frame that looks like a CF challenge
        for frame in page.frames:
            furl = frame.url
            if not ("cloudflare" in furl or "challenges" in furl or "turnstile" in furl):
                continue
            # Try various selectors the CF checkbox uses
            for sel in [
                "input[type='checkbox']",
                ".ctp-checkbox-label",
                "#cf-stage",
                "[data-testid='challenge-body-text']",
                "label",
            ]:
                try:
                    el = await frame.wait_for_selector(sel, timeout=4_000, state="visible")
                    if el:
                        box = await el.bounding_box()
                        if box:
                            cx = box["x"] + box["width"]  * random.uniform(0.35, 0.65)
                            cy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
                            await page.mouse.move(cx, cy)
                            await _pause(200, 400)
                            await page.mouse.click(cx, cy)
                        else:
                            await el.click()
                        print(f"[yolly] CF checkbox clicked ({sel}) — waiting {wait_s}s for pass…")
                        await asyncio.sleep(wait_s)
                        await _snap(page, "cf-challenge-after", None)
                        return True
                except Exception:
                    continue

        # No interactive element found; just wait and hope the JS auto-passes
        print(f"[yolly] CF challenge present but no clickable element found; waiting {wait_s}s…")
        await asyncio.sleep(wait_s)
        return True

    except Exception as exc:
        print(f"[yolly] CF handler error: {exc}")
        return False


# ---------------------------------------------------------------------------
# Step 1 – smailpro.com: create a temp Gmail address
# ---------------------------------------------------------------------------

async def _create_temp_email(
    page: Page,
    progress: Optional[ProgressCB],
    snap: Optional[ScreenshotCB],
) -> str:
    """Navigate to smailpro (MailPro), handle Cloudflare if present, configure
    a Gmail alias, and return the address."""
    if progress:
        await progress("📧 Creating temporary email…")

    await page.goto("https://smailpro.com/temporary-email", wait_until="domcontentloaded", timeout=30_000)
    await _pause(2000, 3500)
    await _snap(page, "smailpro-loaded", snap)

    # Handle Cloudflare "Verify you are human" challenge if present
    cf_found = await _handle_cloudflare_challenge(page, wait_s=12)
    if cf_found:
        await _pause(1500, 2500)
        await _snap(page, "smailpro-after-cf", snap)

    # Click the "+ Create" tab (smailpro/MailPro UI: "History" | "+ Create")
    created = await _click_text(page, "Create")
    if not created:
        await _move_click(page, "button", timeout=5_000)
    await _pause(800, 1400)
    await _snap(page, "smailpro-create-clicked", snap)

    # ── Configure the form ─────────────────────────────────────────────────
    # Each field is a <select> or custom dropdown.  We try <select> first
    # (smailpro uses real <select> elements), then JS fallback.

    async def _set_select(label_text: str, value: str) -> None:
        """Find a <select> near a label matching label_text and set its value."""
        try:
            set_ok = await page.evaluate(f"""() => {{
                const labels = Array.from(document.querySelectorAll('label,th,td,div,span'));
                for (const l of labels) {{
                    if ((l.innerText||l.textContent||'').toLowerCase().trim()
                            .includes({repr(label_text.lower())})) {{
                        // look for a <select> nearby
                        let node = l.nextElementSibling;
                        for (let i=0; i<4 && node; i++) {{
                            if (node.tagName==='SELECT') {{
                                node.value = {repr(value)};
                                node.dispatchEvent(new Event('change',{{bubbles:true}}));
                                return true;
                            }}
                            node = node.nextElementSibling;
                        }}
                        // also search in parent
                        const parent = l.closest('tr,div,li,fieldset');
                        if (parent) {{
                            const sel = parent.querySelector('select');
                            if (sel) {{
                                sel.value = {repr(value)};
                                sel.dispatchEvent(new Event('change',{{bubbles:true}}));
                                return true;
                            }}
                        }}
                    }}
                }}
                return false;
            }}""")
            if set_ok:
                print(f"[yolly/smtp] set '{label_text}' → '{value}'")
                return
        except Exception:
            pass
        # Try Playwright locator with select_option
        try:
            loc = page.get_by_label(re.compile(label_text, re.IGNORECASE))
            await loc.select_option(value=value, timeout=3_000)
        except Exception:
            pass

    await _set_select("Email Type",    "Google")
    await _set_select("Username Type", "Random")
    await _set_select("Account Type",  "Alias")
    await _set_select("Domain",        "gmail.com")
    await _set_select("Server",        "Server-1")
    await _pause(400, 700)
    await _snap(page, "smailpro-configured", snap)

    # Click "Generate"
    if progress:
        await progress("📧 Generating email address…")
    generated = await _click_text(page, "Generate")
    if not generated:
        await _move_click(page, "button[type=submit], button", timeout=5_000)
    await _pause(2000, 3500)
    await _snap(page, "smailpro-generated", snap)

    # ── Extract the email address ──────────────────────────────────────────
    email = ""
    # Try the dedicated readonly input / text element
    for attempt in range(3):
        try:
            email = await page.evaluate("""() => {
                // common patterns: an <input readonly> or a <span>/<div> with the address
                const inp = document.querySelector('input[readonly],input[id*=email],#email-result,#generated-email,.generated-email');
                if (inp && inp.value && inp.value.includes('@')) return inp.value.trim();
                // text nodes containing @gmail.com
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t.includes('@gmail.com') && t.length < 80) return t;
                }
                return '';
            }""")
        except Exception:
            pass
        if email and "@" in email:
            break
        await _pause(1500, 2000)

    if not email:
        raise RuntimeError("[yolly] Could not extract temporary email address from smailpro.com")

    print(f"[yolly] temp email: {email}")
    if progress:
        await progress(f"📧 Temp email ready: `{email}`")
    return email


# ---------------------------------------------------------------------------
# Step 3 – smailpro.com: poll inbox for OTP from hello@yolly.ai
# ---------------------------------------------------------------------------

async def _fetch_otp(
    page: Page,
    progress: Optional[ProgressCB],
    snap: Optional[ScreenshotCB],
    max_wait_s: int = 120,
) -> str:
    """Refresh the smailpro inbox and extract the Yolly OTP. Polls for up to max_wait_s seconds."""
    if progress:
        await progress("📬 Waiting for verification code…")

    deadline = asyncio.get_event_loop().time() + max_wait_s

    while asyncio.get_event_loop().time() < deadline:
        await _pause(3000, 5000)

        # Close ads that might have appeared
        await _dismiss_ads(page)

        # Reload or click Refresh if available
        try:
            refreshed = await _click_text(page, "Refresh")
            if not refreshed:
                await page.reload(wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                pass

        await _pause(1500, 2500)
        await _dismiss_ads(page)
        await _snap(page, "smailpro-inbox", snap)

        # Look for an email row matching "yolly" or "verification"
        found = await page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('tr,li,.mail-item,.email-row,[class*=mail],[class*=inbox]'));
            for (const row of rows) {
                const t = (row.innerText || row.textContent || '').toLowerCase();
                if (t.includes('yolly') || t.includes('verification code')) {
                    // try clicking it
                    row.click();
                    return 'clicked';
                }
            }
            return '';
        }""")

        if found == "clicked":
            await _pause(1500, 2500)
            await _dismiss_ads(page)
            await _snap(page, "smailpro-email-open", snap)

            # Extract the OTP — it is typically a 6-digit code
            otp = await page.evaluate("""() => {
                const body = document.body.innerText || document.body.textContent || '';
                // 6-digit code
                const m = body.match(/\\b(\\d{6})\\b/);
                return m ? m[1] : '';
            }""")
            if otp:
                print(f"[yolly] OTP found: {otp}")
                return otp

            # Maybe the mail body is in an iframe
            for frame in page.frames:
                try:
                    otp = await frame.evaluate("""() => {
                        const body = document.body.innerText || document.body.textContent || '';
                        const m = body.match(/\\b(\\d{6})\\b/);
                        return m ? m[1] : '';
                    }""")
                    if otp:
                        print(f"[yolly] OTP found (frame): {otp}")
                        return otp
                except Exception:
                    pass

        remaining = int(deadline - asyncio.get_event_loop().time())
        print(f"[yolly] OTP not yet arrived — {remaining}s remaining")
        if progress and remaining % 20 < 6:
            await progress(f"📬 Still waiting for OTP… ({remaining}s left)")

    raise RuntimeError("[yolly] Timed out waiting for OTP from Yolly AI")


# ---------------------------------------------------------------------------
# Step 2 + 3 – yolly.ai: sign in, enter OTP
# ---------------------------------------------------------------------------

async def _sign_in_yolly(
    page: Page,
    email: str,
    otp: str,
    progress: Optional[ProgressCB],
    snap: Optional[ScreenshotCB],
) -> None:
    """Navigate to yolly.ai, sign in with email, and enter the OTP."""
    if progress:
        await progress("🔑 Signing into Yolly AI…")

    await page.goto("https://www.yolly.ai", wait_until="domcontentloaded", timeout=30_000)
    await _pause(1500, 2500)
    await _snap(page, "yolly-home", snap)

    # Click Sign In
    clicked = await _click_text(page, "Sign In")
    if not clicked:
        clicked = await _click_text(page, "Sign in")
    if not clicked:
        await _move_click(page, "a[href*=login],a[href*=signin],button:has-text('Sign')", timeout=8_000)
    await _pause(1000, 1800)
    await _snap(page, "yolly-signin-modal", snap)

    # Enter email
    email_input = await page.wait_for_selector(
        "input[type=email],input[name=email],input[placeholder*=email i]",
        timeout=10_000, state="visible",
    )
    await email_input.click()
    await _pause(200, 400)
    await _human_type(page, email)
    await _pause(400, 700)
    await _snap(page, "yolly-email-typed", snap)

    # Click Continue
    await _click_text(page, "Continue")
    await _pause(1500, 2500)
    await _snap(page, "yolly-continue-clicked", snap)

    if progress:
        await progress("🔑 Email submitted — entering verification code…")

    # Enter OTP — might be one input per digit or a single field
    try:
        # Single code input
        code_input = await page.wait_for_selector(
            "input[type=text][maxlength='6'],input[name*=code i],input[placeholder*=code i],input[aria-label*=code i]",
            timeout=10_000, state="visible",
        )
        await code_input.click()
        await _pause(300, 500)
        await code_input.fill(otp)
    except Exception:
        # Try individual digit inputs
        digit_inputs = await page.query_selector_all(
            "input[type=text][maxlength='1'],input[inputmode=numeric][maxlength='1']"
        )
        if len(digit_inputs) >= 6:
            for idx, dig in enumerate(otp[:len(digit_inputs)]):
                await digit_inputs[idx].click()
                await _pause(80, 180)
                await digit_inputs[idx].fill(dig)
                await _pause(60, 140)
        else:
            # Fallback: type into whatever focused input is present
            await page.keyboard.type(otp, delay=100)

    await _pause(600, 1000)
    await _snap(page, "yolly-otp-entered", snap)

    # Submit OTP (Enter or a Verify / Sign In button)
    submitted = await _click_text(page, "Verify")
    if not submitted:
        submitted = await _click_text(page, "Sign In")
    if not submitted:
        await page.keyboard.press("Enter")

    await _pause(2000, 3500)
    await _snap(page, "yolly-signed-in", snap)
    if progress:
        await progress("✅ Signed into Yolly AI")


# ---------------------------------------------------------------------------
# Step 4 + 5 – yolly.ai: generate the video
# ---------------------------------------------------------------------------

async def _generate_yolly_video(
    page: Page,
    prompt: str,
    duration: int,
    progress: Optional[ProgressCB],
    snap: Optional[ScreenshotCB],
) -> bytes:
    """Navigate to Seedance 2.0 page, configure, generate, download, return bytes."""

    if progress:
        await progress("🎬 Opening Seedance 2.0 generator…")

    await page.goto("https://www.yolly.ai/models/seedance-2", wait_until="domcontentloaded", timeout=30_000)
    await _pause(2000, 3500)
    await _snap(page, "yolly-seedance-page", snap)

    # ── Select "Seedance 2.0 Fast" ─────────────────────────────────────────
    selected = False
    # Try clicking a visible tab/button/chip labelled "Fast"
    for label in ("Seedance 2.0 Fast", "Fast", "2.0 Fast"):
        selected = await _click_text(page, label)
        if selected:
            break
    if not selected:
        # JS fallback
        selected = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('button,label,[role=tab],[role=option]')) {
                const t = (el.innerText || el.textContent || '').toLowerCase();
                if (t.includes('fast')) { el.click(); return true; }
            }
            return false;
        }""")
    await _pause(600, 1000)
    await _snap(page, "yolly-fast-selected", snap)

    # ── Type the prompt ─────────────────────────────────────────────────────
    if progress:
        await progress("✍️ Entering prompt…")

    prompt_sel = (
        "textarea[placeholder*=prompt i],"
        "textarea[name*=prompt i],"
        "textarea[aria-label*=prompt i],"
        "textarea"
    )
    prompt_el = await page.wait_for_selector(prompt_sel, timeout=15_000, state="visible")
    await prompt_el.click()
    await _pause(300, 600)
    # Clear existing text
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    await _human_type(page, prompt)
    await _pause(500, 900)
    await _snap(page, "yolly-prompt-typed", snap)

    # ── Set duration ─────────────────────────────────────────────────────────
    if progress:
        await progress(f"⏱️ Setting duration to {duration}s…")

    duration_set = False
    dur_str = str(duration)

    # Try clicking a pill/tab/button with the duration number
    duration_set = await page.evaluate(f"""() => {{
        for (const el of document.querySelectorAll('button,label,[role=tab],[role=option]')) {{
            const t = (el.innerText || el.textContent || '').trim();
            if (t === {repr(dur_str)} || t === {repr(dur_str + 's')} || t === {repr(dur_str + ' s')}) {{
                el.click(); return true;
            }}
        }}
        return false;
    }}""")

    if not duration_set:
        # Try a range input or select
        await page.evaluate(f"""() => {{
            const inputs = document.querySelectorAll('input[type=range],select');
            for (const inp of inputs) {{
                if (inp.tagName === 'SELECT') {{
                    for (const opt of inp.options) {{
                        if (opt.value === {repr(dur_str)} || opt.text.trim() === {repr(dur_str)}) {{
                            inp.value = opt.value;
                            inp.dispatchEvent(new Event('change',{{bubbles:true}}));
                            return;
                        }}
                    }}
                }}
            }}
        }}""")

    await _pause(400, 700)
    await _snap(page, "yolly-duration-set", snap)

    # ── Click Generate ────────────────────────────────────────────────────
    if progress:
        await progress("🚀 Starting generation…")

    gen_clicked = False
    for label in ("Generate", "Create", "Run", "Submit"):
        gen_clicked = await _click_text(page, label)
        if gen_clicked:
            break
    if not gen_clicked:
        await _move_click(page, "button[type=submit]", timeout=5_000)

    await _pause(2000, 3000)
    await _snap(page, "yolly-generating", snap)

    # ── Poll until the video is ready ────────────────────────────────────
    if progress:
        await progress("⏳ Waiting for video to render…")

    video_url: str = ""
    deadline = asyncio.get_event_loop().time() + 600  # 10-minute max

    while asyncio.get_event_loop().time() < deadline:
        await _pause(8000, 12000)

        # Look for a <video> element or a download link that appeared
        video_url = await page.evaluate("""() => {
            // a completed video element
            const vid = document.querySelector('video[src],video source[src]');
            if (vid) {
                const src = vid.src || vid.getAttribute('src') || '';
                if (src && !src.startsWith('blob:')) return src;
            }
            // a download anchor
            const a = document.querySelector('a[download][href],a[href*=".mp4"],a[href*="/download"]');
            if (a) return a.href;
            return '';
        }""")

        if not video_url:
            # Check within any iframes
            for frame in page.frames:
                try:
                    video_url = await frame.evaluate("""() => {
                        const vid = document.querySelector('video[src]');
                        if (vid && vid.src && !vid.src.startsWith('blob:')) return vid.src;
                        const a = document.querySelector('a[download][href]');
                        if (a) return a.href;
                        return '';
                    }""")
                    if video_url:
                        break
                except Exception:
                    pass

        if video_url:
            print(f"[yolly] video URL found: {video_url[:100]}…")
            break

        # Check for error messages
        error_text = await page.evaluate("""() => {
            const body = (document.body.innerText || '').toLowerCase();
            for (const kw of ['error','failed','sorry','limit reached','quota']) {
                if (body.includes(kw)) return body.slice(0, 200);
            }
            return '';
        }""")
        if error_text:
            print(f"[yolly] possible error on page: {error_text[:150]}")

        remaining = int(deadline - asyncio.get_event_loop().time())
        if remaining <= 0:
            break
        if progress and remaining % 60 < 12:
            await progress(f"⏳ Still rendering… ({remaining}s left)")
        await _snap(page, "yolly-wait", snap)

    if not video_url:
        # Last attempt: try clicking the three-dot menu and triggering a download
        video_url = await _try_menu_download(page, snap)

    if not video_url:
        raise RuntimeError("[yolly] Timed out waiting for Yolly video to finish rendering")

    # ── Download the video bytes ──────────────────────────────────────────
    if progress:
        await progress("📥 Downloading video…")

    # Intercept download if the URL is a direct file link we can fetch
    import aiohttp as _aiohttp  # local import to keep module-level clean
    async with _aiohttp.ClientSession() as sess:
        async with sess.get(video_url, timeout=_aiohttp.ClientTimeout(total=120)) as resp:
            resp.raise_for_status()
            video_bytes = await resp.read()

    print(f"[yolly] downloaded {len(video_bytes)/1024/1024:.1f} MB")
    return video_bytes


async def _try_menu_download(page: Page, snap: Optional[ScreenshotCB]) -> str:
    """Try the three-dot menu → Download path to expose a video URL."""
    try:
        # Click a three-dot / kebab menu
        await page.evaluate("""() => {
            for (const el of document.querySelectorAll('[aria-label*=more i],[aria-label*=option i],button')) {
                const t = (el.innerText || el.textContent || '').trim();
                if (t === '⋮' || t === '…' || t === '•••' || t === '' && el.getAttribute('aria-label','').toLowerCase().includes('more')) {
                    el.click(); return;
                }
            }
        }""")
        await _pause(800, 1400)
        # Click Download
        await _click_text(page, "Download")
        await _pause(1500, 2500)
        await _snap(page, "yolly-after-menu-download", snap)
        # Re-check for video URL
        url = await page.evaluate("""() => {
            const a = document.querySelector('a[download][href],a[href*=".mp4"]');
            return a ? a.href : '';
        }""")
        return url or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_yolly_video(
    prompt: str,
    duration: int = 6,
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    """
    Full end-to-end automation:
      smailpro temp Gmail → Yolly sign-in → OTP → generate Seedance 2.0 → return bytes.

    Raises RuntimeError with a descriptive message on failure.
    """

    async with async_playwright() as pw:
        # Use the system Nix Chromium — same binary artlist_bot.py uses.
        # The Playwright-bundled Chromium crashes immediately in this env.
        browser = await pw.chromium.launch(
            executable_path=_CHROMIUM_BIN,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1280,900",
            ],
        )
        _ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        ctx: BrowserContext = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=_ua,
            accept_downloads=True,
            locale="en-US",
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
            print("[yolly] playwright-stealth applied ✓")
        except Exception:
            try:
                from playwright_stealth import stealth_async
                page_tmp = await ctx.new_page()
                await stealth_async(page_tmp)
                await page_tmp.close()
            except Exception:
                pass

        page = await ctx.new_page()

        # Blank second page for smailpro inbox switching
        inbox_page = await ctx.new_page()

        try:
            # ── Step 1: create temp email ───────────────────────────────
            email = await _create_temp_email(inbox_page, progress_cb, screenshot_cb)

            # ── Step 2: start sign-in on Yolly ─────────────────────────
            if progress_cb:
                await progress_cb("🔑 Requesting sign-in OTP from Yolly AI…")

            await page.goto("https://www.yolly.ai", wait_until="domcontentloaded", timeout=30_000)
            await _pause(1500, 2500)

            clicked = await _click_text(page, "Sign In")
            if not clicked:
                clicked = await _click_text(page, "Sign in")
            await _pause(1000, 1800)

            email_input = await page.wait_for_selector(
                "input[type=email],input[name=email],input[placeholder*=email i]",
                timeout=12_000, state="visible",
            )
            await email_input.click()
            await _pause(200, 400)
            await _human_type(page, email)
            await _pause(400, 700)

            await _click_text(page, "Continue")
            await _pause(1500, 2500)
            await _snap(page, "yolly-email-submitted", screenshot_cb)

            # ── Step 3: grab OTP from smailpro inbox ────────────────────
            otp = await _fetch_otp(inbox_page, progress_cb, screenshot_cb)

            # ── Enter OTP on Yolly ──────────────────────────────────────
            if progress_cb:
                await progress_cb("🔑 Entering verification code…")

            try:
                code_input = await page.wait_for_selector(
                    "input[type=text][maxlength='6'],input[name*=code i],input[placeholder*=code i],input[autocomplete*=one-time i]",
                    timeout=10_000, state="visible",
                )
                await code_input.click()
                await code_input.fill(otp)
            except Exception:
                digit_inputs = await page.query_selector_all(
                    "input[type=text][maxlength='1'],input[inputmode=numeric][maxlength='1']"
                )
                if len(digit_inputs) >= 6:
                    for idx, dig in enumerate(otp[:len(digit_inputs)]):
                        await digit_inputs[idx].click()
                        await digit_inputs[idx].fill(dig)
                        await _pause(60, 140)
                else:
                    await page.keyboard.type(otp, delay=100)

            await _pause(500, 900)

            submitted = await _click_text(page, "Verify")
            if not submitted:
                submitted = await _click_text(page, "Sign In")
            if not submitted:
                await page.keyboard.press("Enter")

            await _pause(2500, 4000)
            await _snap(page, "yolly-signed-in", screenshot_cb)
            if progress_cb:
                await progress_cb("✅ Signed in — launching generator…")

            # ── Step 4+5: generate the video ────────────────────────────
            video_bytes = await _generate_yolly_video(
                page, prompt, duration, progress_cb, screenshot_cb
            )

            return video_bytes

        finally:
            await browser.close()
