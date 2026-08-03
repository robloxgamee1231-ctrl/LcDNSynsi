"""
quickframe_bot.py — QuickFrame AI browser automation via Playwright

Creates a brand-new account for every request:
  1. Gets a temp Gmail alias from mailticking.com
  2. Logs in to QuickFrame (Auth0 OTP — no password, just email + 6-digit code)
  3. Navigates to the AI video generator
  4. Enters the prompt and sets the duration slider
  5. Clicks Generate and waits for completion
  6. Downloads the finished video and returns the bytes

Usage:
  video_bytes = await generate_quickframe_video(
      prompt, duration=10, progress_cb=..., screenshot_cb=...
  )
"""

import asyncio
import re
import random
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional

# ── URLs ───────────────────────────────────────────────────────────────────────

_QF_LOGIN_URL    = (
    "https://login.quickframe.com/u/login/identifier"
    "?state=hKFo2SBwaEFYNDF4MjU1Wi1hX19yVDdkOXdMYUtfZl9sRlVCeKFur3VuaXZlcnNhbC1sb2dpbq"
    "N0aWTZIE4wOTV1S1g4QlliSHIyTnV5TU5rSUNRdVJrMlFhNFFuo2NpZNkgMTNQMDkyTU1TTldOZ3pFVn"
    "BPVjVmTFJVbVd1VW44cFI"
)
_QF_GENERATOR_URL = "https://ai.quickframe.com/tools/quickstart/add-elements-to-video"
_MAILTICKING_URL  = "https://mailticking.com/"

# ── Browser binary ─────────────────────────────────────────────────────────────

_CHROMIUM_BIN = (
    "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
)

# ── Types ──────────────────────────────────────────────────────────────────────

ProgressCB   = Optional[Callable[[str], Awaitable[None]]]
ScreenshotCB = Optional[Callable[[str, bytes], Awaitable[None]]]

# ── Stealth JS ─────────────────────────────────────────────────────────────────

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client',       filename: 'internal-nacl-plugin' },
        ];
        arr.item = (i) => arr[i];
        arr.refresh = () => {};
        Object.setPrototypeOf(arr, PluginArray.prototype);
        return arr;
    }
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false },
        runtime: {},
        loadTimes: function() { return {}; },
        csi: function() { return {}; },
    };
}
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _cb(progress_cb: ProgressCB, msg: str) -> None:
    if progress_cb:
        try:
            await progress_cb(msg)
        except Exception:
            pass


async def _snap(page, label: str, screenshot_cb: ScreenshotCB) -> None:
    if not screenshot_cb:
        return
    try:
        img = await page.screenshot(type="jpeg", quality=65, full_page=False)
        await screenshot_cb(f"[quickframe] {label}", img)
    except Exception as e:
        print(f"[quickframe] screenshot({label}): {e}")


async def _human_pause(min_ms: int = 300, max_ms: int = 900) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _new_browser(pw):
    """Launch a stealth Chromium browser and return (browser, context)."""
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
        ],
    )
    _ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    )
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=_ua,
        accept_downloads=True,
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "sec-ch-ua": '"Chromium";v="138", "Google Chrome";v="138", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    try:
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(ctx)
        print("[quickframe] playwright-stealth applied ✓")
    except Exception:
        try:
            from playwright_stealth import stealth
            s = stealth()
            if isinstance(s, str):
                await ctx.add_init_script(s)
        except Exception:
            await ctx.add_init_script(_STEALTH_JS)
    return browser, ctx


# ── Mailticking helpers ────────────────────────────────────────────────────────

_GOOD_DOMAINS    = {"gmail.com", "googlemail.com"}
_EMAIL_RE        = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', re.IGNORECASE)
_EMAIL_PLACEHOLDERS = {"user@domain.com", "example@gmail.com", "test@gmail.com",
                       "your@email.com", "abc@domain.com"}


