"""
oreate_bot.py — Oreate AI browser automation via Playwright

Creates a brand-new account for every request:
  1. Gets a temp Gmail alias from smailpro.com (Playwright handles Turnstile)
  2. Registers at oreateai.com with the temp email
  3. Verifies the account via the inbox link
  4. Generates images  (Nano Banana model)   → returns PNG/JPEG bytes
  5. Generates videos  (Seedance 2.0 model)  → returns MP4 bytes

Required environment variable:
  OREATE_PASSWORD — account password used when signing up. Set it as a
                    secret; the module refuses to run without it.

Usage:
  image_bytes = await generate_oreate_image(prompt, progress_cb=...)
  video_bytes = await generate_oreate_video(prompt, progress_cb=...)
"""

import asyncio
import base64
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Awaitable

import aiohttp

# ── Configuration ─────────────────────────────────────────────────────────────

_OREATE_URL       = "https://www.oreateai.com/"
_MAILTICKING_URL  = "https://mailticking.com/"

# Prefer the system Chromium (NixOS), fall back to playwright-bundled path
_CHROMIUM_PATH = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
    or None   # let Playwright use its bundled binary
)


def _password() -> str:
    pw = os.environ.get("OREATE_PASSWORD", "").strip()
    if not pw:
        raise RuntimeError(
            "OREATE_PASSWORD environment variable is not set. "
            "Add it as a secret before using /image or /video."
        )
    return pw


class _InvalidParameterError(RuntimeError):
    """Raised when Oreate AI returns 'Invalid parameter' on account creation.

    This is recoverable — the caller should get a fresh temp email and retry
    the whole flow from the beginning.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _cb(progress_cb, msg: str) -> None:
    if progress_cb:
        try:
            await progress_cb(msg)
        except Exception:
            pass


async def _screenshot(page, label: str, screenshot_cb) -> None:
    """Take a JPEG screenshot of `page` and pass it to screenshot_cb(label, jpeg_bytes).

    screenshot_cb signature: async (label: str, img_bytes: bytes) -> None
    Non-fatal — a failed screenshot never aborts the automation.
    """
    if not screenshot_cb:
        return
    try:
        img = await page.screenshot(type="jpeg", quality=72, full_page=False)
        await screenshot_cb(label, img)
    except Exception as e:
        print(f"[oreate] screenshot failed ({label}): {e}")


async def _dismiss_popups(page) -> None:
    """Dismiss any overlapping modals, cookie banners, or ad pop-ups.

    Handles (in priority order):
      - tempgbox "free inbox limit" modal  → click "Maybe later"
      - Cookie / privacy consent banners   → click "Essential Only" or "Accept All"
      - Generic × / Close / Skip buttons
    Non-fatal — never raises, only logs.
    """
    _POP_SELS = [
        # ── tempgbox free-inbox-limit modal ──────────────────────────────────
        "button:has-text('Maybe later')",
        "a:has-text('Maybe later')",
        "text=Maybe later",
        "button:has-text('No thanks')",
        "text=No thanks",
        # ── Cookie / privacy consent — prefer minimal acceptance ─────────────
        "button:has-text('Essential Only')",
        "text=Essential Only",
        "button:has-text('Reject all')",
        "button:has-text('Reject')",
        "button:has-text('Decline')",
        # ── Generic close / dismiss buttons ──────────────────────────────────
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button[aria-label*='close' i]",
        "button[aria-label*='dismiss' i]",
        "[class*='modal-close']:visible",
        "button:has-text('Close')",
        "button:has-text('Dismiss')",
        "button:has-text('Skip')",
        "button:has-text('×')",
        "button:has-text('✕')",
        # ── Last resort: accept cookie banner so page is usable ──────────────
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
    ]
    for sel in _POP_SELS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=600):
                await el.click(timeout=1_500)
                print(f"[popups] ✅ dismissed via {sel!r}")
                await page.wait_for_timeout(400)
        except Exception:
            continue


async def _new_context(pw):
    """Launch a Chromium browser context (desktop, 1280×900)."""
    kwargs = dict(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox"],
    )
    if _CHROMIUM_PATH:
        kwargs["executable_path"] = _CHROMIUM_PATH
    browser = await pw.chromium.launch(**kwargs)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    return browser, context


async def _new_mobile_context(pw):
    """Launch a Chromium browser context emulating a Pixel 5 Android phone."""
    kwargs = dict(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox"],
    )
    if _CHROMIUM_PATH:
        kwargs["executable_path"] = _CHROMIUM_PATH
    browser = await pw.chromium.launch(**kwargs)
    context = await browser.new_context(
        viewport={"width": 393, "height": 851},
        device_scale_factor=2.75,
        is_mobile=True,
        has_touch=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 12; Pixel 5) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
    )
    return browser, context


async def _click_first(page, selectors: list[str], *, timeout=8_000, label: str) -> None:
    """Click the first matching selector; raise with a clear message if none match."""
    for sel in selectors:
        try:
            await page.click(sel, timeout=timeout)
            return
        except Exception:
            continue
    raise RuntimeError(
        f"[oreate] Could not click '{label}' — tried: {selectors}"
    )


async def _fill_first(page, selectors: list[str], value: str, *, timeout=8_000, label: str) -> None:
    """Fill the first matching input; raise with a clear message if none match."""
    for sel in selectors:
        try:
            await page.fill(sel, value, timeout=timeout)
            return
        except Exception:
            continue
    raise RuntimeError(
        f"[oreate] Could not fill '{label}' — tried: {selectors}"
    )


async def _type_slow(page, selector: str, value: str, *, timeout=8_000, delay_ms=45) -> None:
    """Click + type character-by-character (more human-like than .fill(), avoids
    some frontend validators that only fire on real keystroke events)."""
    await page.click(selector, timeout=timeout)
    await page.fill(selector, "")
    await page.type(selector, value, delay=delay_ms)


async def _fill_react_first(page, selectors: list[str], value: str, *, timeout=8_000, label: str) -> None:
    """Fill a React-controlled input reliably.

    React controlled components ignore page.fill() because it never fires
    onChange — React's internal state stays empty.  We try two strategies:

    Strategy 1 — keyboard.type():
        Focus the field via click(), then use page.keyboard.type() (NOT
        el.type() which re-clicks and can trigger a re-render that resets
        the field).  Fires real keydown/keypress/keyup events that React
        intercepts and updates state on every character.

    Strategy 2 — JS native setter + event dispatch:
        Bypasses React's synthetic event system entirely.  Uses the native
        HTMLInputElement.prototype.value setter so React can't block the
        assignment, then dispatches InputEvent + change so React's listener
        runs and flushes the new value to component state.
    """
    async def _try_keyboard(el) -> bool:
        await el.wait_for(state="visible", timeout=timeout)
        await el.scroll_into_view_if_needed(timeout=2_000)
        await el.click(timeout=timeout)
        await page.wait_for_timeout(150)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(100)
        # Use page.keyboard.type — NOT el.type() which re-clicks the element
        # and can trigger a React re-render that wipes the value
        await page.keyboard.type(value, delay=40)
        await page.wait_for_timeout(200)
        try:
            actual = await el.input_value(timeout=2_000)
            return actual == value
        except Exception:
            return True  # can't verify, assume it worked

    async def _try_js(el) -> bool:
        """JS native setter — the most reliable approach for React inputs."""
        handle = await el.element_handle(timeout=5_000)
        if not handle:
            return False
        await page.evaluate(
            """([el, val]) => {
                // Use the native setter so React's defineProperty interception
                // doesn't swallow the assignment
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeSetter.call(el, val);
                // Dispatch events so React's onChange listener fires
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, inputType: 'insertText', data: val
                }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            [handle, value],
        )
        await page.wait_for_timeout(100)
        try:
            actual = await el.input_value(timeout=2_000)
            return actual == value
        except Exception:
            return True

    for sel in selectors:
        el = page.locator(sel).first
        for strategy_name, strategy in [("keyboard", _try_keyboard), ("js-setter", _try_js)]:
            try:
                if await strategy(el):
                    print(f"[oreate] _fill_react_first: filled {label!r} via {strategy_name} ({sel!r})")
                    return
            except Exception as exc:
                print(f"[oreate] _fill_react_first({sel!r}) {strategy_name} failed: {exc}")

    raise RuntimeError(
        f"[oreate] Could not fill (react) '{label}' — tried all strategies for: {selectors}"
    )


