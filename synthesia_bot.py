"""
synthesia_bot.py — Playwright automation for Synthesia.io /omni command
Flow per request:
  1. mailticking.com → uncheck 3 alias checkboxes → copy top Gmail → Activate
  2. app.synthesia.io/#/welcome → Work Email → fill form (first/last name, password)
  3. mailticking.com → refresh → find Synthesia verification email → copy 6-digit code
  4. Enter code on Synthesia verification page
  5. Select Free plan
  6. Complete onboarding questions (any answer), skip website, skip teammates
  7. My Media tab → Prompt settings (Gemini Omni model, audio on, 1 generation)
     → close settings → paste prompt → send (▲) → Generate
  8. Wait for render → Download video → return bytes
"""

import asyncio
import random
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

_MAILTICKING_URL    = "https://mailticking.com"
_SYNTHESIA_WELCOME  = "https://app.synthesia.io/#/welcome"

# Fixed registration details (per guide — fresh account every run)
_FIRST_NAME = "jjdott"
_LAST_NAME  = "jddoooot"
_PASSWORD   = "jodygzzzzz@W1"

ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]

_CHROMIUM  = shutil.which("chromium") or None
_SNAP_DIR  = Path("screenshots/synthesia")
_SNAP_DIR.mkdir(parents=True, exist_ok=True)

# ── stealth JS injected before every page load ────────────────────────────────
# Patches the most common bot-detection fingerprints without needing
# playwright-stealth as an installed package.
_STEALTH_JS = """
() => {
    // 1. Hide webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Fake plugins array (real browsers have plugins)
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // 3. Fake languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    // 4. Patch chrome runtime so CDP checks pass
    window.chrome = { runtime: {} };

    // 5. Fix permissions API (headless returns 'denied', real returns 'prompt')
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );

    // 6. Realistic screen dimensions
    Object.defineProperty(screen, 'width',       { get: () => 1920 });
    Object.defineProperty(screen, 'height',      { get: () => 1080 });
    Object.defineProperty(screen, 'availWidth',  { get: () => 1920 });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
    Object.defineProperty(screen, 'colorDepth',  { get: () => 24  });

    // 7. Remove headless clues from user-agent string in JS
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome'),
    });
}
"""


# ── human-like interaction helpers ───────────────────────────────────────────

async def _human_delay(min_ms: int = 80, max_ms: int = 400) -> None:
    """Random pause that mimics human reaction time."""
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def _human_type(page: Page, selector: str, text: str, timeout: int = 6000) -> bool:
    """Click a field then type each character with random inter-key delays."""
    try:
        el = await page.wait_for_selector(selector, timeout=timeout, state="visible")
        if not el:
            return False
        await el.click()
        await _human_delay(150, 350)
        for char in text:
            await page.keyboard.type(char)
            await _human_delay(45, 180)
            # Occasional longer pause (thinking / hesitation)
            if random.random() < 0.06:
                await _human_delay(300, 700)
        return True
    except Exception:
        return False


async def _human_move_and_click(page: Page, selector: str, timeout: int = 6000) -> bool:
    """Move mouse in a slight arc to the element, then click — avoids straight-line teleport."""
    try:
        el = await page.wait_for_selector(selector, timeout=timeout, state="visible")
        if not el:
            return False
        box = await el.bounding_box()
        if not box:
            await el.click()
            return True
        # Target: centre of element ± small jitter
        tx = box["x"] + box["width"]  / 2 + random.uniform(-4, 4)
        ty = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
        # A few intermediate waypoints so the path isn't a straight line
        steps = random.randint(12, 25)
        for i in range(1, steps + 1):
            t  = i / steps
            # Quadratic bezier with a random mid-point offset
            mx = tx * t + random.uniform(-20, 20) * (1 - t) * t * 4
            my = ty * t + random.uniform(-20, 20) * (1 - t) * t * 4
            await page.mouse.move(mx, my)
            await asyncio.sleep(random.uniform(0.008, 0.025))
        await page.mouse.click(tx, ty)
        return True
    except Exception:
        return False


# ── helpers ────────────────────────────────────────────────────────────────────

async def _snap(page: Page, label: str, cb: Optional[ScreenshotCB]) -> None:
    try:
        img = await page.screenshot(type="jpeg", quality=65, full_page=False)
    except Exception as e:
        print(f"[synthesia] screenshot({label}): {e}")
        return

    # Always save to disk
    safe_label = re.sub(r"[^\w\-]", "_", label)
    try:
        (_SNAP_DIR / f"{safe_label}.jpg").write_bytes(img)
        print(f"[snap] 📸 screenshots/synthesia/{safe_label}.jpg")
    except Exception as e:
        print(f"[synthesia] disk-save({label}): {e}")

    # Also fire the Discord DM callback
    if cb:
        try:
            await cb(f"[synthesia] {label}", img)
        except Exception as e:
            print(f"[synthesia] screenshot-cb({label}): {e}")