async def _read_email_from_page(page) -> str:
    for sel in ["input[readonly]", "input[id*='email' i]", "input[name*='email' i]",
                "input[class*='email' i]", "input[type='email']", "input[type='text']"]:
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(count):
                val = (await els.nth(i).get_attribute("value", timeout=1_500) or "").strip()
                m = _EMAIL_RE.search(val)
                if m and m.group().lower() not in _EMAIL_PLACEHOLDERS:
                    return m.group()
        except Exception:
            continue
    try:
        body = await page.inner_text("body", timeout=5_000)
        for m in _EMAIL_RE.finditer(body):
            c = m.group()
            if c.lower() not in _EMAIL_PLACEHOLDERS:
                return c
    except Exception:
        pass
    return ""


async def _get_temp_email(page, screenshot_cb: ScreenshotCB = None) -> str:
    """Open mailticking.com, uncheck googlemail.com + domain.com + plus alias,
    click the refresh arrows, click Activate, return the plain @gmail.com address."""
    print("[quickframe/mail] navigating to mailticking.com…")
    await page.goto(_MAILTICKING_URL, wait_until="domcontentloaded", timeout=40_000)
    await page.wait_for_timeout(2_500)
    await _snap(page, "mailticking-loaded", screenshot_cb)

    raw_email = await _read_email_from_page(page)
    print(f"[quickframe/mail] raw email: {raw_email!r}")
    if not raw_email or "@" not in raw_email:
        raise RuntimeError("mailticking.com: could not read email address")

    username = raw_email.split("@")[0].strip().lower()

    # ── Step 1: Uncheck googlemail.com, domain.com, and +d alias ─────────────
    # Keep only the plain abc@gmail.com checkbox checked.
    await page.evaluate("""() => {
        const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        for (const cb of boxes) {
            const container = cb.closest('li, label, span, div') || cb.parentElement;
            const text = (container ? container.textContent : '').toLowerCase();
            const shouldUncheck = (
                text.includes('googlemail.com') ||
                text.includes('domain.com') ||
                (text.includes('+') && text.includes('gmail'))
            );
            if (shouldUncheck && cb.checked) cb.click();
        }
    }""")
    print("[quickframe/mail] unchecked googlemail.com / domain.com / +alias")
    await page.wait_for_timeout(400)
    await _snap(page, "mailticking-unchecked", screenshot_cb)

    # ── Step 2: Click the inverted/refresh arrows (left sidebar) ─────────────
    # The sidebar has a circular arrows / refresh icon (the 'C'-looking button).
    refresh_clicked = False
    for sel in [
        # By aria-label
        "button[aria-label*='refresh' i]",
        "a[aria-label*='refresh' i]",
        # By title
        "button[title*='refresh' i]",
        "a[title*='refresh' i]",
        # By class or icon text
        ".refresh", "[class*='refresh']",
        # SVG path containing circular arrows — try the sidebar links/buttons
        "aside button", "aside a",
        ".sidebar button", ".sidebar a",
        # Generic: any button that looks like a refresh icon
        "button svg", "a svg",
    ]:
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(count):
                el = els.nth(i)
                if not await el.is_visible(timeout=1_000):
                    continue
                label = (
                    (await el.get_attribute("aria-label") or "") +
                    (await el.get_attribute("title") or "") +
                    (await el.inner_text(timeout=500) or "")
                ).lower()
                if any(kw in label for kw in ("refresh", "reload", "renew", "rotate")):
                    await el.click(timeout=3_000)
                    refresh_clicked = True
                    print(f"[quickframe/mail] refresh arrows clicked via {sel!r} (label={label!r})")
                    break
            if refresh_clicked:
                break
        except Exception:
            continue

    # JS fallback: look for the circular-arrow SVG or a refresh-looking button
    if not refresh_clicked:
        try:
            clicked = await page.evaluate("""() => {
                // Try buttons/links in the left sidebar area
                const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const el of candidates) {
                    const t = (el.getAttribute('aria-label') || el.getAttribute('title') ||
                                el.textContent || '').toLowerCase();
                    if (t.includes('refresh') || t.includes('reload') || t.includes('renew')) {
                        el.click(); return t;
                    }
                }
                // Try clicking the green/left-side icon strip buttons (position-based)
                const sidebarBtns = Array.from(document.querySelectorAll(
                    '.sidebar button, aside button, .left-panel button, [class*="sidebar"] button'
                ));
                if (sidebarBtns.length > 0) {
                    // Refresh is typically the first or second sidebar button
                    sidebarBtns[0].click(); return 'sidebar-btn-0';
                }
                return null;
            }""")
            if clicked:
                refresh_clicked = True
                print(f"[quickframe/mail] refresh arrows JS fallback: {clicked!r}")
        except Exception:
            pass

    if not refresh_clicked:
        print("[quickframe/mail] ⚠️ could not find refresh arrows — skipping")
    await page.wait_for_timeout(800)
    await _snap(page, "mailticking-after-refresh", screenshot_cb)

    # ── Step 3: Click Activate ────────────────────────────────────────────────
    activated = False
    for sel in ["button:has-text('Activate')", "a:has-text('Activate')", "text=Activate",
                "input[value*='Activate' i]"]:
        try:
            await page.locator(sel).first.click(timeout=5_000)
            activated = True
            print(f"[quickframe/mail] Activate clicked via {sel!r}")
            break
        except Exception:
            continue
    if not activated:
        try:
            await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
                const act = els.find(b => (b.textContent || b.value || '').trim().toLowerCase().includes('activate'));
                if (act) act.click();
            }""")
        except Exception:
            pass

    await page.wait_for_timeout(1_500)
    # Use plain @gmail.com — that is the address sent to QuickFrame
    email = f"{username}@gmail.com"
    await _snap(page, f"mailticking-activated-{email}", screenshot_cb)
    print(f"[quickframe/mail] using email: {email!r}")
    return email


async def _poll_otp_code(mail_page, timeout_s: int = 300, screenshot_cb: ScreenshotCB = None) -> str:
    """Poll the mailticking inbox for a 6-digit OTP from QuickFrame/MNTN."""
    _OTP_RE = re.compile(r'\b(\d{6})\b')
    seen_bodies: set[str] = set()

    for attempt in range(timeout_s // 6):
        await asyncio.sleep(6)
        elapsed = (attempt + 1) * 6
        print(f"[quickframe/mail] OTP poll {attempt + 1} ({elapsed}s elapsed)")

        # Reload inbox (scroll to trigger inbox refresh)
        try:
            await mail_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        # Look for inbox rows
        for row_sel in [
            "tr:has-text('quickframe')", "tr:has-text('QuickFrame')",
            "tr:has-text('mountain')", "tr:has-text('Mountain')",
            "tr:has-text('verification')", "tr:has-text('code')",
            "tr", "li[class*='email']", "li[class*='message']",
            "div[class*='mail-item']", ".inbox-row", ".mail-row",
        ]:
            try:
                rows = mail_page.locator(row_sel)
                count = await rows.count()
                for i in range(count):
                    try:
                        row_text = (await rows.nth(i).inner_text(timeout=2_000)).lower()
                    except Exception:
                        continue
                    is_qf = any(kw in row_text for kw in (
                        "quickframe", "mountain", "verification", "code", "mntn", "noreply"
                    ))
                    if not is_qf or row_text in seen_bodies:
                        continue
                    seen_bodies.add(row_text)

                    print("[quickframe/mail] ✅ email row found — opening it")
                    await _snap(mail_page, "mail-qf-arrived", screenshot_cb)
                    try:
                        await rows.nth(i).click(timeout=3_000)
                    except Exception:
                        pass
                    await mail_page.wait_for_timeout(2_000)
                    await _snap(mail_page, "mail-qf-opened", screenshot_cb)

                    # Try iframe body first, then full page
                    body_text = ""
                    try:
                        iframe = mail_page.frame_locator("iframe").first
                        body_text = await iframe.locator("body").inner_text(timeout=4_000)
                    except Exception:
                        pass
                    if not body_text:
                        try:
                            body_text = await mail_page.inner_text("body", timeout=4_000)
                        except Exception:
                            pass

                    m = _OTP_RE.search(body_text)
                    if m:
                        code = m.group(1)
                        print(f"[quickframe/mail] ✅ OTP code: {code}")
                        return code

            except Exception as exc:
                print(f"[quickframe/mail] inbox scan error: {exc}")
                continue

        if elapsed % 30 == 0:
            print(f"[quickframe/mail] still waiting for OTP… ({elapsed}s)")

    raise RuntimeError("QuickFrame verification email did not arrive within ~5 minutes")


# ── Login flow ─────────────────────────────────────────────────────────────────

async def _login_quickframe(
    qf_page,
    email: str,
    mail_page,
    progress_cb: ProgressCB,
    screenshot_cb: ScreenshotCB,
) -> None:
    """Full Auth0 OTP login: go to ai.quickframe.com → click Login → enter email → get code → enter code."""
    await _cb(progress_cb, "🔑 Opening QuickFrame…")
    print(f"[quickframe] navigating to ai.quickframe.com with {email!r}")

    # ── Step 1: Land on the main app and click Login ─────────────────────────
    await qf_page.goto("https://ai.quickframe.com/", wait_until="domcontentloaded", timeout=40_000)
    await _human_pause(2_000, 3_500)
    await _snap(qf_page, "qf-homepage", screenshot_cb)

    # Click the Login button on the homepage
    login_clicked = False
    for sel in [
        "a:has-text('Log in')",
        "a:has-text('Login')",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "a[href*='login' i]",
        "[aria-label*='login' i]",
    ]:
        try:
            el = qf_page.locator(sel).first
            if await el.is_visible(timeout=3_000):
                await el.click(timeout=5_000)
                login_clicked = True
                print(f"[quickframe] Login button clicked via {sel!r}")
                break
        except Exception:
            continue

    if not login_clicked:
        # JS fallback
        try:
            result = await qf_page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('a, button'));
                for (const el of els) {
                    const t = (el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (t === 'log in' || t === 'login' || t === 'sign in') {
                        el.click(); return el.textContent.trim();
                    }
                }
                // Also try any link that contains /login
                const loginLink = document.querySelector('a[href*="login"]');
                if (loginLink) { loginLink.click(); return loginLink.href; }
                return null;
            }""")
            if result:
                login_clicked = True
                print(f"[quickframe] Login JS fallback: {result!r}")
        except Exception:
            pass

    if not login_clicked:
        # Fall back directly to the Auth0 URL
        print("[quickframe] ⚠️ Login button not found — falling back to direct Auth0 URL")
        await qf_page.goto(_QF_LOGIN_URL, wait_until="domcontentloaded", timeout=40_000)

    await _human_pause(2_500, 4_000)
    await _snap(qf_page, "qf-login-page", screenshot_cb)

    # ── Enter email ──────────────────────────────────────────────────────────
    email_sel = None
    for sel in [
        "input[name='username']",
        "input[type='email']",
        "input[name='email']",
        "input[id*='username' i]",
        "input[placeholder*='email' i]",
        "input[placeholder*='address' i]",
    ]:
        try:
            el = await qf_page.wait_for_selector(sel, timeout=5_000, state="visible")
            if el:
                email_sel = sel
                break
        except Exception:
            continue

    if not email_sel:
        await _snap(qf_page, "qf-no-email-field", screenshot_cb)
        raise RuntimeError("QuickFrame login: could not find email input")

    await qf_page.click(email_sel)
    await _human_pause(200, 400)
    await qf_page.fill(email_sel, email)
    await _human_pause(400, 700)
    await _snap(qf_page, "qf-email-typed", screenshot_cb)

    # ── Click Continue ───────────────────────────────────────────────────────
    clicked = await qf_page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        for (const b of btns) {
            const t = (b.innerText || b.textContent || '').trim().toLowerCase();
            if (t === 'continue' || t === 'next' || t === 'sign in' || t === 'send code') {
                b.click(); return b.innerText.trim();
            }
        }
        const form = document.querySelector('form');
        if (form) {
            const sub = form.querySelector('button[type="submit"], input[type="submit"]');
            if (sub) { sub.click(); return sub.innerText || sub.value || 'submit'; }
        }
        return null;
    }""")
    if not clicked:
        await qf_page.keyboard.press("Enter")
    print(f"[quickframe] Continue clicked: {clicked!r}")

    await _cb(progress_cb, "📬 Waiting for verification code…")
    await _human_pause(3_000, 5_000)
    await _snap(qf_page, "qf-after-continue", screenshot_cb)

    # ── Poll mailticking for the 6-digit code ────────────────────────────────
    code = await _poll_otp_code(mail_page, timeout_s=300, screenshot_cb=screenshot_cb)
    await _cb(progress_cb, f"🔢 Got verification code — entering it…")
    print(f"[quickframe] entering OTP code: {code}")

    # ── Enter the code ───────────────────────────────────────────────────────
    # Auth0 OTP screens vary: sometimes a single 6-char input, sometimes 6 separate boxes
    await _human_pause(1_000, 2_000)
    await _snap(qf_page, "qf-otp-screen", screenshot_cb)

    # Try single input first
    single_filled = False
    for sel in [
        "input[name='code']",
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric']",
        "input[type='text'][maxlength='6']",
        "input[type='number']",
        "input[placeholder*='code' i]",
        "input[aria-label*='code' i]",
    ]:
        try:
            el = await qf_page.wait_for_selector(sel, timeout=3_000, state="visible")
            if el:
                await el.click()
                await el.fill(code)
                single_filled = True
                print(f"[quickframe] OTP filled via single input: {sel!r}")
                break
        except Exception:
            continue

    # Fallback: 6 individual digit boxes
    if not single_filled:
        try:
            boxes = qf_page.locator("input[maxlength='1']")
            count = await boxes.count()
            if count >= 6:
                for i, ch in enumerate(code[:count]):
                    await boxes.nth(i).click()
                    await boxes.nth(i).fill(ch)
                    await _human_pause(80, 160)
                single_filled = True
                print("[quickframe] OTP filled via 6 individual boxes")
        except Exception:
            pass

    if not single_filled:
        # Last resort: focus the page and type the code
        await qf_page.keyboard.type(code, delay=120)
        print("[quickframe] OTP typed via keyboard")

    await _human_pause(500, 900)
    await _snap(qf_page, "qf-otp-entered", screenshot_cb)

    # ── Submit the code ──────────────────────────────────────────────────────
    submitted = await qf_page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        for (const b of btns) {
            const t = (b.innerText || b.textContent || '').trim().toLowerCase();
            if (t === 'continue' || t === 'sign in' || t === 'verify' || t === 'submit') {
                b.click(); return b.innerText.trim();
            }
        }
        const form = document.querySelector('form');
        if (form) {
            const sub = form.querySelector('button[type="submit"], input[type="submit"]');
            if (sub) { sub.click(); return sub.innerText || sub.value || 'submit'; }
        }
        return null;
    }""")
    if not submitted:
        await qf_page.keyboard.press("Enter")
    print(f"[quickframe] OTP submitted: {submitted!r}")

    await _cb(progress_cb, "✅ Logged in to QuickFrame!")
    await _human_pause(4_000, 6_000)
    await _snap(qf_page, "qf-after-otp", screenshot_cb)

    # Check we're no longer on the login domain
    current_url = qf_page.url
    print(f"[quickframe] post-login URL: {current_url}")
    if "login.quickframe.com" in current_url:
        await _snap(qf_page, "qf-still-on-login", screenshot_cb)
        raise RuntimeError("QuickFrame login failed — still on login page after OTP")