# ── Step 1: Get temporary email from mailticking.com ──────────────────────────
# mailticking.com generates @gmail.com / @googlemail.com addresses:
#   1. Load https://mailticking.com/
#   2. Dismiss any modal / cookie banner
#   3. Read the displayed email address
#   4. If it has a '+' alias or a non-Gmail domain, click the inverted-arrows
#      (↺ refresh) icon to regenerate — repeat up to 5×
#   5. Click "Activate" to start the inbox
#   6. Return the clean email address
#
# The inbox appears on the same page after activation.

_GOOD_DOMAINS = {"gmail.com", "googlemail.com"}

# Broad email regex — we validate the domain ourselves
_EMAIL_RE_BROAD = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', re.IGNORECASE)
_EMAIL_PLACEHOLDERS = {
    "user@domain.com", "example@gmail.com", "test@gmail.com",
    "your@email.com", "abc@domain.com",
}


def _is_good_email(addr: str) -> bool:
    """Return True if addr is a clean Gmail/Googlemail address with no + alias."""
    addr = addr.strip().lower()
    if not addr or addr in _EMAIL_PLACEHOLDERS:
        return False
    if "+" in addr:
        return False
    local, _, domain = addr.partition("@")
    return domain in _GOOD_DOMAINS


async def _read_email_from_page(page) -> str:
    """Extract the first plausible email address from the page (inputs first, then body text)."""
    # Priority 1: readonly / email-labelled input fields
    for sel in [
        "input[readonly]",
        "input[id*='email' i]",
        "input[name*='email' i]",
        "input[class*='email' i]",
        "input[type='email']",
        "input[type='text']",
    ]:
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(count):
                val = (await els.nth(i).get_attribute("value", timeout=1_500) or "").strip()
                m = _EMAIL_RE_BROAD.search(val)
                if m and m.group().lower() not in _EMAIL_PLACEHOLDERS:
                    return m.group()
        except Exception:
            continue
    # Priority 2: full page text scan
    try:
        body = await page.inner_text("body", timeout=5_000)
        for m in _EMAIL_RE_BROAD.finditer(body):
            candidate = m.group()
            if candidate.lower() not in _EMAIL_PLACEHOLDERS:
                return candidate
    except Exception:
        pass
    return ""


async def _click_refresh_arrows(page) -> bool:
    """Click the inverted-arrows / refresh icon on mailticking to generate a new address."""
    # Ordered from most specific to most generic
    _REFRESH_SELS = [
        # Ant Design / common icon libraries
        ".anticon-reload",
        ".anticon-sync",
        ".anticon-redo",
        "span[role='img'][aria-label*='reload' i]",
        "span[role='img'][aria-label*='sync' i]",
        "span[role='img'][aria-label*='refresh' i]",
        # Generic attribute-based
        "button[aria-label*='refresh' i]",
        "button[aria-label*='reload' i]",
        "button[aria-label*='regenerate' i]",
        "button[aria-label*='new' i]",
        "[class*='refresh']:visible",
        "[class*='reload']:visible",
        "[class*='rotate']:visible",
        "[class*='sync']:visible",
        # Icon tags
        "i[class*='refresh' i]",
        "i[class*='sync' i]",
        "i[class*='reload' i]",
        "i[class*='rotate' i]",
        "span[class*='refresh' i]",
        "svg[class*='refresh' i]",
    ]
    for sel in _REFRESH_SELS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.click(timeout=2_000)
                print(f"[mailticking] ↺ refresh clicked via {sel!r}")
                return True
        except Exception:
            continue
    # Last resort: JS click on the first SVG inside a button/span near the email field
    try:
        clicked = await page.evaluate("""() => {
            const candidates = [
                ...document.querySelectorAll('button svg, span svg, [class*="reload"] svg, [class*="refresh"] svg')
            ];
            for (const svg of candidates) {
                const btn = svg.closest('button, span, div, a');
                if (btn) { btn.click(); return true; }
            }
            return false;
        }""")
        if clicked:
            print("[mailticking] ↺ refresh clicked via JS SVG fallback")
            return True
    except Exception:
        pass
    return False