async def _click_first(page: Page, selectors: list[str], timeout: int = 4000, force: bool = False) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                await el.click(force=force, timeout=5_000)
                return True
        except Exception:
            pass
    return False


async def _fill_first(page: Page, selectors: list[str], value: str, timeout: int = 4000) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                await el.click()
                await el.fill(value)
                return True
        except Exception:
            pass
    return False


# ── mailticking helpers (same security/dialog patterns as buzzy_bot) ───────────

async def _dismiss_security_verification(page: Page) -> bool:
    try:
        body = await page.inner_text("body")
        if "verify you are human" not in body.lower() and "too many requests" not in body.lower():
            return False
        print("[synthesia/mail] security-verification popup detected — clicking checkbox")
        for sel in ["input[type='checkbox']", "label:has-text('verify')", ".verify-checkbox"]:
            try:
                el = await page.wait_for_selector(sel, timeout=2_000, state="visible")
                if el:
                    await el.click()
                    await page.wait_for_timeout(1_000)
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


async def _dismiss_no_email_dialog(page: Page) -> bool:
    try:
        body = await page.inner_text("body")
        if "no email found" not in body.lower():
            return False
        for sel in ["button:has-text('OK')", "button:has-text('Close')",
                    "button:has-text('Cancel')", ".modal button", "[role='dialog'] button"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


# ── Step 1: get temp Gmail from mailticking ────────────────────────────────────

async def _get_temp_email(page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]) -> str:
    await progress("📧 Opening mailticking.com…")
    await page.goto(_MAILTICKING_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(4_000)
    await _dismiss_security_verification(page)
    await _snap(page, "mailticking-loaded", snap)

    # Uncheck the three unwanted formats
    checkboxes = await page.query_selector_all("input[type='checkbox']")
    for cb_el in checkboxes:
        try:
            label_text = await page.evaluate(
                """el => {
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) return lbl.textContent || '';
                    }
                    const p = el.closest('label') || el.parentElement;
                    return p ? p.textContent || '' : '';
                }""",
                cb_el,
            )
            label_text = (label_text or "").strip().lower()
            should_uncheck = (
                "+" in label_text
                or "googlemail" in label_text
                or "domain" in label_text
            )
            if should_uncheck:
                is_checked = await cb_el.is_checked()
                if is_checked:
                    await cb_el.click()
                    await page.wait_for_timeout(400)
                    print(f"[synthesia/mail] unchecked: {label_text[:60]}")
        except Exception as e:
            print(f"[synthesia/mail] checkbox err: {e}")

    await page.wait_for_timeout(800)
    await _snap(page, "mailticking-unchecked", snap)

    # Click Change / ↺
    changed = await _click_first(page, [
        "button:has-text('Change')", "button[class*='change' i]",
        "a:has-text('Change')", "button:has-text('↺')",
        "button.btn-warning", "button.btn-info",
    ])
    if not changed:
        try:
            await page.evaluate("""
                const btns = Array.from(document.querySelectorAll('button'));
                const ch = btns.find(b => b.textContent.trim().toLowerCase().includes('change')
                                      || b.className.includes('warning')
                                      || b.className.includes('info'));
                if (ch) ch.click();
            """)
        except Exception:
            pass

    await page.wait_for_timeout(2_000)
    await _dismiss_security_verification(page)
    await _snap(page, "mailticking-after-change", snap)

    # Read the updated Gmail address
    email: Optional[str] = None
    for sel in ["input[readonly]", "input[type='text']", "#email", "input[value*='@gmail']"]:
        try:
            el = await page.query_selector(sel)
            if el:
                val = await el.input_value()
                if val and "@gmail.com" in val and len(val) > len("@gmail.com") + 3:
                    email = val.strip()
                    break
        except Exception:
            pass

    if not email:
        try:
            val = await page.evaluate("""() => {
                const inputs = Array.from(document.querySelectorAll('input'));
                for (const i of inputs) {
                    if (i.value && i.value.includes('@gmail.com')) return i.value;
                }
                return null;
            }""")
            if val and len(val) > len("@gmail.com") + 3:
                email = val.strip()
        except Exception:
            pass

    if not email:
        try:
            text = await page.inner_text("body")
        except Exception:
            text = await page.content()
        matches = re.findall(r'[a-zA-Z0-9][a-zA-Z0-9._%\-]*@gmail\.com', text)
        for m in matches:
            local_part = m.split("@")[0]
            if len(local_part) >= 4 and "example" not in m:
                email = m
                break

    if not email:
        await _snap(page, "mailticking-no-email", snap)
        raise RuntimeError("mailticking: could not read Gmail address after Change")

    print(f"[synthesia/mail] got email: {email}")
    await progress(f"📧 Got email: `{email}`")

    # Click Activate
    activated = await _click_first(page, [
        "button:has-text('Activate')", "input[value='Activate']",
        "a:has-text('Activate')", "button[class*='activate' i]", "button.btn-success",
    ])
    if not activated:
        try:
            await page.evaluate("""
                const btns = Array.from(document.querySelectorAll('button'));
                const act = btns.find(b => b.textContent.trim().toLowerCase().includes('activate'));
                if (act) act.click();
            """)
        except Exception:
            pass

    await page.wait_for_timeout(2_000)
    await _snap(page, "mailticking-activated", snap)
    return email


# ── Step 2: register on Synthesia ─────────────────────────────────────────────

async def _register_synthesia(
    page: Page, email: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress("🎬 Opening Synthesia registration…")
    await page.goto(_SYNTHESIA_WELCOME, wait_until="domcontentloaded", timeout=40_000)
    await _human_delay(3000, 5000)
    await _snap(page, "synthesia-welcome", snap)

    # The welcome page shows the email input directly in a modal —
    # no "Work Email" button needed, just type into the field.
    await progress(f"📧 Typing email: `{email}`")
    email_typed = await _human_type(page, "input[type='email']", email, timeout=8000)
    if not email_typed:
        # Fallbacks: name/placeholder selectors
        for sel in ["input[name='email']", "input[placeholder*='email' i]",
                    "input[autocomplete='email']"]:
            email_typed = await _human_type(page, sel, email, timeout=3000)
            if email_typed:
                break
    if not email_typed:
        # Last resort: JS inject + React synthetic event
        print("[synthesia] WARNING: email field not found — JS inject")
        await page.evaluate(f"""
            const inp = document.querySelector('input[type="email"], input[placeholder*="email" i]');
            if (inp) {{
                inp.value = {repr(email)};
                inp.dispatchEvent(new Event('input',  {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        """)

    await _human_delay(400, 800)
    await _snap(page, "synthesia-email-typed", snap)

    # Click "Continue with work email" (button copy has varied between
    # "Continue with work email" and "Continue with email" — try both).
    cont = await _human_move_and_click(page, "button:has-text('Continue with work email')", timeout=5000)
    if not cont:
        cont = await _human_move_and_click(page, "button:has-text('Continue with email')", timeout=3000)
    if not cont:
        cont = await _human_move_and_click(page, "button[type='submit']", timeout=3000)
    if not cont:
        await page.keyboard.press("Enter")

    # Wait for the DOM to actually move off the email screen instead of a
    # blind fixed delay — avoids snapping/acting on a stale screen.
    try:
        await page.wait_for_selector(
            "input[placeholder*='first name' i], input[name='firstName'], "
            "input[name='first_name'], input[type='password']",
            timeout=10_000, state="visible",
        )
    except Exception:
        pass
    await _human_delay(600, 1200)
    await _snap(page, "synthesia-after-email-continue", snap)

    # Next screen is usually "You're setting up a new account" — first name +
    # last name + email (email pre-filled/read-only). Some flows skip straight
    # to the password screen, so detect which one actually loaded.
    has_name_fields = await page.query_selector(
        "input[placeholder*='first name' i], input[name='firstName'], input[name='first_name']"
    ) is not None

    if has_name_fields:
        await progress("📝 Entering name…")
        first_typed = await _human_type(
            page,
            "input[placeholder*='first name' i], input[name='firstName'], "
            "input[name='first_name'], input[autocomplete='given-name']",
            _FIRST_NAME, timeout=6000,
        )
        if not first_typed:
            print("[synthesia] WARNING: first name field not found — JS inject")
            await page.evaluate(f"""
                const inp = document.querySelector('input[placeholder*="first name" i], input[name="firstName"]');
                if (inp) {{
                    inp.value = {repr(_FIRST_NAME)};
                    inp.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            """)

        last_typed = await _human_type(
            page,
            "input[placeholder*='last name' i], input[name='lastName'], "
            "input[name='last_name'], input[autocomplete='family-name']",
            _LAST_NAME, timeout=6000,
        )
        if not last_typed:
            print("[synthesia] WARNING: last name field not found — JS inject")
            await page.evaluate(f"""
                const inp = document.querySelector('input[placeholder*="last name" i], input[name="lastName"]');
                if (inp) {{
                    inp.value = {repr(_LAST_NAME)};
                    inp.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            """)

        await _human_delay(400, 800)
        await _snap(page, "synthesia-name-typed", snap)

        name_submitted = await _human_move_and_click(page, "button:has-text('Continue')", timeout=4000)
        if not name_submitted:
            name_submitted = await _human_move_and_click(page, "button[type='submit']", timeout=3000)
        if not name_submitted:
            await page.keyboard.press("Enter")

        try:
            await page.wait_for_selector("input[type='password']", timeout=10_000, state="visible")
        except Exception:
            pass
        await _human_delay(600, 1200)
        await _snap(page, "synthesia-after-name-continue", snap)

    # Password screen (email is pre-filled and read-only)
    await progress("🔑 Entering password…")
    pw_typed = await _human_type(page, "input[type='password']", _PASSWORD, timeout=8000)
    if not pw_typed:
        pw_typed = await _human_type(page, "input[name='password'], input[autocomplete='new-password'], input[placeholder*='password' i]", _PASSWORD, timeout=4000)
    if not pw_typed:
        print("[synthesia] WARNING: password field not found — JS inject")
        await page.evaluate(f"""
            const inp = document.querySelector('input[type="password"]');
            if (inp) {{
                inp.value = {repr(_PASSWORD)};
                inp.dispatchEvent(new Event('input',  {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        """)

    await _human_delay(500, 900)
    await _snap(page, "synthesia-password-typed", snap)

    # Click Continue
    submitted = await _human_move_and_click(page, "button:has-text('Continue')", timeout=4000)
    if not submitted:
        submitted = await _human_move_and_click(page, "button[type='submit']", timeout=3000)
    if not submitted:
        await page.keyboard.press("Enter")

    await _human_delay(3000, 4500)
    await _snap(page, "synthesia-form-submitted", snap)
    await progress("📬 Registration submitted — waiting for verification email…")


# ── Step 3: poll mailticking for Synthesia verification code ──────────────────

async def _get_synthesia_code(
    page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> str:
    await progress("📬 Polling inbox for Synthesia verification code…")
    max_attempts = 48  # ~8 minutes

    for attempt in range(max_attempts):
        await _dismiss_security_verification(page)
        await _dismiss_no_email_dialog(page)

        # Click Refresh
        await _click_first(page, [
            "button:has-text('Refresh')",
            "[class*='refresh']:not(input)",
            "button[title*='refresh' i]",
            "[aria-label*='refresh' i]",
        ], timeout=2000)
        await page.wait_for_timeout(4_000)
        await _dismiss_security_verification(page)
        await _dismiss_no_email_dialog(page)
        await _snap(page, f"mailticking-inbox-{attempt}", snap)

        # Find and open the Synthesia email
        email_clicked = False
        for row_sel in ["tr", "li", ".email-item", ".message", "[class*='mail-row']", "tbody tr"]:
            rows = await page.query_selector_all(row_sel)
            for row in rows:
                try:
                    txt = (await row.inner_text()).lower()
                    if ("synthesia" in txt or "verification" in txt
                            or "verify" in txt or "confirm" in txt):
                        opened = False
                        for btn_sel in [
                            "button:has-text('Check email')",
                            "button:has-text('Check emails')",
                            "button:has-text('View')",
                            "button:has-text('Open')",
                            "a:has-text('View')",
                            "button", "a",
                        ]:
                            try:
                                btn = await row.query_selector(btn_sel)
                                if btn:
                                    await btn.click()
                                    opened = True
                                    print(f"[synthesia/mail] clicked '{btn_sel}' in row: {txt[:60]}")
                                    break
                            except Exception:
                                pass
                        if not opened:
                            await row.click()
                        email_clicked = True
                        await page.wait_for_timeout(3_000)
                        popped = await _dismiss_no_email_dialog(page)
                        if popped:
                            email_clicked = False
                        break
                except Exception:
                    pass
            if email_clicked:
                break

        await _snap(page, f"mailticking-email-body-{attempt}", snap)

        if not email_clicked:
            await progress(f"📬 Waiting for Synthesia email… ({(attempt + 1) * 10}s)")
            await page.wait_for_timeout(5_000)
            continue

        # Read code from all frames (Synthesia email may be in an iframe)
        text_parts: list[str] = []
        for frame in page.frames:
            try:
                frame_text = await frame.inner_text("body")
                if frame_text:
                    text_parts.append(frame_text)
            except Exception:
                pass
        text = "\n".join(text_parts) or await page.inner_text("body")

        patterns = [
            r'verification code[:\s]+(\d{6})',
            r'confirm.*?code[:\s]+(\d{6})',
            r'your code[:\s]+(\d{6})',
            r'code[:\s]is[:\s]*(\d{6})',
            r'code[:\s]+(\d{6})',
        ]
        code = None
        for pattern in patterns:
            found = re.findall(pattern, text, re.IGNORECASE)
            if found:
                code = found[0]
                break

        if not code:
            found = re.findall(r'\b(\d{6})\b', text)
            if found:
                code = found[0]

        if code:
            print(f"[synthesia/mail] code found: {code}")
            await _snap(page, "mailticking-code-found", snap)
            await progress(f"✅ Got verification code: `{code}`")
            return code

        print(f"[synthesia/mail] attempt {attempt + 1}/{max_attempts} — email opened but no code yet")
        await progress(f"📬 Waiting for code… ({(attempt + 1) * 10}s)")
        await page.wait_for_timeout(5_000)

    await _snap(page, "mailticking-no-code", snap)
    raise RuntimeError("mailticking: Synthesia verification code never arrived after ~8 minutes")


# ── Step 4: enter verification code on Synthesia ──────────────────────────────

async def _enter_verification_code(
    page: Page, code: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress(f"🔑 Entering verification code…")

    # Synthesia may render individual digit input boxes or a single field
    # Try individual OTP boxes first
    otp_inputs = await page.query_selector_all(
        "input[maxlength='1'], input[data-index], [class*='otp'] input, [class*='code'] input[type='text']"
    )
    if len(otp_inputs) >= 6:
        for i, digit in enumerate(code[:6]):
            try:
                await otp_inputs[i].click()
                await otp_inputs[i].fill(digit)
                await page.wait_for_timeout(100)
            except Exception:
                pass
        print(f"[synthesia] typed code into {len(otp_inputs)} OTP boxes")
    else:
        # Single input field — type digit by digit
        filled = await _fill_first(page, [
            "input[name='code']", "input[name='otp']", "input[name='verificationCode']",
            "input[placeholder*='code' i]", "input[placeholder*='verification' i]",
            "input[type='number']", "input[type='text']",
        ], code)
        if filled:
            print(f"[synthesia] typed code into single field")
        else:
            # Try typing it as keystrokes
            try:
                await page.keyboard.type(code, delay=80)
                print(f"[synthesia] typed code via keyboard")
            except Exception:
                print(f"[synthesia] WARNING: could not type verification code")

    await page.wait_for_timeout(800)
    await _snap(page, "synthesia-code-entered", snap)

    # Submit
    submitted = await _click_first(page, [
        "button:has-text('Verify')",
        "button:has-text('Confirm')",
        "button:has-text('Continue')",
        "button:has-text('Submit')",
        "button[type='submit']",
    ], timeout=3000)
    if not submitted:
        await page.keyboard.press("Enter")

    await page.wait_for_timeout(4_000)
    await _snap(page, "synthesia-code-submitted", snap)


# ── Step 5: select Free plan ──────────────────────────────────────────────────

async def _select_free_plan(
    page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress("🆓 Selecting Free plan…")
    await page.wait_for_timeout(3_000)
    await _snap(page, "synthesia-plan-page", snap)

    async def _still_on_plan_page() -> bool:
        try:
            body = (await page.inner_text("body")).lower()
            return "plan" in body and "no credit card required" in body
        except Exception:
            return False

    if not await _still_on_plan_page():
        # Already past the plan gate for this account — nothing to click.
        await _snap(page, "synthesia-free-selected", snap)
        return

    # Every tier's button now just reads "Get started" — text matching on
    # "free"/"start for free"/etc. never matches. The Free card is the only
    # one with "No credit card required" directly above its button, so use
    # that as a precise, wording-independent anchor.
    selected = await _click_first(page, [
        "button:below(:text('No credit card required'))",
    ], timeout=5000)

    if not selected:
        try:
            selected = await page.evaluate("""
                () => {
                    const cards = Array.from(document.querySelectorAll('*')).filter(el => {
                        if (el.children.length === 0) return false;
                        const txt = el.textContent;
                        return txt.includes('Free') && txt.includes('No credit card required') && txt.length < 2000;
                    });
                    cards.sort((a, b) => a.textContent.length - b.textContent.length);
                    for (const card of cards) {
                        const btn = card.querySelector('button');
                        if (btn) { btn.click(); return true; }
                    }
                    return false;
                }
            """)
        except Exception:
            selected = False

    if not selected:
        # Older copy, in case Synthesia reverts the button wording.
        selected = await _click_first(page, [
            "button:has-text('Get started for free')",
            "button:has-text('Start for free')",
            "button:has-text('Continue with Free')",
            "button:has-text('Choose Free')",
            "button:has-text('Select Free')",
        ], timeout=3000)

    if not selected:
        # Last resort: Free is always the first/leftmost card, so its
        # "Get started" button is the first one in DOM order.
        try:
            selected = await page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const gs = btns.filter(b => b.textContent.trim().toLowerCase() === 'get started');
                    if (gs.length) { gs[0].click(); return true; }
                    return false;
                }
            """)
        except Exception:
            selected = False

    if not selected:
        print("[synthesia] WARNING: could not find a Free-plan button to click")

    # Verify we actually left the plan-gate screen instead of sleeping and
    # hoping — a missed click can just scroll the page and look fine.
    left_plan_page = False
    for _ in range(10):  # up to 10 × 1s = 10s
        await page.wait_for_timeout(1_000)
        if not await _still_on_plan_page():
            left_plan_page = True
            break

    await _snap(page, "synthesia-free-selected", snap)

    if not left_plan_page:
        print("[synthesia] WARNING: still on the plan page after clicking Free — retrying once")
        if await _click_first(page, ["button:below(:text('No credit card required'))"], timeout=4000):
            for _ in range(10):
                await page.wait_for_timeout(1_000)
                if not await _still_on_plan_page():
                    left_plan_page = True
                    break
        await _snap(page, "synthesia-free-selected-retry", snap)

    if not left_plan_page:
        print("[synthesia] WARNING: never confirmed leaving the plan page — continuing anyway")


# ── Step 6: complete onboarding ───────────────────────────────────────────────

async def _complete_onboarding(
    page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress("📋 Completing onboarding…")

    # Go through up to 10 onboarding screens
    for step in range(10):
        await page.wait_for_timeout(2_000)
        await _snap(page, f"synthesia-onboarding-{step}", snap)

        body = await page.inner_text("body")
        body_lower = body.lower()

        # Detect skip-able screens
        if "invite" in body_lower and ("teammate" in body_lower or "team" in body_lower):
            skipped = await _click_first(page, [
                "button:has-text('Skip')", "a:has-text('Skip')",
                "button:has-text('skip')",
            ], timeout=3000)
            if skipped:
                print(f"[synthesia] skipped teammates invite screen")
                continue

        if "company website" in body_lower or "website" in body_lower:
            skipped = await _click_first(page, [
                "button:has-text('Skip')", "a:has-text('Skip')",
            ], timeout=3000)
            if skipped:
                print(f"[synthesia] skipped company website screen")
                continue

        # Check if we've reached the dashboard
        if ("dashboard" in body_lower or "playground" in body_lower
                or "create" in body_lower or "template" in body_lower
                or "new video" in body_lower):
            print(f"[synthesia] onboarding complete — reached dashboard")
            break

        # Try clicking any visible radio/option button (first one found)
        option_clicked = await _click_first(page, [
            "input[type='radio'] + label",
            "label:has-text('')",
            "[role='radio']",
            "[class*='option']:first-child",
            "[class*='choice']:first-child",
            "li:first-child button",
            "li:first-child",
        ], timeout=2000)

        if not option_clicked:
            # Try JS clicking first list item / option
            try:
                await page.evaluate("""
                    const opts = document.querySelectorAll('[role="radio"], [class*="option"], [class*="choice"]');
                    if (opts.length) opts[0].click();
                """)
                option_clicked = True
            except Exception:
                pass

        # Click Next / Continue after selecting option
        await page.wait_for_timeout(500)
        next_clicked = await _click_first(page, [
            "button:has-text('Next')", "button:has-text('Continue')",
            "button[type='submit']", "button:has-text('Done')",
        ], timeout=2000)

        if not next_clicked and not option_clicked:
            # Nothing to click — probably done
            print(f"[synthesia] onboarding step {step}: nothing clickable, assuming done")
            break

    await _snap(page, "synthesia-onboarding-done", snap)


# ── Step 7: generate video in AI Playground ───────────────────────────────────

async def _generate_omni_video(
    page: Page, prompt: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> bytes:
    await progress("🎬 Opening AI Clips…")

    # Navigate straight to the My Media tab — the prompt bar (with the
    # settings/model/audio/generations controls) lives here now, not on the
    # old AI Playground page.
    await page.goto(
        "https://app.synthesia.io/#/ai-clips?tab=my-media",
        wait_until="domcontentloaded", timeout=30_000,
    )
    await page.wait_for_timeout(3_000)
    await _snap(page, "synthesia-my-media", snap)

    try:
        await page.wait_for_selector("textarea", timeout=15_000)
    except Exception:
        print("[synthesia] WARNING: prompt bar never appeared on My Media page")

    # ── Open the "Prompt settings" panel (sliders icon in the prompt bar) ──
    await progress("⚙️ Opening prompt settings…")
    settings_opened = await _click_first(page, [
        "[aria-label*='prompt settings' i]",
        "[aria-label*='settings' i]",
        "[title*='settings' i]",
        "button:has(svg[class*='slider' i])",
        "button:has(svg[class*='tune' i])",
        "button:has(svg[class*='adjust' i])",
        "[data-testid*='settings']",
    ], timeout=4000)

    if not settings_opened:
        # Heuristic fallback: the settings icon sits in the same icon row as
        # the prompt textarea, right before the send/up-arrow button.
        try:
            settings_opened = await page.evaluate("""
                () => {
                    const ta = document.querySelector('textarea');
                    if (!ta) return false;
                    const bar = ta.closest('form') || ta.parentElement?.parentElement || document.body;
                    const btns = Array.from(bar.querySelectorAll('button'));
                    if (btns.length < 2) return false;
                    btns[btns.length - 2].click();
                    return true;
                }
            """)
        except Exception:
            settings_opened = False

    await page.wait_for_timeout(1_000)
    await _snap(page, "synthesia-prompt-settings-opened", snap)

    if not settings_opened:
        print("[synthesia] WARNING: could not open Prompt settings panel — continuing with defaults")

    if settings_opened:
        # ── AI model → Gemini Omni ──────────────────────────────────────────
        await progress("🤖 Selecting Gemini Omni model…")
        model_opened = await _click_first(page, [
            "button:below(:text('AI model'))",
            "[role='combobox']:below(:text('AI model'))",
        ], timeout=3000)

        if not model_opened:
            try:
                model_opened = await page.evaluate("""
                    () => {
                        const label = Array.from(document.querySelectorAll('*'))
                            .find(el => el.children.length === 0 && el.textContent.trim() === 'AI model');
                        if (!label) return false;
                        let node = label.closest('div');
                        for (let i = 0; i < 4 && node; i++) {
                            const sib = node.nextElementSibling;
                            if (sib) { sib.click(); return true; }
                            node = node.parentElement;
                        }
                        return false;
                    }
                """)
            except Exception:
                model_opened = False

        await page.wait_for_timeout(800)
        await _snap(page, "synthesia-model-dropdown", snap)

        omni_selected = await _click_first(page, [
            "li:has-text('Gemini Omni')",
            "[role='option']:has-text('Gemini Omni')",
            "button:has-text('Gemini Omni')",
            "li:has-text('Google Omni')",
            "[role='option']:has-text('Google Omni')",
            "li:has-text('Omni')",
            "[role='option']:has-text('Omni')",
        ], timeout=3000)

        if not omni_selected:
            try:
                omni_selected = await page.evaluate("""
                    () => {
                        const els = Array.from(document.querySelectorAll('li, [role="option"], button, div'));
                        const o = els.find(e => e.children.length === 0 && e.textContent.toLowerCase().includes('omni'));
                        if (o) { o.click(); return true; }
                        return false;
                    }
                """)
            except Exception:
                omni_selected = False

        if not omni_selected:
            print("[synthesia] WARNING: could not select Gemini Omni model — leaving default model")

        await page.wait_for_timeout(800)
        await _snap(page, "synthesia-model-selected", snap)

        # ── Audio → starts muted, turn it ON ────────────────────────────────
        await progress("🔊 Turning audio on…")
        audio_toggled = await _click_first(page, [
            "button:below(:text('Audio'))",
            "[aria-label*='unmute' i]",
            "[aria-label*='audio' i]",
            "button:has(svg[class*='mute' i])",
            "button:has(svg[class*='volume' i])",
        ], timeout=3000)

        if not audio_toggled:
            try:
                audio_toggled = await page.evaluate("""
                    () => {
                        const label = Array.from(document.querySelectorAll('*'))
                            .find(el => el.children.length === 0 && el.textContent.trim() === 'Audio');
                        if (!label) return false;
                        let node = label.closest('div');
                        for (let i = 0; i < 4 && node; i++) {
                            const sib = node.nextElementSibling;
                            const btn = sib && (sib.matches('button') ? sib : sib.querySelector('button'));
                            if (btn) { btn.click(); return true; }
                            node = node.parentElement;
                        }
                        return false;
                    }
                """)
            except Exception:
                audio_toggled = False

        if not audio_toggled:
            print("[synthesia] WARNING: could not toggle audio on")

        await page.wait_for_timeout(500)
        await _snap(page, "synthesia-audio-on", snap)

        # ── Generations → 1 (defaults to 1, but set explicitly) ─────────────
        gen_opened = await _click_first(page, [
            "button:below(:text('Generations'))",
            "[aria-label*='generations' i]",
        ], timeout=2000)
        if gen_opened:
            await page.wait_for_timeout(500)
            picked_one = await _click_first(page, [
                "li:has-text('1')", "[role='option']:has-text('1')",
            ], timeout=1500)
            if not picked_one:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
        await page.wait_for_timeout(400)

        # ── Close the Prompt settings panel with the X button ───────────────
        closed = await _click_first(page, [
            "[aria-label='Close']",
            "[aria-label*='close' i]",
            "button:has-text('×')",
        ], timeout=2000)
        if not closed:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        await page.wait_for_timeout(800)
        await _snap(page, "synthesia-settings-closed", snap)

    # ── Enter the prompt ─────────────────────────────────────────────────────
    await progress("✍️ Entering prompt…")
    prompt_filled = await _fill_first(page, [
        "textarea[placeholder*='describe' i]",
        "textarea",
    ], prompt)

    if not prompt_filled:
        print(f"[synthesia] WARNING: could not fill prompt field — trying keyboard type")
        try:
            await page.keyboard.type(prompt, delay=30)
        except Exception:
            pass

    await page.wait_for_timeout(500)
    await _snap(page, "synthesia-prompt-entered", snap)

    # ── Submit via the up-arrow (send) button ───────────────────────────────
    await progress("🚀 Generating video…")
    submitted = await _click_first(page, [
        "button[aria-label*='send' i]",
        "button[aria-label*='generate' i]",
        "button[aria-label*='submit' i]",
        "button:has(svg[class*='arrow-up' i])",
    ], timeout=4000)

    if not submitted:
        try:
            submitted = await page.evaluate("""
                () => {
                    const ta = document.querySelector('textarea');
                    if (!ta) return false;
                    const bar = ta.closest('form') || ta.parentElement?.parentElement || document.body;
                    const btns = Array.from(bar.querySelectorAll('button'));
                    if (!btns.length) return false;
                    btns[btns.length - 1].click();
                    return true;
                }
            """)
        except Exception:
            submitted = False

    if not submitted:
        print("[synthesia] WARNING: could not click the send/up-arrow button — falling back to Enter key")
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    await page.wait_for_timeout(1_500)
    await _snap(page, "synthesia-submitted", snap)

    # Poll for completion — the new item shows a "Generating…" placeholder
    # card until the render finishes, up to 5 minutes.
    await progress("⏳ Rendering video…")
    video_ready    = False
    saw_generating = False
    stable_polls   = 0
    for poll in range(60):  # 60 × 5s = 5 minutes
        await page.wait_for_timeout(5_000)
        try:
            body_lower = (await page.inner_text("body")).lower()

            if "generating" in body_lower:
                saw_generating = True
                stable_polls = 0
                pct_match = re.search(r'(\d{1,3})\s*%', body_lower)
                if pct_match:
                    await progress(f"⏳ Rendering… {pct_match.group(1)}%")
                elif poll % 6 == 0:
                    await progress(f"⏳ Rendering… ({(poll + 1) * 5}s)")
                continue

            if not saw_generating:
                # Haven't seen the "Generating…" placeholder yet — too early
                # to trust it's done, keep waiting.
                continue

            stable_polls += 1
            has_download = await page.query_selector(
                "[aria-label*='download' i], [title*='download' i], a[download]"
            )
            if has_download or stable_polls >= 2:
                video_ready = True
                print(f"[synthesia] video ready after ~{(poll + 1) * 5}s")
                break
        except Exception:
            pass

    await _snap(page, "synthesia-generation-done", snap)

    if not video_ready:
        raise RuntimeError("Synthesia: video never finished rendering after 5 minutes")

    # Grab the video URL directly from the page — no download needed
    await progress("🔗 Getting video URL…")

    video_url = await page.evaluate("""() => {
        const v = document.querySelector('video[src]');
        if (v && v.src && !v.src.startsWith('blob:')) return v.src;
        const s = document.querySelector('video source[src]');
        if (s && s.src && !s.src.startsWith('blob:')) return s.src;
        // Check for download link with an mp4/video URL
        for (const a of document.querySelectorAll('a[href]')) {
            if (/\\.mp4|/video/i.test(a.href) && a.href.startsWith('http')) return a.href;
        }
        return null;
    }""")

    if video_url:
        print(f"[synthesia] ✅ video URL from page: {video_url[:80]}")
        await progress("✅ Done!")
        return video_url

    # Fallback: trigger download and capture the URL from the download event
    print("[synthesia] no video src URL found — falling back to download event")
    await progress("⬇️ Downloading video…")
    try:
        async with page.expect_download(timeout=60_000) as dl_info:
            clicked = await _click_first(page, [
                "[aria-label*='download' i]",
                "[title*='download' i]",
                "[data-testid*='download']",
                "button:has-text('Download')",
                "a:has-text('Download')",
                "a[download]",
            ], timeout=5000)
            if not clicked:
                await page.evaluate("""
                    const els = Array.from(document.querySelectorAll('button, a'));
                    const d = els.find(e =>
                        e.textContent.toLowerCase().includes('download')
                        || (e.getAttribute('aria-label') || '').toLowerCase().includes('download')
                        || (e.getAttribute('title') || '').toLowerCase().includes('download')
                    );
                    if (d) d.click();
                """)
        dl = await dl_info.value
        dl_url = dl.url
        if dl_url and not dl_url.startswith("blob:"):
            print(f"[synthesia] ✅ video URL from download event: {dl_url[:80]}")
            await progress("✅ Done!")
            return dl_url
        # blob: — read the file bytes as last resort
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name
        await dl.save_as(tmp_path)
        data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        print(f"[synthesia] downloaded {len(data) // 1024} KB (blob fallback)")
        await progress(f"✅ Video ready ({len(data) // 1024 // 1024 or 1} MB)")
        return data
    except Exception as e:
        raise RuntimeError(f"Synthesia: could not obtain video URL or bytes: {e}")


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_synthesia_video(
    prompt: str,
    progress_cb: ProgressCB,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    """
    Full end-to-end flow: fresh Synthesia account → generate → download → return bytes.
    Raises RuntimeError on any unrecoverable failure.
    """
    kw = dict(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled"],
    )
    if _CHROMIUM:
        kw["executable_path"] = _CHROMIUM

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kw)
        ctx: BrowserContext = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )

        # Inject stealth patches before every page load — hides webdriver flag,
        # fakes plugins/languages, patches chrome runtime, etc.
        await ctx.add_init_script(_STEALTH_JS)

        # Two pages: one stays on mailticking, one navigates Synthesia
        mail_page  = await ctx.new_page()
        synth_page = await ctx.new_page()

        try:
            # 1. Get temp email
            email = await _get_temp_email(mail_page, progress_cb, screenshot_cb)

            # 2. Register on Synthesia (synth_page)
            await _register_synthesia(synth_page, email, progress_cb, screenshot_cb)

            # 3. Get verification code (mail_page)
            code = await _get_synthesia_code(mail_page, progress_cb, screenshot_cb)

            # 4. Enter code on Synthesia (synth_page — already on verification page)
            await _enter_verification_code(synth_page, code, progress_cb, screenshot_cb)

            # 5. Select Free plan
            await _select_free_plan(synth_page, progress_cb, screenshot_cb)

            # 6. Complete onboarding
            await _complete_onboarding(synth_page, progress_cb, screenshot_cb)

            # 7. Generate video
            video_bytes = await _generate_omni_video(synth_page, prompt, progress_cb, screenshot_cb)

            return video_bytes

        finally:
            await browser.close()