# ── Video generation ───────────────────────────────────────────────────────────

async def _generate_video(
    qf_page,
    prompt: str,
    duration: int,
    progress_cb: ProgressCB,
    screenshot_cb: ScreenshotCB,
) -> bytes:
    """Navigate to the generator, fill prompt, set duration, generate, download."""
    await _cb(progress_cb, "🎬 Opening video generator…")
    print(f"[quickframe] navigating to generator: {_QF_GENERATOR_URL}")

    await qf_page.goto(_QF_GENERATOR_URL, wait_until="domcontentloaded", timeout=40_000)
    await _human_pause(3_000, 5_000)
    await _snap(qf_page, "qf-generator-loaded", screenshot_cb)

    # ── Enter prompt ─────────────────────────────────────────────────────────
    await _cb(progress_cb, "✍️ Entering prompt…")
    prompt_sel = None
    for sel in [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea[placeholder*='enter' i]",
        "textarea",
        "div[contenteditable='true']",
        "input[placeholder*='prompt' i]",
    ]:
        try:
            el = await qf_page.wait_for_selector(sel, timeout=5_000, state="visible")
            if el:
                prompt_sel = sel
                break
        except Exception:
            continue

    if not prompt_sel:
        await _snap(qf_page, "qf-no-prompt-field", screenshot_cb)
        raise RuntimeError("QuickFrame generator: could not find prompt input")

    await qf_page.click(prompt_sel)
    await _human_pause(300, 500)
    await qf_page.fill(prompt_sel, prompt)
    await _human_pause(500, 800)
    await _snap(qf_page, "qf-prompt-typed", screenshot_cb)

    # ── Set duration slider ───────────────────────────────────────────────────
    await _cb(progress_cb, f"⏱️ Setting duration to {duration}s…")
    print(f"[quickframe] setting duration slider to {duration}s")

    # Try to find the range slider and set it to the target duration (1–15)
    slider_set = False
    for slider_sel in [
        "input[type='range']",
        "[role='slider']",
        ".duration-slider input",
        "input[min='1'][max='15']",
        "input[min][max]",
    ]:
        try:
            slider = qf_page.locator(slider_sel).first
            if not await slider.is_visible(timeout=3_000):
                continue
            # Get min/max to compute the target value
            min_val = float(await slider.get_attribute("min") or "1")
            max_val = float(await slider.get_attribute("max") or "15")
            target  = max(min_val, min(max_val, float(duration)))
            await slider.fill(str(int(target)))
            # Also dispatch input/change events so React picks it up
            await qf_page.evaluate(
                """([sel, val]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(el, val);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                [slider_sel, str(int(target))],
            )
            slider_set = True
            print(f"[quickframe] duration slider set to {int(target)} via {slider_sel!r}")
            break
        except Exception as e:
            print(f"[quickframe] slider {slider_sel!r} failed: {e}")
            continue

    if not slider_set:
        print("[quickframe] ⚠️ could not find duration slider — using default")

    await _human_pause(500, 800)
    await _snap(qf_page, "qf-duration-set", screenshot_cb)

    # ── Click Generate ────────────────────────────────────────────────────────
    await _cb(progress_cb, "🚀 Starting generation…")
    print("[quickframe] clicking Generate button")
    gen_clicked = False
    for sel in [
        "button:has-text('Generate')",
        "button[aria-label*='generate' i]",
        "button[type='submit']",
        "button:has-text('Create')",
        "button:has-text('Submit')",
    ]:
        try:
            btn = qf_page.locator(sel).first
            if await btn.is_visible(timeout=3_000):
                await btn.click(timeout=5_000)
                gen_clicked = True
                print(f"[quickframe] Generate clicked via {sel!r}")
                break
        except Exception:
            continue

    if not gen_clicked:
        # JS fallback
        await qf_page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                if (t === 'generate' || t === 'create' || t === 'submit') {
                    b.click(); return;
                }
            }
        }""")

    await _snap(qf_page, "qf-generating-start", screenshot_cb)
    await _cb(progress_cb, "⏳ Your video is on its way…")

    # ── Poll for completion ───────────────────────────────────────────────────
    # Look for a download button / video element appearing
    video_url: str | None = None
    download_handle = None

    for attempt in range(80):  # 80 × 6s ≈ 8 min
        await asyncio.sleep(6)
        elapsed = (attempt + 1) * 6

        if elapsed % 30 == 0:
            await _cb(progress_cb, f"⏳ Still generating… ({elapsed}s)")
            await _snap(qf_page, f"qf-poll-{elapsed}s", screenshot_cb)

        # Check for a download button
        for dl_sel in [
            "button:has-text('Download')",
            "a[download]",
            "a:has-text('Download')",
            "[aria-label*='download' i]",
            "button[aria-label*='download' i]",
        ]:
            try:
                el = qf_page.locator(dl_sel).first
                if await el.is_visible(timeout=1_000):
                    print(f"[quickframe] ✅ Download button found after {elapsed}s")
                    await _snap(qf_page, "qf-video-ready", screenshot_cb)
                    await _cb(progress_cb, "⬇️ Downloading video…")

                    # Try intercepting the download
                    tag = await el.get_attribute("tagName") or ""
                    href = await el.get_attribute("href") or ""
                    if href and not href.startswith("javascript"):
                        video_url = href
                        break

                    # Use Playwright download event
                    try:
                        async with qf_page.expect_download(timeout=60_000) as dl_info:
                            await el.click(timeout=10_000)
                        dl = await dl_info.value
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                            await dl.save_as(f.name)
                            video_bytes = Path(f.name).read_bytes()
                        print(f"[quickframe] ✅ downloaded {len(video_bytes)//1024}KB via download event")
                        return video_bytes
                    except Exception as dl_err:
                        print(f"[quickframe] download event failed: {dl_err} — trying video src")

            except Exception:
                continue

        if video_url:
            break

        # Also check for a <video> tag with a real src
        try:
            vid_src = await qf_page.evaluate("""() => {
                const v = document.querySelector('video[src]');
                if (v && v.src && !v.src.startsWith('blob:') && v.src !== window.location.href)
                    return v.src;
                const s = document.querySelector('video source[src]');
                if (s && s.src) return s.src;
                return null;
            }""")
            if vid_src:
                video_url = vid_src
                print(f"[quickframe] ✅ video src found after {elapsed}s: {vid_src[:80]}")
                break
        except Exception:
            pass

        # Check for blob video (fetch directly)
        try:
            blob_src = await qf_page.evaluate("""() => {
                const v = document.querySelector('video');
                return (v && v.src && v.src.startsWith('blob:')) ? v.src : null;
            }""")
            if blob_src:
                print(f"[quickframe] ✅ blob video found after {elapsed}s")
                await _cb(progress_cb, "⬇️ Downloading video…")
                arr = await qf_page.evaluate(
                    """async (url) => {
                        const r = await fetch(url);
                        return Array.from(new Uint8Array(await r.arrayBuffer()));
                    }""",
                    blob_src,
                )
                video_bytes = bytes(arr)
                print(f"[quickframe] ✅ blob downloaded {len(video_bytes)//1024}KB")
                return video_bytes
        except Exception:
            pass

    if not video_url:
        await _snap(qf_page, "qf-timeout", screenshot_cb)
        raise RuntimeError("QuickFrame: video did not complete within ~8 minutes")

    # ── Fetch via URL ─────────────────────────────────────────────────────────
    await _cb(progress_cb, "⬇️ Downloading video…")
    import aiohttp
    async with aiohttp.ClientSession() as sess:
        async with sess.get(video_url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"QuickFrame video download failed: HTTP {resp.status}")
            video_bytes = await resp.read()

    print(f"[quickframe] ✅ downloaded {len(video_bytes)//1024}KB from URL")
    return video_bytes