async def _get_temp_email(page, screenshot_cb=None) -> str:
    """Navigate to mailticking.com and return a clean @googlemail.com address.

    mailticking shows a modal "Your Temp Email is Ready" with:
      • An input containing the raw inbox address (e.g. imildaly@rulersonline.com)
      • 4 alias-format checkboxes:
          ☑ abc@domain.com        ← non-gmail domain, leave alone
          ☑ a.b.c@gmail.com       ← dotted gmail, fine
          ☑ abc+d@gmail.com       ← PLUS ALIAS — must UNCHECK (Oreate rejects these)
          ☑ abc@googlemail.com    ← keep checked — this is what we register with
      • A yellow Activate button

    Flow:
      1. Load https://mailticking.com/ and dismiss any popups
      2. Read the raw email → extract username (part before @)
      3. Uncheck the abc+d@gmail.com checkbox via JS (Playwright click is blocked by the overlay)
      4. Click Activate
      5. Return username@googlemail.com
    """
    print("[mailticking] ── STEP 1 ── mailticking.com → getting email address")

    await page.goto(_MAILTICKING_URL, wait_until="domcontentloaded", timeout=40_000)
    await page.wait_for_timeout(2_500)
    await _dismiss_popups(page)
    await page.wait_for_timeout(500)
    await _screenshot(page, "🌐 mailticking.com loaded", screenshot_cb)

    # ── Read the raw inbox email (any domain, e.g. imildaly@rulersonline.com) ─
    raw_email = await _read_email_from_page(page)
    print(f"[mailticking] raw inbox email: {raw_email!r}")

    if not raw_email or "@" not in raw_email:
        await _screenshot(page, "❌ mailticking: no email found on page", screenshot_cb)
        raise RuntimeError("mailticking.com: could not read an email address from the modal")

    username = raw_email.split("@")[0].strip().lower()
    print(f"[mailticking] extracted username: {username!r}")

    await _screenshot(page, "📋 mailticking: modal visible — unchecking + alias checkbox", screenshot_cb)

    # ── Uncheck the abc+d@gmail.com (plus-alias) checkbox ────────────────────
    # Strategy 1: JS — find all checkboxes whose label/container text contains '+'
    # and uncheck them. This bypasses any click-blocking overlay on the modal.
    unchecked_js = False
    try:
        unchecked_js = await page.evaluate("""() => {
            let unchecked = false;
            // Walk every checkbox on the page
            const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
            for (const cb of boxes) {
                // Find the nearest text-containing ancestor
                const container = cb.closest('li, label, span, div') || cb.parentElement;
                const text = container ? container.textContent : '';
                // Match the + alias pattern: contains '+' and 'gmail'
                if (text.includes('+') && text.toLowerCase().includes('gmail')) {
                    if (cb.checked) {
                        cb.click();          // toggle off
                        unchecked = true;
                        console.log('[mailticking] JS unchecked + alias:', text.trim());
                    }
                }
            }
            return unchecked;
        }""")
        print(f"[mailticking] JS uncheck of + alias: {'✅ done' if unchecked_js else 'was already unchecked or not found'}")
    except Exception as e:
        print(f"[mailticking] JS uncheck error: {e}")

    # Strategy 2: Playwright locator fallback (in case JS approach missed it)
    if not unchecked_js:
        for sel in [
            "li:has-text('+') input[type='checkbox']",
            "label:has-text('+d@gmail') input[type='checkbox']",
            "label:has-text('+') input[type='checkbox']",
        ]:
            try:
                cb = page.locator(sel).first
                if await cb.is_visible(timeout=1_000) and await cb.is_checked(timeout=1_000):
                    await cb.uncheck(timeout=2_000)
                    print(f"[mailticking] ✅ unchecked + alias via {sel!r}")
                    unchecked_js = True
                    break
            except Exception:
                continue

    await page.wait_for_timeout(400)
    await _screenshot(page, "📋 mailticking: + alias unchecked — clicking Activate", screenshot_cb)

    # ── Click Activate ────────────────────────────────────────────────────────
    print("[mailticking] clicking Activate…")
    activate_clicked = False
    for sel in [
        "button:has-text('Activate')",
        "a:has-text('Activate')",
        "text=Activate",
        "input[value*='Activate' i]",
    ]:
        try:
            await page.locator(sel).first.click(timeout=5_000)
            activate_clicked = True
            print(f"[mailticking] ✅ Activate clicked via {sel!r}")
            break
        except Exception:
            continue

    if not activate_clicked:
        print("[mailticking] ⚠️  Activate button not found — trying JS click")
        try:
            await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a'));
                const act = btns.find(b => b.textContent.trim().toLowerCase().includes('activate'));
                if (act) act.click();
            }""")
        except Exception:
            pass

    await page.wait_for_timeout(1_500)
    await _dismiss_popups(page)

    # ── Build the registration email ──────────────────────────────────────────
    # mailticking routes username@googlemail.com to this inbox (checkbox was kept checked)
    email = f"{username}@googlemail.com"

    await _screenshot(page, f"📧 mailticking: inbox active, using {email}", screenshot_cb)
    print(f"[mailticking] ✅ email={email!r}")
    return email


# ── Step 2: Create Oreate AI account ─────────────────────────────────────────
# Page layout (from live screenshot):
#   - Homepage loads the SPA dashboard at /
#   - Top-right: "Log in" button/link
#   - Bottom-left sidebar: "Log in" link
#   - Clicking "Log in" opens a login modal/page that also has a "Sign up" link
#   - No "Try for Free" button on the main app page

async def _create_account(oreate_page, email: str, progress_cb=None, screenshot_cb=None) -> None:
    print(f"[oreate] ── STEP 2 ── oreateai.com → creating account with {email}")
    await _cb(progress_cb, "🌐 Opening Oreate AI sign-up page…")
    await oreate_page.goto(_OREATE_URL, wait_until="domcontentloaded", timeout=30_000)
    await oreate_page.wait_for_timeout(2_000)
    await _dismiss_popups(oreate_page)  # clear any cookie/ad banners on the homepage
    await oreate_page.wait_for_timeout(400)
    await _screenshot(oreate_page, "🌐 Oreate AI homepage — before clicking Try for Free", screenshot_cb)

    # SOP Step 5: Select "Try for Free" — opens the sign-up modal with the
    # registration form (Email + Password + Terms checkbox + "Create Account").
    # Fall back to "Log in" if "Try for Free" isn't visible (same modal).
    await _click_first(
        oreate_page,
        [
            "button:has-text('Try for Free')",
            "a:has-text('Try for Free')",
            "text=Try for Free",
            "button:has-text('Get Started')",
            "a:has-text('Get Started')",
            # Fallback — Log in link also opens the auth modal
            "text=Log in",
            "a:has-text('Log in')",
            "button:has-text('Log in')",
            "button:has-text('Login')",
            ".login-btn",
        ],
        timeout=12_000,
        label="Try for Free / Log in button",
    )
    await oreate_page.wait_for_timeout(1_500)

    # Wait for the sign-up modal — try several known field selectors
    # (the site has used both IDs like #form_item_email and plain placeholders).
    modal_found = False
    for modal_sel in [
        "input[placeholder='Email']",
        "input[placeholder='email']",
        "input[type='email']",
        "#form_item_email",
        "input[placeholder*='email' i]",
        "input[placeholder*='mail' i]",
    ]:
        try:
            await oreate_page.wait_for_selector(modal_sel, timeout=5_000)
            modal_found = True
            break
        except Exception:
            continue

    if not modal_found:
        await _screenshot(oreate_page, "❌ Sign-up modal did NOT appear — debug this", screenshot_cb)
        raise RuntimeError("Oreate AI sign-up modal did not appear after clicking Try for Free / Log in")

    print("[oreate] sign-up modal open — filling email + password + checkbox")
    await _screenshot(oreate_page, "📝 Sign-up modal open — filling form now", screenshot_cb)
    await _cb(progress_cb, "📝 Filling sign-up form…")

    # Fill email — use React-aware typing so onChange fires and the controlled
    # input's internal state matches what we typed (plain fill() is silent to React)
    await _fill_react_first(
        oreate_page,
        [
            "input[placeholder='Email']",
            "input[placeholder='email']",
            "input[type='email']",
            "#form_item_email",
            "input[placeholder*='mail' i]",
        ],
        email,
        timeout=8_000,
        label="email field",
    )
    await oreate_page.wait_for_timeout(400)

    # Fill password — same React-aware approach
    await _fill_react_first(
        oreate_page,
        [
            "input[placeholder='Password']",
            "input[placeholder='password']",
            "input[type='password']",
            "#form_item_password",
            "input[placeholder*='password' i]",
        ],
        _password(),
        timeout=8_000,
        label="password field",
    )
    await oreate_page.wait_for_timeout(400)

    # Accept Terms & Conditions checkbox — physical click required (React state)
    checkbox_clicked = False
    for cb_sel in [
        "input[type='checkbox']",
        "#form_item_check",
        "label:has-text('Terms')",
        "label:has-text('accept')",
    ]:
        try:
            cb = oreate_page.locator(cb_sel).first
            await cb.scroll_into_view_if_needed(timeout=2_000)
            await cb.click(timeout=4_000)
            await oreate_page.wait_for_timeout(300)
            checkbox_clicked = True
            print(f"[oreate] T&C checkbox clicked via: {cb_sel!r}")
            break
        except Exception:
            continue

    if not checkbox_clicked:
        try:
            await oreate_page.locator("input[type='checkbox']").first.click(force=True, timeout=4_000)
            print("[oreate] T&C checkbox force-clicked")
        except Exception:
            print("[oreate] ⚠️  could not click T&C checkbox — form may reject")

    await oreate_page.wait_for_timeout(300)
    await _screenshot(oreate_page, "📝 Form filled — email, password, T&C done → submitting", screenshot_cb)
    await _cb(progress_cb, "📝 Submitting account creation form…")
    print("[oreate] clicking Create Account button")

    await _click_first(
        oreate_page,
        [
            "button:has-text('Create Account')",
            "button:has-text('Sign Up')",
            "button:has-text('Sign up')",
            "button:has-text('Register')",
            "button[type='submit']",
            "input[type='submit']",
        ],
        timeout=10_000,
        label="Create Account / Sign Up button",
    )

    # Wait for the server response (toast/banner appears within ~3 s).
    await oreate_page.wait_for_timeout(3_000)
    await _screenshot(oreate_page, "✅ Create Account submitted — checking for confirmation", screenshot_cb)

    # Collect page text via multiple methods — the "Invalid parameter" badge is a
    # styled chip element that inner_text() can skip if it is aria-hidden or opacity-0.
    try:
        page_text = await oreate_page.inner_text("body", timeout=5_000)
    except Exception:
        page_text = ""
    try:
        raw_html = await oreate_page.content()
    except Exception:
        raw_html = ""
    try:
        js_text = await oreate_page.evaluate("() => document.body.innerText")
    except Exception:
        js_text = ""
    combined = (page_text + " " + raw_html + " " + js_text).lower()
    lowered = combined  # keep name consistent with code below

    # "Invalid parameter" means Oreate AI rejected the email address.
    # This is recoverable — the caller retries with a fresh temp email.
    if "invalid parameter" in lowered:
        await _screenshot(oreate_page, "❌ Invalid parameter — will retry with a fresh email", screenshot_cb)
        print("[oreate] ❌ 'Invalid parameter' detected — raising _InvalidParameterError for retry")
        raise _InvalidParameterError(
            "Oreate AI returned 'Invalid parameter' — email was rejected by the server"
        )

    if "already exists" in lowered or "already registered" in lowered:
        raise RuntimeError(f"Account creation failed — page says: {page_text[:300]}")

    print("[oreate] ✅ account created — verification email should arrive shortly")
    await _cb(progress_cb, "✅ Account created — verification email on its way…")


# ── Step 3: Verify the email via tempgbox.net ────────────────────────────────

async def _verify_email(
    email: str,
    tgbox_page,
    oreate_page,
    progress_cb=None,
    screenshot_cb=None,
) -> None:
    """Scroll down on tempgbox.net and wait for the Oreate AI verification email.

    The inbox is on the same page — just scroll down. When the email appears,
    click it to reveal the body, then extract the verification link and navigate
    the oreate_page to it.
    """
    print(f"[tempgbox] ── STEP 3 ── tempgbox.net → waiting for verification email ({email})")
    await _cb(progress_cb, "📬 Checking tempgbox.net inbox for Oreate AI verification email…")

    verify_url: str | None = None
    seen_subjects: set[str] = set()

    for attempt in range(50):  # 50 × 6 s ≈ 5 min
        await asyncio.sleep(6)
        elapsed = (attempt + 1) * 6
        print(f"[tempgbox] verify poll {attempt + 1}/50 ({elapsed}s elapsed)")

        # Scroll down to the inbox area
        try:
            await tgbox_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        # Look for inbox rows — tempgbox shows emails in a table/list below the generator
        try:
            for row_sel in [
                "tr:has-text('oreate')",
                "tr:has-text('verify')",
                "tr:has-text('confirm')",
                "li:has-text('oreate')",
                "li:has-text('verify')",
                "div[class*='mail']:has-text('oreate')",
                "div[class*='mail']:has-text('verify')",
                # Broader: any row with a keyword
                "tr", "li[class*='email']", "li[class*='message']",
                ".inbox-row", ".mail-row",
            ]:
                rows = tgbox_page.locator(row_sel)
                count = await rows.count()
                for i in range(count):
                    try:
                        row_text = (await rows.nth(i).inner_text(timeout=2_000)).lower()
                    except Exception:
                        continue
                    is_oreate = any(
                        kw in row_text
                        for kw in ("oreate", "verify", "confirm", "welcome", "activate")
                    )
                    if not is_oreate:
                        continue
                    if row_text in seen_subjects:
                        continue
                    seen_subjects.add(row_text)

                    print(f"[tempgbox] ✅ Oreate email row found — clicking it")
                    await _screenshot(tgbox_page, "📬 tempgbox: Oreate email arrived!", screenshot_cb)

                    # Click the row to open/expand the message
                    try:
                        await rows.nth(i).click(timeout=3_000)
                    except Exception:
                        pass
                    await tgbox_page.wait_for_timeout(2_000)

                    # Read the body — check for an iframe or inline text
                    body_html = ""
                    try:
                        iframe = tgbox_page.frame_locator("iframe").first
                        body_html = await iframe.locator("body").inner_html(timeout=4_000)
                    except Exception:
                        pass
                    if not body_html:
                        try:
                            body_html = await tgbox_page.inner_html("body", timeout=4_000)
                        except Exception:
                            pass

                    for u in re.findall(r'https?://[^\s"\'<>\\]+', body_html):
                        if any(k in u.lower() for k in ("verify", "confirm", "activate")):
                            if "tempgbox" not in u.lower():
                                verify_url = u
                                print(f"[tempgbox] verify link: {u[:80]}")
                                break
                    if verify_url:
                        break
                if verify_url:
                    break
        except Exception as exc:
            print(f"[tempgbox] inbox scan error: {exc}")

        if verify_url:
            print(f"[tempgbox] ✅ verification link secured after {elapsed}s")
            break

        await _cb(progress_cb, f"📬 Still waiting for verification email… ({elapsed}s)")

    if not verify_url:
        raise RuntimeError("Verification email from Oreate AI did not arrive within ~5 minutes")

    await _cb(progress_cb, "🔗 Clicking email verification link…")
    print(f"[oreate] navigating to verify URL: {verify_url[:100]}")
    await oreate_page.goto(verify_url, wait_until="domcontentloaded", timeout=30_000)
    await oreate_page.wait_for_timeout(4_000)

    page_text = ""
    try:
        page_text = await oreate_page.inner_text("body", timeout=5_000)
    except Exception:
        pass
    if any(w in page_text.lower() for w in ("error", "invalid", "expired", "failed")):
        await _screenshot(oreate_page, "❌ Verification failed — check this page", screenshot_cb)
        raise RuntimeError(f"Verification failed — page says: {page_text[:300]}")

    await _screenshot(oreate_page, "✅ Verified! Oreate AI dashboard is live", screenshot_cb)
    print("[oreate] ✅ email verified — Oreate AI dashboard loaded")
    await _cb(progress_cb, "✅ Email verified — account is active!")


# ── Image / video byte helpers ────────────────────────────────────────────────

async def _fetch_image_bytes(page, src: str) -> bytes:
    if src.startswith("data:"):
        _, encoded = src.split(",", 1)
        return base64.b64decode(encoded)
    if src.startswith("blob:"):
        arr = await page.evaluate(
            """async (url) => {
                const r = await fetch(url);
                return Array.from(new Uint8Array(await r.arrayBuffer()));
            }""",
            src,
        )
        return bytes(arr)
    import aiohttp as _aio
    async with _aio.ClientSession() as s:
        async with s.get(src, timeout=_aio.ClientTimeout(total=60)) as r:
            return await r.read()


async def _fetch_video_bytes(page, src: str) -> bytes:
    if src.startswith("blob:"):
        arr = await page.evaluate(
            """async (url) => {
                const r = await fetch(url);
                return Array.from(new Uint8Array(await r.arrayBuffer()));
            }""",
            src,
        )
        return bytes(arr)
    import aiohttp as _aio
    async with _aio.ClientSession() as s:
        async with s.get(src, timeout=_aio.ClientTimeout(total=180)) as r:
            return await r.read()


# ── Image generation (Nano Banana) ────────────────────────────────────────────

async def _generate_image_on_page(oreate_page, prompt: str, progress_cb=None, screenshot_cb=None) -> bytes:
    print("[oreate] ── STEP 4 ── generating image (Nano Banana)")
    await _cb(progress_cb, "🖼️ Opening AI Image section…")

    await _click_first(
        oreate_page,
        ["text=AI Image", "a[href*='image']", "nav a:has-text('Image')", "button:has-text('Image')"],
        timeout=12_000,
        label="AI Image nav",
    )
    await oreate_page.wait_for_timeout(2_000)
    await _screenshot(oreate_page, "🖼️ AI Image section open", screenshot_cb)

    # SOP: Nano Banana is the default model — switch to it if something else is selected
    try:
        page_text = await oreate_page.inner_text("body", timeout=5_000)
        if "Nano Banana" not in page_text:
            print("[oreate] Nano Banana not visible — attempting to select it")
            for sel in ["text=Nano Banana", "option:has-text('Nano')", "button:has-text('Nano')"]:
                try:
                    await oreate_page.click(sel, timeout=5_000)
                    await oreate_page.wait_for_timeout(1_000)
                    print(f"[oreate] Nano Banana selected via {sel!r}")
                    break
                except Exception:
                    continue
        else:
            print("[oreate] Nano Banana already selected ✅")
    except Exception:
        pass
    await _screenshot(oreate_page, "🖼️ Model check — Nano Banana confirmed", screenshot_cb)

    # Snapshot existing images so we can detect the newly generated one
    existing_srcs: set[str] = set()
    try:
        for h in await oreate_page.query_selector_all("img"):
            s = await h.get_attribute("src") or ""
            existing_srcs.add(s)
    except Exception:
        pass

    print(f"[oreate] entering image prompt: {prompt[:80]!r}")
    await _cb(progress_cb, "🖼️ Entering image prompt…")
    await _fill_first(
        oreate_page,
        ["textarea[placeholder*='prompt' i]", "textarea[placeholder*='describe' i]", "textarea",
         "input[placeholder*='prompt' i]", "[contenteditable='true']"],
        prompt,
        timeout=8_000,
        label="prompt field",
    )

    await _cb(progress_cb, "🖼️ Clicking Generate…")
    print("[oreate] clicking Generate button (image)")
    await _click_first(
        oreate_page,
        ["button[aria-label*='generate' i]", "button[title*='generate' i]",
         "button[aria-label*='send' i]", "button:has-text('Generate')", "button[type='submit']"],
        timeout=8_000,
        label="Generate button",
    )
    await _screenshot(oreate_page, "🖼️ Generate clicked — waiting for image result", screenshot_cb)
    await _cb(progress_cb, "⏳ Waiting for image… (1–3 min)")

    async def _new_img() -> str | None:
        for h in await oreate_page.query_selector_all("img"):
            src = await h.get_attribute("src") or ""
            if src and src not in existing_srcs and (
                src.startswith("blob:") or src.startswith("data:")
                or "generated" in src or "output" in src
            ):
                return src
        return None

    deadline = asyncio.get_event_loop().time() + 180
    new_src = None
    while asyncio.get_event_loop().time() < deadline:
        new_src = await _new_img()
        if new_src:
            break
        await oreate_page.wait_for_timeout(3_000)

    if not new_src:
        await _screenshot(oreate_page, "❌ Image never appeared — timed out after 3 min", screenshot_cb)
        raise RuntimeError("Image generation did not produce a new result within 3 minutes")

    print("[oreate] ✅ new image detected — downloading")
    await oreate_page.wait_for_timeout(1_500)
    await _screenshot(oreate_page, "🖼️ Image generated! Downloading now…", screenshot_cb)
    await _cb(progress_cb, "📥 Downloading generated image…")

    image_bytes: bytes | None = None
    try:
        async with oreate_page.expect_download(timeout=30_000) as dl_info:
            await _click_first(
                oreate_page,
                ["button[aria-label*='download' i]", "button[title*='download' i]",
                 "a[download]", "button:has-text('Download')"],
                timeout=5_000, label="Download button",
            )
        download = await dl_info.value
        image_bytes = Path(await download.path()).read_bytes()
        print(f"[oreate] image downloaded via Playwright download: {len(image_bytes)//1024} KB")
    except Exception as e:
        print(f"[oreate] download button fallback: {e}")

    if not image_bytes and new_src:
        image_bytes = await _fetch_image_bytes(oreate_page, new_src)
        if image_bytes:
            print(f"[oreate] image fetched via src URL: {len(image_bytes)//1024} KB")

    if not image_bytes:
        try:
            image_bytes = await oreate_page.locator("img").last.screenshot()
            print("[oreate] image captured via element screenshot fallback")
        except Exception:
            pass

    if not image_bytes:
        await _screenshot(oreate_page, "❌ Could not download image — debug needed", screenshot_cb)
        raise RuntimeError("Could not download the generated image from Oreate AI")

    print(f"[oreate] ✅ image ready — {len(image_bytes)//1024} KB")
    return image_bytes


# ── Video generation (Seedance 2.0) ──────────────────────────────────────────

async def _generate_video_on_page(oreate_page, prompt: str, image_bytes: bytes | None, progress_cb=None, screenshot_cb=None) -> bytes:
    print("[oreate] ── STEP 5 ── generating video (Seedance 2.0 @ 480p)")
    await _cb(progress_cb, "🎬 Returning to dashboard via purple geometric logo…")

    # SOP Part 4 Step 1: Return to main dashboard by selecting the purple geometric logo
    logo_clicked = False
    for logo_sel in [
        "[class*='logo']",
        "a[href='/']",
        "a[href='/dashboard']",
        "img[alt*='logo' i]",
        "img[src*='logo' i]",
        ".logo-link",
        "[class*='brand']",
    ]:
        try:
            await oreate_page.click(logo_sel, timeout=3_000)
            logo_clicked = True
            print(f"[oreate] purple logo clicked via {logo_sel!r}")
            break
        except Exception:
            continue

    if not logo_clicked:
        # Fallback: navigate directly to root URL
        print("[oreate] logo click failed — navigating to root URL as fallback")
        try:
            await oreate_page.goto(_OREATE_URL, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass
    await oreate_page.wait_for_timeout(1_500)
    await _screenshot(oreate_page, "🎬 Back on Oreate AI dashboard", screenshot_cb)

    # SOP Part 4 Step 2: Use back arrow if necessary
    for sel in ["button[aria-label*='back' i]", "[class*='back-arrow']", "button:has-text('Back')"]:
        try:
            await oreate_page.click(sel, timeout=3_000)
            await oreate_page.wait_for_timeout(1_000)
            print(f"[oreate] back arrow clicked via {sel!r}")
            break
        except Exception:
            continue

    # SOP Part 4 Step 3: Select Seedance
    print("[oreate] navigating to AI Video / Seedance section")
    await _click_first(
        oreate_page,
        ["text=AI Video", "text=Seedance", "a[href*='video']", "a[href*='seedance']",
         "button:has-text('Video')", "button:has-text('Seedance')"],
        timeout=12_000,
        label="AI Video / Seedance nav",
    )
    await oreate_page.wait_for_timeout(1_500)
    await _screenshot(oreate_page, "🎬 Seedance section open", screenshot_cb)

    # SOP Part 4 Step 4: Choose Seedance 2.0
    print("[oreate] selecting Seedance 2.0")
    try:
        await _click_first(
            oreate_page,
            ["text=Seedance 2.0", "option:has-text('2.0')", "button:has-text('2.0')", "[data-value*='2.0']"],
            timeout=8_000,
            label="Seedance 2.0 option",
        )
        await oreate_page.wait_for_timeout(1_000)
        print("[oreate] ✅ Seedance 2.0 selected")
    except Exception:
        print("[oreate] ⚠️  Seedance 2.0 click failed — may already be selected")
    await _screenshot(oreate_page, "🎬 Seedance 2.0 selected", screenshot_cb)

    # SOP Part 4 Step 5: Set the output quality to 480p
    await _cb(progress_cb, "🎬 Setting output quality to 480p…")
    quality_set = False
    for q_val in ["480p", "480"]:
        for q_sel in [
            "select[name*='quality' i]",
            "select[id*='quality' i]",
            "select[class*='quality' i]",
            "select",
        ]:
            try:
                await oreate_page.select_option(q_sel, label=q_val, timeout=2_000)
                quality_set = True
                break
            except Exception:
                try:
                    await oreate_page.select_option(q_sel, value=q_val, timeout=2_000)
                    quality_set = True
                    break
                except Exception:
                    continue
        if quality_set:
            break

    if not quality_set:
        for btn_q in [
            "button:has-text('480p')",
            "label:has-text('480p')",
            "[data-quality='480p']",
            "[data-value='480p']",
            "input[value='480p']",
            "input[value='480']",
            "span:has-text('480p')",
        ]:
            try:
                await oreate_page.click(btn_q, timeout=2_000)
                quality_set = True
                break
            except Exception:
                continue

    if not quality_set:
        try:
            await oreate_page.evaluate("""() => {
                for (const sel of document.querySelectorAll('select')) {
                    const opt = Array.from(sel.options).find(
                        o => o.text.includes('480') || o.value.includes('480')
                    );
                    if (opt) {
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        return;
                    }
                }
            }""")
            quality_set = True
        except Exception:
            pass

    if quality_set:
        print("[oreate] ✅ quality set to 480p")
        await oreate_page.wait_for_timeout(600)
    else:
        print("[oreate] ⚠️  could not set 480p quality — proceeding with default")
    await _screenshot(oreate_page, f"🎬 Quality {'set to 480p ✅' if quality_set else '⚠️ could not set 480p'}", screenshot_cb)

    print(f"[oreate] entering video prompt: {prompt[:80]!r}")
    await _cb(progress_cb, "🎬 Entering video prompt…")
    await _fill_first(
        oreate_page,
        ["textarea[placeholder*='prompt' i]", "textarea[placeholder*='describe' i]", "textarea",
         "input[placeholder*='prompt' i]", "[contenteditable='true']"],
        prompt,
        timeout=8_000,
        label="prompt field",
    )

    if image_bytes:
        await _cb(progress_cb, "🖼️ Uploading image as video reference…")
        print(f"[oreate] uploading image reference ({len(image_bytes)//1024} KB)")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            await oreate_page.locator("input[type='file']").first.set_input_files(tmp_path, timeout=10_000)
            await oreate_page.wait_for_timeout(2_000)
            print("[oreate] ✅ image reference uploaded")
        except Exception as e:
            print(f"[oreate] image upload skipped: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # Snapshot existing video elements so we can detect the new one
    existing_video_srcs: set[str] = set()
    try:
        for h in await oreate_page.query_selector_all("video, a[download*='.mp4']"):
            s = (await h.get_attribute("src") or "") + (await h.get_attribute("href") or "")
            existing_video_srcs.add(s)
    except Exception:
        pass

    await _cb(progress_cb, "🎬 Clicking Generate…")
    print("[oreate] clicking Generate button (video)")
    await _click_first(
        oreate_page,
        ["button[aria-label*='generate' i]", "button[title*='generate' i]",
         "button[aria-label*='send' i]", "button:has-text('Generate')", "button[type='submit']"],
        timeout=8_000,
        label="Generate button",
    )
    await _screenshot(oreate_page, "🎬 Generate clicked — waiting for video result (2–6 min)", screenshot_cb)
    await _cb(progress_cb, "⏳ Waiting for video… (2–6 min)")

    async def _new_video() -> str | None:
        for sel, attr in [("video[src]", "src"), ("video source[src]", "src"),
                           ("a[href*='.mp4']", "href"), ("a[download*='.mp4']", "href")]:
            try:
                for h in await oreate_page.query_selector_all(sel):
                    s = await h.get_attribute(attr) or ""
                    if s and s not in existing_video_srcs:
                        return s
            except Exception:
                continue
        return None

    deadline = asyncio.get_event_loop().time() + 420
    new_src = None
    elapsed_checks = 0
    while asyncio.get_event_loop().time() < deadline:
        new_src = await _new_video()
        if new_src:
            break
        await oreate_page.wait_for_timeout(5_000)
        elapsed_checks += 1
        if elapsed_checks % 12 == 0:  # screenshot every ~60s
            await _screenshot(oreate_page, f"⏳ Still waiting for video… ({elapsed_checks * 5}s)", screenshot_cb)

    if not new_src:
        await _screenshot(oreate_page, "❌ Video never appeared — timed out after 7 min", screenshot_cb)
        raise RuntimeError("Video generation did not produce a new result within 7 minutes")

    print("[oreate] ✅ new video detected — downloading")
    await oreate_page.wait_for_timeout(2_000)
    await _screenshot(oreate_page, "🎬 Video generated! Downloading now…", screenshot_cb)
    await _cb(progress_cb, "📥 Downloading generated video…")

    video_bytes: bytes | None = None
    try:
        async with oreate_page.expect_download(timeout=60_000) as dl_info:
            await _click_first(
                oreate_page,
                ["button[aria-label*='download' i]", "button[title*='download' i]",
                 "a[download]", "button:has-text('Download')"],
                timeout=8_000, label="Download button",
            )
        video_bytes = Path(await (await dl_info.value).path()).read_bytes()
        print(f"[oreate] video downloaded via Playwright download: {len(video_bytes)//1024//1024} MB")
    except Exception as e:
        print(f"[oreate] download button fallback: {e}")

    if not video_bytes and new_src:
        video_bytes = await _fetch_video_bytes(oreate_page, new_src)
        if video_bytes:
            print(f"[oreate] video fetched via src URL: {len(video_bytes)//1024//1024} MB")

    if not video_bytes:
        await _screenshot(oreate_page, "❌ Could not download video — debug needed", screenshot_cb)
        raise RuntimeError("Could not download the generated video from Oreate AI")

    print(f"[oreate] ✅ video ready — {len(video_bytes)//1024//1024} MB")
    return video_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

_MAX_SIGNUP_RETRIES = 5  # max attempts when Oreate AI returns "Invalid parameter"


async def generate_oreate_image(
    prompt: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """SOP: tempgbox.net → oreateai.com → verify → generate image."""
    _password()
    print("[oreate] ════════════════════════════════════════")
    print("[oreate]  IMAGE GENERATION — new account every run")
    print("[oreate]  ORDER: tempgbox.net → oreateai.com")
    print("[oreate] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()
                # ① tempgbox.net — get temp Gmail address via Playwright
                await _cb(progress_cb, "📧 Getting temporary Gmail address from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email ready: {email}")
                # ② oreateai.com — create account with that email
                try:
                    await _create_account(oreate_page, email, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"Oreate AI kept returning 'Invalid parameter' after "
                            f"{attempt} attempts — giving up"
                        ) from exc
                    print(f"[oreate] ⚠️  _InvalidParameterError on attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Oreate AI rejected the email (attempt {attempt}/{_MAX_SIGNUP_RETRIES}) — retrying…")
                    continue
                # ③ tempgbox.net — wait for verification email, click link
                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)
                # ④ oreateai.com — generate image
                return await _generate_image_on_page(oreate_page, prompt, progress_cb, screenshot_cb)
            finally:
                await browser.close()
    raise RuntimeError("Image generation failed — all signup attempts exhausted")


async def generate_oreate_video(
    prompt: str,
    image_bytes: bytes | None = None,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """SOP: tempgbox.net → oreateai.com → verify → image ref → video."""
    _password()
    print("[oreate] ════════════════════════════════════════")
    print("[oreate]  VIDEO GENERATION — new account every run")
    print("[oreate]  ORDER: tempgbox.net → oreateai.com")
    print("[oreate] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()
                # ① tempgbox.net — get temp Gmail address via Playwright
                await _cb(progress_cb, "📧 Getting temporary Gmail address from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email ready: {email}")
                # ② oreateai.com — create account
                try:
                    await _create_account(oreate_page, email, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"Oreate AI kept returning 'Invalid parameter' after "
                            f"{attempt} attempts — giving up"
                        ) from exc
                    print(f"[oreate] ⚠️  _InvalidParameterError on attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Oreate AI rejected the email (attempt {attempt}/{_MAX_SIGNUP_RETRIES}) — retrying…")
                    continue
                # ③ tempgbox.net — wait for verification email, click link
                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)
                # ④ oreateai.com — generate reference image with Nano Banana
                ref = image_bytes
                if ref is None:
                    await _cb(progress_cb, "🖼️ Generating reference image (Nano Banana)…")
                    ref = await _generate_image_on_page(oreate_page, prompt, progress_cb, screenshot_cb)
                    await _cb(progress_cb, "🖼️ Reference image done — switching to Seedance 2.0…")
                # ⑤ oreateai.com — generate Seedance 2.0 video @ 480p
                return await _generate_video_on_page(oreate_page, prompt, ref, progress_cb, screenshot_cb)
            finally:
                await browser.close()
    raise RuntimeError("Video generation failed — all signup attempts exhausted")