# ── Public entry point ─────────────────────────────────────────────────────────

async def generate_quickframe_video(
    prompt: str,
    duration: int = 10,
    progress_cb: ProgressCB = None,
    screenshot_cb: ScreenshotCB = None,
) -> bytes:
    """
    Generate a video using QuickFrame AI.

    Args:
        prompt:        Text description of the video.
        duration:      Clip length in seconds (1–15, default 10).
        progress_cb:   Async callback(msg) for status updates.
        screenshot_cb: Async callback(label, jpeg_bytes) for debug screenshots.

    Returns:
        MP4 video as raw bytes.
    """
    duration = max(1, min(15, int(duration)))

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser, ctx = await _new_browser(pw)
        try:
            # Two pages — one for mailticking, one for QuickFrame
            mail_page = await ctx.new_page()
            qf_page   = await ctx.new_page()

            await _cb(progress_cb, "📧 Getting temporary email…")
            email = await _get_temp_email(mail_page, screenshot_cb=screenshot_cb)

            await _login_quickframe(qf_page, email, mail_page, progress_cb, screenshot_cb)

            video_bytes = await _generate_video(
                qf_page, prompt, duration, progress_cb, screenshot_cb
            )
            return video_bytes

        finally:
            await ctx.close()
            await browser.close()
