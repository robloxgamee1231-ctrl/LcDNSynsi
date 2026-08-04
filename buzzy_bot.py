"""
buzzy_bot.py — Playwright automation for Buzzy.now
Flow per request:
  1. mailticking.com → uncheck 3 alias checkboxes → copy top Gmail → Activate
  2. buzzy.now/login → Continue with Email → enter email + password (freerobux)
  3. mailticking.com → refresh → find Buzzy verification code
  4. Enter 6-digit code on buzzy.now (typed, not pasted)
  5. Close any promo popup (seedance-promo-modal)
  6. Image: open "Agent Mode" menu → click "Image Generator" → select model
     tab (Seedream 4.0 / GPT Image 1) → click the prompt editor
     (.prompt-editor-wrapper, force=True — the fixed header can intercept a
     normal click here) → type prompt → click ".create-btn" (force=True)
     → poll for "Image Done" → the result appears directly in the chat feed
     with a download icon overlaid on its corner (no folder/lightbox step)
     → click it → confirm download
     Video: scroll to Video Generation → pick 2.5 Update (30s) → Seedance or Google Omni
            → prompt → ↑ → folder → select → download
     (Every step has a "look around" fallback — if the expected element isn't
     found, scan visible clickable elements for a loose text match instead of
     giving up outright, since Buzzy's UI changes without notice.)
"""

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional

from playwright.async_api import async_playwright, Page

_MAILTICKING_URL = "https://mailticking.com"
_BUZZY_LOGIN     = "https://www.buzzy.now/login"
_BUZZY_PASSWORD  = "freerobux1231"

ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]


# ── helpers ────────────────────────────────────────────────────────────────────

async def _snap(page: Page, label: str, cb: Optional[ScreenshotCB]) -> None:
    if cb:
        try:
            img = await page.screenshot(type="jpeg", quality=65, full_page=False)
            await cb(f"[buzzy] {label}", img)
        except Exception as e:
            print(f"[buzzy] screenshot({label}): {e}")


async def _click_first(page: Page, selectors: list[str], timeout: int = 3000, force: bool = False) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                await el.click(force=force, timeout=5_000)
                return True
        except Exception:
            pass
    return False


async def _type_into(page: Page, selectors: list[str], text: str, delay: int = 45) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=3000, state="visible")
            if el:
                await el.click()
                await el.fill("")
                await page.keyboard.type(text, delay=delay)
                return True
        except Exception:
            pass
    return False


async def _dismiss_security_verification(page: Page) -> bool:
    """mailticking occasionally shows a 'Security Verification — Too many
    requests. Please verify you are human.' modal with a checkbox labelled
    'Verify you are human'.  Just clicking the checkbox is enough to dismiss
    it (confirmed by user 2026-07-13).  Call this whenever interacting with
    mailticking so it never blocks the automation."""
    try:
        # Detect by the modal heading or the checkbox label text
        is_present = await page.evaluate(
            """() => {
                const text = document.body.innerText || '';
                return text.includes('Security Verification') ||
                       text.includes('verify you are human') ||
                       text.includes('Too many requests');
            }"""
        )
        if not is_present:
            return False
        print("[buzzy/mail] security-verification popup detected — clicking checkbox")
        # Try clicking the checkbox directly
        for sel in [
            "input[type='checkbox']",
            "label:has-text('Verify you are human')",
            "label:has-text('verify')",
            ".verify-checkbox",
            "[class*='captcha'] input",
            "[class*='verify'] input",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(force=True)
                    await page.wait_for_timeout(1_500)
                    print(f"[buzzy/mail] clicked security-verification checkbox via {sel}")
                    return True
            except Exception:
                pass
        # Fallback: JS click on any visible checkbox inside the modal
        clicked = await page.evaluate(
            """() => {
                const modal = document.querySelector('[class*="modal"], [class*="dialog"], [role="dialog"]');
                const root  = modal || document;
                const cb    = root.querySelector('input[type="checkbox"]');
                if (cb) { cb.click(); return true; }
                // Also try clicking any label that mentions verify/human
                const labels = Array.from(root.querySelectorAll('label'));
                const lbl = labels.find(l => l.textContent.toLowerCase().includes('human') ||
                                             l.textContent.toLowerCase().includes('verify'));
                if (lbl) { lbl.click(); return true; }
                return false;
            }"""
        )
        if clicked:
            await page.wait_for_timeout(1_500)
            print("[buzzy/mail] JS-clicked security-verification checkbox")
            return True
        # Last resort: hit Cancel so the modal at least disappears
        for sel in ["button:has-text('Cancel')", "button:has-text('cancel')"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await page.wait_for_timeout(500)
                    print("[buzzy/mail] dismissed security-verification via Cancel")
                    return True
            except Exception:
                pass
    except Exception as e:
        print(f"[buzzy/mail] security-verification handler error: {e}")
    return False


async def _dismiss_no_email_dialog(page: Page) -> bool:
    """mailticking pops up a modal ("No email found. Please consider using a
    full Email address or confirm whether the current email has already been
    registered.") when its own "Check emails" action re-queries the mail
    server and momentarily finds nothing — this is a transient race, not a
    real failure, but the modal sits on top of the page and blocks every
    click underneath it until dismissed. Detect it and click OK/Close so the
    polling loop can keep going instead of stalling until it hits max
    attempts."""
    try:
        dialog = await page.query_selector("text=/No email found/i")
        if not dialog:
            return False
        print("[buzzy/mail] dismissing transient 'No email found' popup")
        for btn_sel in ["button:has-text('OK')", "button:has-text('Ok')", "button:has-text('Close')", "[aria-label='Close']", "button:has-text('×')"]:
            try:
                btn = await page.query_selector(btn_sel)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
        # No obvious close button found — Escape usually closes modal dialogs.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False


# ── Step 1: get temp Gmail from mailticking ───────────────────────────────────

async def _get_temp_email(page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]) -> str:
    await progress("📧 Opening mailticking.com…")
    await page.goto(_MAILTICKING_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(4_000)
    await _dismiss_security_verification(page)
    await _snap(page, "mailticking-loaded", snap)

    # Uncheck the three unwanted formats:
    #   abc@domain.com  |  abc+d@gmail.com  |  abc@googlemail.com
    # Leave only the dotted-gmail format (a.b.c@gmail.com) checked.
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
                "+" in label_text               # abc+d@gmail.com
                or "googlemail" in label_text   # abc@googlemail.com
                or "domain" in label_text       # abc@domain.com
            )
            if should_uncheck:
                is_checked = await cb_el.is_checked()
                if is_checked:
                    await cb_el.click()
                    await page.wait_for_timeout(400)
                    print(f"[buzzy/mail] unchecked: {label_text[:60]}")
        except Exception as e:
            print(f"[buzzy/mail] checkbox err: {e}")

    await page.wait_for_timeout(800)
    await _snap(page, "mailticking-unchecked", snap)

    # Click the Change / ↺ button to regenerate the email in the dotted-gmail format
    changed = await _click_first(page, [
        "button:has-text('Change')",
        "button[class*='change' i]",
        "a:has-text('Change')",
        "button:has-text('↺')",
        "[title*='change' i]",
        "[aria-label*='change' i]",
        "[class*='refresh'] button",
        "button.btn-warning",   # mailticking uses btn-warning (teal) for Change
        "button.btn-info",
    ])
    if not changed:
        print("[buzzy/mail] WARNING: Change button not found — trying JS click on first teal/warning button")
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

    # Read the updated Gmail address from the top input
    email: Optional[str] = None
    email_source: str = ""

    # Try reading from the input field first (most reliable — the raw <input>
    # value can't be split across tags the way rendered/highlighted text can)
    for sel in [
        "input[readonly]",
        "input[type='text']",
        "#email",
        "input[value*='@gmail']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                val = await el.input_value()
                if val and "@gmail.com" in val and len(val) > len("@gmail.com") + 3:
                    email = val.strip()
                    email_source = f"input({sel})"
                    break
        except Exception:
            pass

    # Fallback 1: scan *every* input's value via JS — some builds render the
    # address inside an input that doesn't match the selectors above.
    if not email:
        try:
            val = await page.evaluate(
                """() => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    for (const i of inputs) {
                        if (i.value && i.value.includes('@gmail.com')) return i.value;
                    }
                    return null;
                }"""
            )
            if val and len(val) > len("@gmail.com") + 3:
                email = val.strip()
                email_source = "js-input-scan"
        except Exception:
            pass

    # Fallback 2: use the rendered/visible text (inner_text), NOT raw HTML.
    # mailticking highlights the randomized part of the address in its own
    # <span>, so regex-matching raw page.content() only sees the fragment
    # after the last tag boundary (e.g. "c@gmail.com" instead of the full
    # address). inner_text() flattens the DOM into plain text first, so the
    # full address comes back as one contiguous string.
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
                email_source = "inner_text-scan"
                break

    if not email:
        await _snap(page, "mailticking-no-email", snap)
        raise RuntimeError("mailticking: could not read Gmail address after Change")

    print(f"[buzzy/mail] got email: {email} (source: {email_source})")
    await progress(f"📧 Got email: `{email}`")

    # Click Activate
    activated = await _click_first(page, [
        "button:has-text('Activate')",
        "input[value='Activate']",
        "a:has-text('Activate')",
        "button[class*='activate' i]",
        "button.btn-success",
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


# ── Step 3: poll mailticking for Buzzy verification code ─────────────────────

async def _get_verification_code(
    page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> str:
    await progress("📬 Polling inbox for Buzzy verification code…")

    # mailticking can take a while to actually deliver the email, and its own
    # "Check emails" button occasionally throws a transient "No email found"
    # popup while the message is still in flight. Be patient: poll for up to
    # ~8 minutes (48 attempts * ~10s) instead of giving up after ~4 minutes.
    max_attempts = 48
    for attempt in range(max_attempts):
        # Dismiss any overlay modals before doing anything else — both the
        # transient "No email found" dialog and the "Security Verification"
        # human-check modal can block every click underneath them.
        await _dismiss_security_verification(page)
        await _dismiss_no_email_dialog(page)

        # Click refresh button
        await _click_first(page, [
            "button:has-text('Refresh')",
            "[class*='refresh']:not(input)",
            "button[title*='refresh' i]",
            "svg[class*='refresh']",
            "[aria-label*='refresh' i]",
        ], timeout=2000)
        await page.wait_for_timeout(4_000)
        await _dismiss_security_verification(page)
        await _dismiss_no_email_dialog(page)
        await _snap(page, f"mailticking-inbox-{attempt}", snap)

        # Find and open the Buzzy email. mailticking rows carry a dedicated
        # "Check emails" / "View" action button rather than being clickable
        # as a whole — click that button first, and only fall back to
        # clicking the row itself if no such button exists.
        email_clicked = False
        for row_sel in ["tr", "li", ".email-item", ".message", "[class*='mail-row']", "tbody tr"]:
            rows = await page.query_selector_all(row_sel)
            for row in rows:
                try:
                    txt = (await row.inner_text()).lower()
                    if "buzzy" in txt or "verification" in txt or "verify" in txt or "login" in txt:
                        opened = False
                        for btn_sel in [
                            "button:has-text('Check email')",
                            "button:has-text('Check emails')",
                            "button:has-text('View')",
                            "button:has-text('Open')",
                            "a:has-text('Check email')",
                            "a:has-text('View')",
                            "button",
                            "a",
                        ]:
                            try:
                                btn = await row.query_selector(btn_sel)
                                if btn:
                                    await btn.click()
                                    opened = True
                                    print(f"[buzzy/mail] clicked '{btn_sel}' inside row: {txt[:60]}")
                                    break
                            except Exception:
                                pass
                        if not opened:
                            await row.click()
                            print(f"[buzzy/mail] clicked row directly: {txt[:60]}")
                        email_clicked = True
                        await page.wait_for_timeout(3_000)  # wait longer for email body to load
                        # The "Check emails" action can throw the transient
                        # "No email found" popup if it re-queries mid-flight
                        # — dismiss it here so we still try reading the body
                        # this same attempt instead of losing the round trip.
                        popped = await _dismiss_no_email_dialog(page)
                        if popped:
                            print("[buzzy/mail] 'No email found' popup appeared right after opening — treating as not-yet-arrived")
                            email_clicked = False
                        break
                except Exception:
                    pass
            if email_clicked:
                break

        await _snap(page, f"mailticking-email-body-{attempt}", snap)

        if not email_clicked:
            print(f"[buzzy/mail] attempt {attempt+1}/{max_attempts} — Buzzy email not found/clickable yet, waiting…")
            await progress(f"📬 Waiting for email to arrive… ({(attempt+1)*10}s)")
            await page.wait_for_timeout(5_000)
            continue

        # Read code from the *rendered visible text* of the now-opened email,
        # not raw page.content(). Raw HTML can contain incidental 6-digit
        # sequences (cache-busters, hashes, timestamps) that the old generic
        # \d{6} fallback would grab even when the email never actually opened.
        #
        # mailticking renders the opened email body inside an <iframe> (the
        # message HTML from Buzzy's mail server), so page.inner_text('body')
        # on the *main* frame never sees it — the code is visible on screen
        # but invisible to text extraction, so the loop just kept re-opening
        # the same email forever. Collect text from every frame, not just
        # the top-level one.
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
            r'login.*?code[:\s]+(\d{6})',
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

        # Only use the unqualified \b(\d{6})\b fallback once we've confirmed
        # the email body actually opened (email_clicked) — never on a bare
        # inbox listing, where it tends to match unrelated numbers.
        if not code:
            found = re.findall(r'\b(\d{6})\b', text)
            if found:
                code = found[0]

        if code:
            print(f"[buzzy/mail] code found: {code}")
            await _snap(page, "mailticking-code-found", snap)
            await progress(f"✅ Got verification code: `{code}`")
            return code

        print(f"[buzzy/mail] attempt {attempt+1}/{max_attempts} — email opened but no code text yet, waiting…")
        await progress(f"📬 Waiting for code… ({(attempt+1)*10}s)")
        await page.wait_for_timeout(5_000)

    await _snap(page, "mailticking-no-code", snap)
    raise RuntimeError("mailticking: Buzzy verification code never arrived after ~8 minutes of polling")


# ── Step 2: login to Buzzy ────────────────────────────────────────────────────

async def _login_buzzy(
    page: Page, email: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress("🐝 Navigating to Buzzy.now login…")
    await page.goto(_BUZZY_LOGIN, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(3_000)
    await _snap(page, "buzzy-login-page", snap)

    # Click "Continue with Email"
    await _click_first(page, [
        "button:has-text('Continue with Email')",
        "button:has-text('Email')",
        "a:has-text('Continue with Email')",
        "[data-testid*='email']",
        "text=Continue with Email",
    ])
    await page.wait_for_timeout(2_000)
    await _snap(page, "buzzy-email-option", snap)

    # Enter email
    await _type_into(page, [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='email' i]",
        "input[autocomplete='email']",
    ], email)
    await page.wait_for_timeout(300)

    # Enter password
    await _type_into(page, [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='password' i]",
    ], _BUZZY_PASSWORD)

    await _snap(page, "buzzy-form-filled", snap)
    await progress("🚀 Submitting login form…")

    # Submit
    await _click_first(page, [
        "button[type='submit']",
        "button:has-text('Continue')",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Next')",
    ])
    await page.wait_for_timeout(3_000)
    await _snap(page, "buzzy-after-submit", snap)


# ── Step 4: enter verification code on Buzzy ─────────────────────────────────

async def _code_screen_present(page: Page) -> bool:
    """True while the 'Enter Code' verification screen is still showing."""
    try:
        return await page.locator("text=Enter Code").count() > 0
    except Exception:
        return False


async def _visible_inputs(page: Page, exclude_types: set[str]) -> list:
    """All visible <input> elements, excluding hidden/checkbox/etc. Many custom
    OTP box components render plain `<input>` with no `type` attribute at all
    (default is "text" but the DOM attribute is absent), so attribute
    selectors like input[type='text'] silently match zero elements. Filter by
    the *effective* type via getAttribute instead of relying on CSS attribute
    selectors."""
    result = []
    for el in await page.query_selector_all("input"):
        try:
            if not await el.is_visible():
                continue
            t = (await el.get_attribute("type") or "text").lower()
            if t in exclude_types:
                continue
            result.append(el)
        except Exception:
            pass
    return result


async def _type_code_digits(page: Page, code: str) -> None:
    """Fill the OTP boxes. Prefer typing one digit directly into each of the
    N separate box inputs (most reliable); fall back to focusing a single
    input and relying on the site's own auto-advance behaviour."""
    boxes: list = []
    for sel in [
        "input[maxlength='1']",
        "input[type='tel']",
        "input[autocomplete='one-time-code']",
        "input[type='number']",
    ]:
        els = await page.query_selector_all(sel)
        if len(els) >= len(code):
            boxes = els
            print(f"[buzzy] found {len(els)} code box(es) via {sel}")
            break

    # Fallback: attribute selectors matched nothing — many OTP box components
    # use plain <input> elements with no `type` attribute set at all, which
    # `input[type='...']` selectors can never match. Scan every visible,
    # non-hidden/checkbox/radio/button input instead.
    exclude = {"hidden", "checkbox", "radio", "submit", "button", "file", "range", "color"}
    if not boxes:
        generic = await _visible_inputs(page, exclude)
        print(f"[buzzy] generic visible-input scan found {len(generic)} candidate(s)")
        if len(generic) >= len(code):
            boxes = generic[: len(code)] if len(generic) > len(code) else generic
            print(f"[buzzy] using {len(boxes)} box(es) from generic scan")

    if boxes:
        for i, digit in enumerate(code):
            if i >= len(boxes):
                break
            try:
                await boxes[i].click()
                await page.wait_for_timeout(80)
                await boxes[i].fill("")
                await page.keyboard.press(digit)  # real keydown/keyup for React
                await page.wait_for_timeout(80)
            except Exception as e:
                print(f"[buzzy] code box {i} type err: {e}")
        return

    # Fallback: exactly one input field on screen, rely on auto-advance
    generic = await _visible_inputs(page, exclude)
    first_input = generic[0] if generic else None
    if first_input:
        print("[buzzy] falling back to single-input auto-advance typing")
        await first_input.click()
        await page.wait_for_timeout(300)
        for digit in code:
            await page.keyboard.press(digit)
            await page.wait_for_timeout(150)
    else:
        print("[buzzy] WARNING: no code input found at all")


async def _click_next(page: Page) -> None:
    """Escalating click attempts against the Next/Verify/Confirm button."""
    print("[buzzy] clicking Next button")

    # Method 1: Playwright locator with force=True (bypasses disabled/pointer-events checks)
    next_clicked = False
    for label in ["Next", "Verify", "Confirm", "Continue", "Submit"]:
        try:
            loc = page.get_by_role("button", name=label, exact=False)
            if await loc.count() > 0:
                await loc.first.click(force=True, timeout=3_000)
                print(f"[buzzy] force-locator clicked: {label}")
                next_clicked = True
                break
        except Exception as e:
            print(f"[buzzy] locator {label}: {e}")

    await page.wait_for_timeout(500)

    # Method 2: mouse.click() at the button's actual bounding-box centre
    if not next_clicked:
        try:
            btn_el = await page.query_selector("button:has-text('Next')")
            if btn_el:
                box = await btn_el.bounding_box()
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    await page.mouse.click(cx, cy)
                    print(f"[buzzy] mouse.click Next at ({cx:.0f}, {cy:.0f})")
                    next_clicked = True
        except Exception as e:
            print(f"[buzzy] mouse click: {e}")

    await page.wait_for_timeout(500)

    # Method 3: remove disabled attribute then JS click
    try:
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const targets = ['next', 'verify', 'confirm', 'continue', 'submit'];
            for (const btn of btns) {
                const t = btn.textContent.trim().toLowerCase();
                if (targets.some(k => t.includes(k))) {
                    btn.removeAttribute('disabled');
                    btn.style.pointerEvents = 'auto';
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return btn.textContent.trim();
                }
            }
        }""")
        print("[buzzy] JS dispatchEvent click fired")
    except Exception as e:
        print(f"[buzzy] JS dispatch: {e}")

    await page.wait_for_timeout(500)

    # Method 4: Enter key on the page
    await page.keyboard.press("Enter")
    print("[buzzy] pressed Enter")


async def _enter_code_buzzy(
    page: Page, code: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> None:
    await progress(f"🔑 Entering code {code} on Buzzy…")
    await page.wait_for_timeout(1_000)
    await _snap(page, "buzzy-code-page", snap)

    accepted = False
    for attempt in range(1, 4):
        await _type_code_digits(page, code)
        await page.wait_for_timeout(800)
        await _snap(page, f"buzzy-code-entered-{attempt}", snap)

        await _click_next(page)
        await page.wait_for_timeout(3_000)

        if not await _code_screen_present(page):
            print(f"[buzzy] code accepted on attempt {attempt}")
            accepted = True
            break

        print(f"[buzzy] still on code screen after attempt {attempt}/3 — retrying")
        await progress(f"⚠️ Code didn't submit, retrying… ({attempt}/3)")
        await page.wait_for_timeout(1_000)

    await page.wait_for_timeout(1_500)
    await _snap(page, "buzzy-after-code", snap)

    if not accepted and await _code_screen_present(page):
        await _snap(page, "buzzy-code-failed", snap)
        raise RuntimeError(
            "Buzzy: verification code screen never advanced after 3 attempts "
            "(Next button click isn't registering or the code was wrong)"
        )


# ── Step 5: dismiss any popup ─────────────────────────────────────────────────

async def _dismiss_promo_popup(page: Page) -> bool:
    """Buzzy shows a "seedance-promo-modal" upsell dialog on first load after
    login. Confirmed via live debugging (debug_buzzy_promo.py)."""
    for sel in [
        ".seedance-promo-modal__close",
        "[aria-label='Close promotion']",
        "[data-slot='dialog-close']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(500)
                print(f"[buzzy] dismissed promo modal via {sel}")
                return True
        except Exception:
            pass
    return False


async def _dismiss_popup(page: Page) -> None:
    for close_sel in [
        "button[aria-label*='close' i]",
        "button[aria-label='Close']",
        "[class*='close']",
        "[class*='modal'] button:has-text('×')",
        "button:has-text('×')",
        "button:has-text('✕')",
        "[data-testid*='close']",
    ]:
        try:
            el = await page.query_selector(close_sel)
            if el and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def _find_by_text(page: Page, keywords: list[str]):
    """'Look around' fallback: when a specific selector doesn't find the
    expected element (Buzzy's UI changes without notice), scan every visible
    button/link/role=button element for text, aria-label, or title that
    loosely matches one of the given keywords instead of giving up."""
    for el in await page.query_selector_all("button, a, [role='button']"):
        try:
            if not await el.is_visible():
                continue
            txt = ((await el.inner_text()) or "").strip().lower()
            aria = (await el.get_attribute("aria-label") or "").lower()
            title = (await el.get_attribute("title") or "").lower()
            haystack = f"{txt} {aria} {title}"
            if any(k in haystack for k in keywords):
                return el
        except Exception:
            pass
    return None


async def _editor_contains_text(page: Page, needle: str) -> bool:
    """Check whether the prompt editor actually holds the text we tried to
    type — confirmed live 2026-07-13 that clicking `.prompt-editor-wrapper`
    can succeed (no exception) and `page.keyboard.type()` can run without
    error while the text never lands anywhere, because the wrapper div
    itself isn't the focusable/editable node — a fixed header overlay
    silently stole focus back after the force-click. Never trust "the click
    didn't throw" as proof of success; read the DOM back."""
    try:
        snippet = needle[:20].strip()
        if not snippet:
            return False
        found = await page.evaluate(
            """(snippet) => {
                const wrap = document.querySelector('.prompt-editor-wrapper');
                if (!wrap) return false;
                const hay = (wrap.innerText || wrap.textContent || '') + ' ' +
                    Array.from(wrap.querySelectorAll('textarea, input'))
                        .map(el => el.value || '').join(' ');
                return hay.includes(snippet);
            }""",
            snippet,
        )
        return bool(found)
    except Exception:
        return False


async def _type_prompt_editor(page: Page, prompt: str) -> bool:
    """Type `prompt` into Buzzy's prompt editor and verify it actually landed.

    DOM confirmed 2026-07-13:
      - .prompt-editor-wrapper wraps the editor
      - .prompt-editor[contenteditable] starts hidden (display:none inline)
      - Force-clicking the WRAPPER triggers Vue to show & focus the editor
      - After that click the editor is at real coordinates (~420,280 675×46)
      - keyboard.type() works once the editor is truly focused
      - DO NOT use execCommand('selectAll') — it selects all page text when
        the editor isn't the active element, causing visible full-page
        text selection (confirmed broken 2026-07-13).
    """

    # ── Strategy A: wrapper force-click → direct editor click → type ─────────
    # Confirmed approach: the wrapper click activates Vue's handler which
    # un-hides the editor and gives it focus. We then mouse-click directly
    # on the editor's real coordinates and type.
    try:
        wrapper = await page.query_selector(".prompt-editor-wrapper")
        if wrapper:
            await wrapper.click(force=True, timeout=5_000)
            await page.wait_for_timeout(700)

            # Editor should now be visible at real coords — click it directly
            editor = await page.query_selector(".prompt-editor[contenteditable]")
            if editor:
                try:
                    box = await editor.bounding_box()
                    if box and box.get("width", 0) > 0:
                        await page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                        await page.wait_for_timeout(300)
                        print(f"[buzzy] clicked editor directly at ({box['x']:.0f},{box['y']:.0f})")
                except Exception as be:
                    print(f"[buzzy] direct editor click skipped: {be}")

            # Editor is empty on first use — just type; no select-all needed
            await page.keyboard.type(prompt, delay=30)
            await page.wait_for_timeout(500)
            if await _editor_contains_text(page, prompt):
                print("[buzzy] prompt landed via wrapper force-click + type")
                return True
            print("[buzzy] wrapper force-click + type: text didn't land")
    except Exception as e:
        print(f"[buzzy] strategy A failed: {e}")

    # ── Strategy B: JS activate wrapper + insertText (NO selectAll) ──────────
    # Clear via ed.textContent='' (not execCommand selectAll which grabs the
    # whole page), focus, place cursor at start, then insertText.
    try:
        result = await page.evaluate(
            """(text) => {
                // Trigger Vue handler on the wrapper first
                const wrap = document.querySelector('.prompt-editor-wrapper');
                if (wrap) {
                    wrap.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
                    wrap.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true}));
                    wrap.dispatchEvent(new MouseEvent('click',     {bubbles:true, cancelable:true}));
                }

                const ed = document.querySelector('.prompt-editor[contenteditable]')
                        || document.querySelector('[contenteditable="true"]');
                if (!ed) return {ok:false, reason:'no editor'};

                // Make visible without touching page-level selection
                ed.style.removeProperty('display');
                ed.style.display = 'block';
                ed.style.minHeight = '40px';

                // Clear content directly (no execCommand selectAll)
                ed.textContent = '';

                // Focus and place cursor
                ed.focus();
                const range = document.createRange();
                const sel   = window.getSelection();
                sel.removeAllRanges();
                range.setStart(ed, 0);
                range.collapse(true);
                sel.addRange(range);

                // insertText fires the InputEvent Vue is listening for
                const ok = document.execCommand('insertText', false, text);
                return {ok, content: ed.textContent, active: document.activeElement === ed};
            }""",
            prompt,
        )
        print(f"[buzzy] JS insertText result: {result}")
        await page.wait_for_timeout(500)
        if await _editor_contains_text(page, prompt):
            print("[buzzy] prompt landed via JS insertText")
            return True
        print("[buzzy] JS insertText: text didn't land — trying keyboard")
    except Exception as e:
        print(f"[buzzy] strategy B failed: {e}")

    # ── Strategy C: JS focus + keyboard.type (no Ctrl+A) ────────────────────
    try:
        await page.evaluate(
            """() => {
                const ed = document.querySelector('.prompt-editor[contenteditable]')
                        || document.querySelector('[contenteditable="true"]');
                if (ed) {
                    ed.style.removeProperty('display');
                    ed.textContent = '';
                    ed.focus();
                }
            }"""
        )
        await page.wait_for_timeout(300)
        await page.keyboard.type(prompt, delay=25)
        await page.wait_for_timeout(400)
        if await _editor_contains_text(page, prompt):
            print("[buzzy] prompt landed via JS focus + keyboard.type")
            return True
        print("[buzzy] JS focus + keyboard.type didn't land")
    except Exception as e:
        print(f"[buzzy] strategy C failed: {e}")

    # ── Strategy D: textarea / input fallbacks ────────────────────────────────
    for sel in [
        ".prompt-editor-wrapper textarea",
        ".prompt-editor-wrapper input",
        "textarea[placeholder*='describe' i]",
        "textarea",
    ]:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            await el.click(force=True, timeout=5_000)
            await page.wait_for_timeout(200)
            await page.keyboard.type(prompt, delay=25)
            await page.wait_for_timeout(300)
            if await _editor_contains_text(page, prompt):
                print(f"[buzzy] prompt landed via fallback '{sel}'")
                return True
        except Exception as e:
            print(f"[buzzy] fallback '{sel}' failed: {e}")

    return False


async def _list_visible_labels(page: Page, limit: int = 40) -> list[str]:
    """'Look around' helper: dump the text of every visible clickable element
    so a failure can be diagnosed/matched against instead of guessing blind."""
    try:
        labels = await page.evaluate(
            """(limit) => Array.from(document.querySelectorAll("button, [role='option'], li, a"))
                .filter(el => el.offsetParent !== null)
                .map(el => (el.textContent || '').trim())
                .filter(t => t && t.length < 40)
                .slice(0, limit)""",
            limit,
        )
        return labels
    except Exception:
        return []


# ── Step 6 & 7: generate image ───────────────────────────────────────────────

async def _generate_image_buzzy(
    page: Page,
    prompt: str,
    model: str,     # "Nano Banana" or "GPT Image"
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> bytes:
    await progress("🖼️ Setting up Image mode…")

    # Dismiss the seedance promo modal first (confirmed via live debugging),
    # then fall back to any other generic popup.
    await page.wait_for_timeout(1_500)
    await _dismiss_promo_popup(page)
    await _dismiss_popup(page)
    await page.wait_for_timeout(500)
    await _snap(page, "buzzy-home", snap)

    # Buzzy's current homepage is a single "AI Director" chat/agent screen.
    # Open the "Agent Mode" pill's dropdown (bottom-left of the prompt box)
    # and pick "Image Generator" from it (confirmed selector — NOT "Image").
    await progress("🧭 Opening Agent Mode menu…")
    trigger = await page.query_selector("button:has-text('Agent Mode')")
    opened_mode_menu = False
    if trigger:
        await trigger.click()
        opened_mode_menu = True
    await page.wait_for_timeout(700)
    await _snap(page, "buzzy-agent-mode-menu", snap)

    clicked_image_mode = False
    if opened_mode_menu:
        img_item = await page.query_selector("text=/Image Generator/i")
        if img_item:
            await img_item.click()
            clicked_image_mode = True
            await page.wait_for_timeout(1_500)

    if not clicked_image_mode:
        # Look around: the exact wording moved before, so scan every visible
        # clickable label for anything mentioning "image" instead of giving up.
        labels = await _list_visible_labels(page)
        print(f"[buzzy] 'Image Generator' not found — visible labels: {labels}")
        el = await _find_by_text(page, ["image"])
        if el:
            await el.click()
            clicked_image_mode = True
            await page.wait_for_timeout(1_500)
            print("[buzzy] fallback-clicked an element matching 'image'")

    if not clicked_image_mode:
        await _snap(page, "buzzy-image-mode-not-found", snap)
        print("[buzzy] WARNING: could not enter Image Generator mode — continuing anyway")

    # The Agent Mode dropdown's own backdrop/overlay can still be mid-fade-out
    # right after the click registers — a click on it lands but the overlay
    # keeps intercepting pointer events for a beat. Nudge focus away with
    # Escape so it fully collapses before we touch anything else, and give it
    # a moment longer than before (confirmed live: 1.5s wasn't enough — the
    # very next click ate a 30s interception timeout against a leftover
    # overlay / the fixed header).
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1_000)
    await _snap(page, "buzzy-image-mode-selected", snap)

    # Model selection: tabs are "Seedream 4.0" / "GPT Image 1" (confirmed live
    # 2026-07-13) — not "Nano Banana" as originally assumed. Try the requested
    # model plus a couple of known aliases; force=True because these tabs sit
    # in the same region the fixed header intercepts normal clicks against.
    await progress(f"🤖 Selecting model: {model}… (if available)")
    model_aliases = [model]
    if "nano" in model.lower() or "seedream" in model.lower():
        model_aliases.append("Seedream")
    if "gpt" in model.lower():
        model_aliases.append("GPT Image")
    model_selectors = []
    for alias in model_aliases:
        model_selectors += [
            f"button:has-text('{alias}')",
            f"[role='tab']:has-text('{alias}')",
            f"label:has-text('{alias}')",
        ]
    await _click_first(page, model_selectors, timeout=1_500, force=True)
    await page.wait_for_timeout(300)

    # Type prompt into the prompt editor (confirmed selector). force=True on
    # the click — the fixed header (`pointer-events-auto` inner nav div) can
    # sit over the editor's click point and a normal click hangs the full 30s
    # actionability timeout waiting for it to stop "intercepting".
    await progress("✍️ Typing prompt…")
    typed = await _type_prompt_editor(page, prompt)
    if not typed:
        # Last-resort fallback: some other textarea/input outside
        # .prompt-editor-wrapper might be the real target if Buzzy's markup
        # changed. Try those, still verifying the text actually landed.
        typed = await _type_into(page, [
            "textarea[placeholder*='prompt' i]",
            "textarea[placeholder*='describe' i]",
            "textarea",
            "input[placeholder*='prompt' i]",
        ], prompt, delay=25)
        if typed and not await _editor_contains_text(page, prompt):
            typed = False
    if not typed:
        await _snap(page, "buzzy-prompt-not-found", snap)
        raise RuntimeError(
            "Buzzy: typed into the prompt editor but the text never landed "
            "(editor stole focus back or the wrapper isn't the real input) — "
            "aborting instead of clicking Create on an empty prompt"
        )
    await page.wait_for_timeout(400)
    await _snap(page, "buzzy-prompt-typed", snap)

    # Click Create.  The button label is "Create ✦" (or just "Create") and
    # lives inside the prompt bar on the right side.  We try several
    # strategies in order — the JS approach is the most reliable because it
    # doesn't depend on Playwright's actionability checks.
    await progress("🚀 Starting generation…")

    # ── Create strategy 1: JS — find the "Create ✦" button inside the prompt
    #    bar area, explicitly excluding the "Create a video" header pill.
    created = False
    try:
        clicked = await page.evaluate(
            """() => {
                // Priority 1: button with class create-btn anywhere on page
                const byClass = document.querySelector('.create-btn');
                if (byClass && byClass.offsetParent !== null && !byClass.disabled) {
                    byClass.click();
                    return byClass.textContent.trim();
                }

                // Priority 2: button INSIDE the prompt-editor-wrapper
                const wrapper = document.querySelector('.prompt-editor-wrapper');
                if (wrapper) {
                    const inner = wrapper.querySelector('button');
                    if (inner && inner.offsetParent !== null) {
                        inner.click();
                        return inner.textContent.trim();
                    }
                }

                // Priority 3: button whose text is exactly "Create" or starts
                //   with "Create" but is NOT "Create a video" (header pill).
                const btns = Array.from(document.querySelectorAll('button'));
                const match = btns.find(b => {
                    const t = b.textContent.trim().toLowerCase();
                    return t.startsWith('create') &&
                           !t.includes('video') &&
                           b.offsetParent !== null &&
                           !b.disabled;
                });
                if (match) {
                    match.click();
                    return match.textContent.trim();
                }
                return null;
            }"""
        )
        if clicked:
            print(f"[buzzy] JS clicked Create button: '{clicked}'")
            created = True
    except Exception as e:
        print(f"[buzzy] JS create click failed: {e}")

    # ── Create strategy 2: Playwright selectors (wider list, force=True) ─────
    if not created:
        print("[buzzy] JS create click didn't fire — trying Playwright selectors")
        created = await _click_first(page, [
            ".create-btn",
            "button:has-text('Create')",
            "button:has-text('create')",
            "[class*='create-btn']",
            "[class*='createBtn']",
            "button[aria-label*='create' i]",
            "button[aria-label*='send' i]",
            "button[aria-label*='generate' i]",
            "button[type='submit']",
        ], timeout=4_000, force=True)

    # ── Create strategy 3: find by text helper ────────────────────────────────
    if not created:
        el = await _find_by_text(page, ["create", "generate", "send"])
        if el:
            try:
                await el.click(force=True, timeout=5_000)
                print("[buzzy] clicked Create via _find_by_text")
                created = True
            except Exception as e:
                print(f"[buzzy] _find_by_text click failed: {e}")

    # ── Create strategy 4: press Enter — many prompt bars submit on Enter ─────
    if not created:
        print("[buzzy] all Create selectors failed — pressing Enter as last resort")
        await page.keyboard.press("Enter")
        created = True   # assume it worked; generation loop will time-out if not

    await _snap(page, "buzzy-after-create", snap)
    print(f"[buzzy] Create clicked (created={created})")

    # Wait for "Image Done" (confirmed marker — up to ~3 min)
    await progress("⏳ Generating image… (up to ~2 min)")
    generated = False
    for tick in range(36):
        await page.wait_for_timeout(5_000)
        elapsed = (tick + 1) * 5
        await progress(f"⏳ Generating… ({elapsed}s)")

        done_el = await page.query_selector("text=/Image Done/i")
        if done_el:
            generated = True
            print(f"[buzzy] image ready at ~{elapsed}s")
            break
        if tick % 4 == 3:
            await _snap(page, f"buzzy-generating-{tick}", snap)

    if not generated:
        await _snap(page, "buzzy-timeout", snap)
        raise RuntimeError("Buzzy image generation timed out")

    await page.wait_for_timeout(1_500)
    await _snap(page, "buzzy-gen-done", snap)

    return await _open_and_download_image(page, progress, snap)


async def _generate_video_buzzy(
    page: Page,
    prompt: str,
    model: str,     # "Seedance" or "Google Omni"
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> bytes:
    await progress("🎬 Navigating to Video Generation…")

    await _dismiss_popup(page)
    await page.wait_for_timeout(1_000)

    # Scroll to Video Generation section
    try:
        vid_section = await page.query_selector(
            "text=Video Generation, [id*='video' i], [class*='video-gen' i], "
            "h2:has-text('Video'), h3:has-text('Video')"
        )
        if vid_section:
            await vid_section.scroll_into_view_if_needed()
    except Exception:
        await page.evaluate("window.scrollBy(0, 1200)")

    await page.wait_for_timeout(1_000)
    await _snap(page, "buzzy-video-section", snap)

    # Select "2.5 Update (30s)" version
    await _click_first(page, [
        "button:has-text('2.5 Update')",
        "button:has-text('2.5')",
        "[role='tab']:has-text('2.5')",
        "label:has-text('2.5 Update')",
        "[class*='version']:has-text('2.5')",
    ], timeout=3000)
    await page.wait_for_timeout(400)

    # Select model (Seedance or Google Omni)
    await progress(f"🤖 Selecting model: {model}…")
    await _click_first(page, [
        f"button:has-text('{model}')",
        f"[role='tab']:has-text('{model}')",
        f"label:has-text('{model}')",
        f"[aria-label*='{model}' i]",
        f"li:has-text('{model}')",
    ], timeout=3000)
    await page.wait_for_timeout(500)

    # Type prompt
    await progress("✍️ Typing prompt…")
    await _type_into(page, [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea",
        "input[placeholder*='prompt' i]",
        "input[type='text']",
    ], prompt, delay=30)
    await page.wait_for_timeout(400)
    await _snap(page, "buzzy-video-prompt", snap)

    # Click generate / up-arrow
    await progress("🚀 Starting video generation…")
    await _click_first(page, [
        "button[aria-label*='send' i]",
        "button[aria-label*='generate' i]",
        "[class*='send']:not(input)",
        "button:has-text('Generate')",
        "button[type='submit']",
    ])

    # Wait up to 3 min
    await progress("⏳ Generating video… (up to ~2 min)")
    generated = False
    for tick in range(36):
        await page.wait_for_timeout(5_000)
        elapsed = (tick + 1) * 5
        await progress(f"⏳ Generating… ({elapsed}s)")

        done_el = await page.query_selector(
            "[aria-label*='folder' i], [class*='folder'], "
            "[data-testid*='folder'], button:has-text('Download'), "
            "video[src], [class*='thumbnail']"
        )
        if done_el:
            generated = True
            print(f"[buzzy] video ready at ~{elapsed}s")
            break
        await _snap(page, f"buzzy-vid-generating-{tick}", snap)

    if not generated:
        await _snap(page, "buzzy-vid-timeout", snap)
        raise RuntimeError("Buzzy video generation timed out")

    await _snap(page, "buzzy-vid-done", snap)
    await progress("📁 Opening folder to download…")

    await _click_first(page, [
        "[aria-label*='folder' i]",
        "[class*='folder']",
        "[data-testid*='folder']",
        "button:has-text('Folder')",
    ])
    await page.wait_for_timeout(2_000)
    await _snap(page, "buzzy-vid-folder-open", snap)

    return await _download_from_folder(page, "video", progress, snap)


async def _open_and_download_image(
    page: Page, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> bytes:
    """Download the generated image from Buzzy.

    Strategies (in order):
      0. Click the generated image card/canvas ("the box") to select it,
         then click the toolbar download button that appears.
      1. Click toolbar icon buttons near "Run this" / "Share" anchor.
      2. aria-label/title download button or <a download> link.
      3. Browser fetch() of the largest <img> src (no offsetParent check).
      4. Click every small button near the image and intercept download.
      5. Element screenshot fallback: call element.screenshot() directly on
         the largest visible <img> — no page screenshot, no cropping needed.
    """
    await progress("⬇️ Downloading image…")
    await page.wait_for_timeout(800)
    await _snap(page, "buzzy-download-attempt", snap)

    import asyncio as _asyncio

    # ── Helpers: reliable image-grab methods ─────────────────────────────────

    async def _find_best_raw_img_el():
        """Scan ALL img elements, log their URLs + natural dimensions, and return
        the element handle with the largest naturalWidth × naturalHeight.
        The raw AI-generated image will have the biggest natural size (e.g. 1024×1024),
        while Buzzy's card thumbnails are composite UI images at smaller natural sizes."""
        try:
            info_list = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('img'))
                    .map((img, idx) => {
                        const r = img.getBoundingClientRect();
                        const src = img.src || img.currentSrc || '';
                        return {
                            idx,
                            src,
                            nw: img.naturalWidth  || 0,
                            nh: img.naturalHeight || 0,
                            rw: Math.round(r.width),
                            rh: Math.round(r.height),
                        };
                    })
                    .filter(i => i.src && !i.src.startsWith('data:') && !i.src.includes('.svg'));
            }""")
            print(f"[buzzy] all imgs ({len(info_list)}): " +
                  ", ".join(f"{i['nw']}×{i['nh']} {i['src'][:80]}" for i in info_list))
            # Pick the one with the largest natural area (the raw AI image)
            valid = [i for i in info_list if i["nw"] > 0 and i["nh"] > 0]
            if not valid:
                return None
            best = max(valid, key=lambda i: i["nw"] * i["nh"])
            print(f"[buzzy] best raw img: idx={best['idx']} {best['nw']}×{best['nh']} {best['src'][:100]}")
            handles = await page.query_selector_all("img")
            if best["idx"] < len(handles):
                return handles[best["idx"]]
        except Exception as e:
            print(f"[buzzy] _find_best_raw_img_el failed: {e}")
        return None

    async def _download_img_via_requests(img_el) -> Optional[bytes]:
        """Get the img src and download it using Python requests + browser cookies.
        Bypasses CORS, download-event, and blob-URL issues entirely."""
        try:
            src = await img_el.evaluate("el => el.src || el.currentSrc || ''")
            if not src or src.startswith("data:") or src.endswith(".svg"):
                print(f"[buzzy] requests: src unusable: {(src or '')[:80]}")
                return None
            print(f"[buzzy] requests: downloading {src}")
            cookies = await page.context.cookies()
            import requests as _req
            session = _req.Session()
            for ck in cookies:
                session.cookies.set(
                    ck["name"], ck["value"], domain=ck.get("domain", "")
                )
            resp = session.get(
                src, timeout=30,
                headers={"Referer": page.url, "User-Agent": "Mozilla/5.0"}
            )
            if resp.ok and len(resp.content) > 5_000:
                print(f"[buzzy] {len(resp.content)//1024} KB via requests ({resp.headers.get('content-type','?')})")
                return resp.content
            print(f"[buzzy] requests: status={resp.status_code} size={len(resp.content)}")
        except Exception as e:
            print(f"[buzzy] requests download failed: {e}")
        return None

    async def _element_screenshot_bytes(img_el) -> Optional[bytes]:
        """Capture just the image element using element.screenshot() — no clipping needed."""
        try:
            await img_el.scroll_into_view_if_needed()
            await page.wait_for_timeout(400)
            data = await img_el.screenshot(type="png")
            if len(data) > 5_000:
                print(f"[buzzy] {len(data)//1024} KB via element.screenshot()")
                return data
            print(f"[buzzy] element.screenshot(): too small ({len(data)} bytes)")
        except Exception as e:
            print(f"[buzzy] element.screenshot() failed: {e}")
        return None

    async def _try_download_click(btn, label: str) -> Optional[bytes]:
        """Click btn and handle whatever Buzzy does:
        - Standard browser download event  → save and return bytes
        - New tab / popup opens with image → fetch from new page and return bytes
        Registers both listeners BEFORE clicking so neither event is missed."""
        loop = _asyncio.get_event_loop()
        dl_fut: _asyncio.Future = loop.create_future()
        pg_fut: _asyncio.Future = loop.create_future()

        def _on_dl(dl):
            if not dl_fut.done():
                dl_fut.set_result(dl)

        def _on_pg(pg):
            if not pg_fut.done():
                pg_fut.set_result(pg)

        page.context.on("download", _on_dl)
        page.context.on("page", _on_pg)
        try:
            await btn.click(force=True, timeout=5_000)
        except Exception as e:
            print(f"[buzzy] {label} click: {e}")
            page.context.remove_listener("download", _on_dl)
            page.context.remove_listener("page", _on_pg)
            return None

        async def _await_dl():
            return await dl_fut

        async def _await_pg():
            return await pg_fut

        dl_task = _asyncio.create_task(_await_dl())
        pg_task = _asyncio.create_task(_await_pg())
        done, pending = await _asyncio.wait(
            [dl_task, pg_task], timeout=10.0, return_when=_asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        page.context.remove_listener("download", _on_dl)
        page.context.remove_listener("page", _on_pg)

        # Handle download event
        if dl_task in done:
            try:
                dl = dl_task.result()
                with tempfile.NamedTemporaryFile(
                    suffix=Path(dl.suggested_filename).suffix or ".png", delete=False
                ) as tmp:
                    tmp_path = tmp.name
                await dl.save_as(tmp_path)
                data = Path(tmp_path).read_bytes()
                Path(tmp_path).unlink(missing_ok=True)
                if len(data) > 5_000:
                    print(f"[buzzy] {len(data)//1024} KB via download-event ({label})")
                    return data
            except Exception as e:
                print(f"[buzzy] {label} dl-event handle: {e}")

        # Handle new tab that opened with the image URL
        if pg_task in done:
            try:
                new_pg = pg_task.result()
                await new_pg.wait_for_load_state("domcontentloaded", timeout=15_000)
                img_url = new_pg.url
                print(f"[buzzy] {label}: new tab → {img_url[:100]}")
                raw_b64 = await new_pg.evaluate(
                    """async () => {
                        try {
                            const resp = await fetch(location.href, {credentials: 'include'});
                            if (!resp.ok) return null;
                            const buf = await resp.arrayBuffer();
                            const u8  = new Uint8Array(buf);
                            let s = '';
                            for (let i = 0; i < u8.length; i++)
                                s += String.fromCharCode(u8[i]);
                            return btoa(s);
                        } catch (e) { return null; }
                    }"""
                )
                await new_pg.close()
                if raw_b64:
                    import base64 as _b64
                    raw = _b64.b64decode(raw_b64)
                    if len(raw) > 5_000:
                        print(f"[buzzy] {len(raw)//1024} KB from new-tab ({label})")
                        return raw
            except Exception as e:
                print(f"[buzzy] {label} new-tab handle: {e}")

        print(f"[buzzy] {label}: no download or new-tab within 10s")
        return None

    async def _click_toolbar_download_btn() -> Optional[bytes]:
        """Find the ↓ download icon button to the right of 'Run this' in the toolbar
        and click it.

        Toolbar layout (confirmed from user screenshots):
          ► Run this  |  [copy/duplicate]  |  [↓ download]  |  [↗ expand/fullscreen]

        Rules:
        • SKIP any element whose aria-label/title/class contains expand, fullscreen,
          maximize, zoom — clicking those navigates away from Buzzy entirely.
        • PREFER any element whose aria-label/title contains download or save.
        • If no explicit download label, use the SECOND icon (index 1, 0-based) since
          index 0 is copy and index 2 is expand.
        • Also try page.mouse.click at the exact coordinates as a fallback.
        """
        try:
            meta = await page.evaluate(
                """() => {
                    const sel = 'button, a, [role="button"], [onclick], ' +
                                '[class*="btn"], [class*="icon-btn"], [class*="toolbar"]';
                    const allEls = Array.from(document.querySelectorAll(sel));

                    const allBtns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    let anchor = allBtns.find(b => (b.textContent || '').trim() === 'Run this');
                    if (!anchor) {
                        anchor = allBtns.find(b =>
                            (b.textContent || '').trim().toLowerCase().includes('share')
                        );
                    }
                    if (!anchor) return [];

                    const aBox = anchor.getBoundingClientRect();
                    return allEls
                        .filter(b => {
                            const box = b.getBoundingClientRect();
                            return box.left > aBox.right - 5 &&
                                   Math.abs(box.top - aBox.top) < 35 &&
                                   box.width > 0 && box.width < 70 &&
                                   box.height > 0;
                        })
                        .map(b => {
                            const box = b.getBoundingClientRect();
                            return {
                                x:         box.left,
                                y:         box.top,
                                width:     box.width,
                                height:    box.height,
                                tag:       b.tagName,
                                text:      (b.textContent || '').trim().slice(0, 30),
                                ariaLabel: b.getAttribute('aria-label') || '',
                                title:     b.getAttribute('title') || '',
                                cls:       (b.className || '').slice(0, 60),
                            };
                        });
                }"""
            )
            print(f"[buzzy] toolbar icons after 'Run this': {meta}")

            if not meta:
                return None

            sorted_meta = sorted(meta, key=lambda d: d["x"])

            # Filter out expand/fullscreen/copy buttons — only keep download candidates
            _SKIP_WORDS  = ("expand", "fullscreen", "maximize", "zoom", "copy", "duplicate", "share")
            _DL_WORDS    = ("download", "save", "export")

            filtered = []
            for info in sorted_meta:
                combined = (info.get("ariaLabel","") + " " +
                            info.get("title","") + " " +
                            info.get("cls","") + " " +
                            info.get("text","")).lower()
                if any(w in combined for w in _SKIP_WORDS):
                    print(f"[buzzy] skipping toolbar icon (looks like expand/copy): {info}")
                    continue
                filtered.append(info)

            # Prioritise any icon explicitly labelled "download"
            def _dl_priority(info):
                combined = (info.get("ariaLabel","") + " " + info.get("title","")).lower()
                return 0 if any(w in combined for w in _DL_WORDS) else 1

            # If we have ≥2 remaining icons the second (index 1) is the download button
            # based on the confirmed toolbar layout: [copy(0)] [download(1)] [expand(2)]
            # The expand should have been filtered above, so index 0 here is likely copy
            # and index 1 is download.  Sort download-labelled first, then by position.
            ordered = sorted(filtered, key=lambda info: (_dl_priority(info), info["x"]))

            # Toolbar order: [copy(0)] [↓download(1)] [↗expand(2)]
            # Try the DOWNLOAD button (index 1) first, then copy (index 0) as fallback.
            # Never try index 2+ (expand navigates away).
            # Use response interception as primary — download events never fire on Buzzy.
            indices_to_try = ([1, 0] if len(ordered) >= 2 else [0])
            for idx in indices_to_try:
                if idx >= len(ordered):
                    continue
                info = ordered[idx]
                cx = info["x"] + info["width"] / 2
                cy = info["y"] + info["height"] / 2
                lbl = f"toolbar[{idx}]@({cx:.0f},{cy:.0f})"
                print(f"[buzzy] clicking {lbl}")

                # ── Response interception: capture any new image HTTP response ──────
                # Buzzy may fetch the image over the network when ↓ is clicked.
                intercepted: list = []

                async def _on_resp(response, _lbl=lbl):
                    try:
                        ct = response.headers.get("content-type", "")
                        if "image/" in ct and "svg" not in ct:
                            body = await response.body()
                            if len(body) > 30_000:
                                intercepted.append(body)
                                print(f"[buzzy] {_lbl} intercepted {len(body)//1024}KB "
                                      f"from {response.url[:80]}")
                    except Exception:
                        pass

                page.on("response", _on_resp)

                # ── Also arm download + new-tab futures ───────────────────────────
                loop = _asyncio.get_event_loop()
                dl_fut3: _asyncio.Future = loop.create_future()
                pg_fut3: _asyncio.Future = loop.create_future()

                def _on_dl3(dl):
                    if not dl_fut3.done(): dl_fut3.set_result(dl)

                def _on_pg3(pg):
                    if not pg_fut3.done(): pg_fut3.set_result(pg)

                page.context.on("download", _on_dl3)
                page.context.on("page",     _on_pg3)

                await page.mouse.click(cx, cy)

                async def _aw_dl3(): return await dl_fut3
                async def _aw_pg3(): return await pg_fut3
                dl_t3 = _asyncio.create_task(_aw_dl3())
                pg_t3 = _asyncio.create_task(_aw_pg3())
                done3, pend3 = await _asyncio.wait(
                    [dl_t3, pg_t3], timeout=8.0,
                    return_when=_asyncio.FIRST_COMPLETED
                )
                for t in pend3: t.cancel()
                page.context.remove_listener("download", _on_dl3)
                page.context.remove_listener("page",     _on_pg3)
                page.remove_listener("response", _on_resp)

                # ── Check response interception first ────────────────────────────
                if intercepted:
                    best_body = max(intercepted, key=len)
                    print(f"[buzzy] {len(best_body)//1024} KB via response-intercept ({lbl})")
                    return best_body

                # ── Check download event ─────────────────────────────────────────
                if dl_t3 in done3:
                    try:
                        dl = dl_t3.result()
                        with tempfile.NamedTemporaryFile(
                            suffix=Path(dl.suggested_filename).suffix or ".png",
                            delete=False
                        ) as tmp:
                            tmp_path = tmp.name
                        await dl.save_as(tmp_path)
                        data = Path(tmp_path).read_bytes()
                        Path(tmp_path).unlink(missing_ok=True)
                        if len(data) > 5_000:
                            print(f"[buzzy] {len(data)//1024} KB via download-event ({lbl})")
                            return data
                    except Exception as e:
                        print(f"[buzzy] {lbl} dl-event: {e}")

                # ── Check new tab ────────────────────────────────────────────────
                if pg_t3 in done3:
                    try:
                        new_pg = pg_t3.result()
                        await new_pg.wait_for_load_state("domcontentloaded", timeout=15_000)
                        tab_url = new_pg.url
                        print(f"[buzzy] {lbl}: new tab → {tab_url[:80]}")
                        raw_b64 = await new_pg.evaluate(
                            """async () => {
                                try {
                                    const r = await fetch(location.href, {credentials:'include'});
                                    if (!r.ok) return null;
                                    const buf = await r.arrayBuffer();
                                    const u8 = new Uint8Array(buf);
                                    let s='';
                                    for (let i=0;i<u8.length;i++) s+=String.fromCharCode(u8[i]);
                                    return btoa(s);
                                } catch(e){return null;}
                            }"""
                        )
                        await new_pg.close()
                        if raw_b64:
                            import base64 as _b64
                            raw = _b64.b64decode(raw_b64)
                            if len(raw) > 5_000:
                                print(f"[buzzy] {len(raw)//1024} KB from new-tab ({lbl})")
                                return raw
                    except Exception as e:
                        print(f"[buzzy] {lbl} new-tab: {e}")

                print(f"[buzzy] {lbl}: nothing captured")
        except Exception as e:
            print(f"[buzzy] _click_toolbar_download_btn: {e}")
        return None

    # ── Strategy 0: click the image box → then immediately try the toolbar ↓ ──
    # User confirmed: clicking the image reveals a download button in the
    # top-right toolbar (next to "Share").  Do both in one combined step.
    try:
        clicked_box = False
        best_img_el = None  # keep a handle for the screenshot fallback

        # First: scan all images by natural dimensions to find the real AI image
        # (largest naturalWidth × naturalHeight = raw 1024px AI image, not Buzzy's card thumbnail)
        raw_img_el = await _find_best_raw_img_el()

        # Click the largest visible img to reveal the toolbar — fall back to old selector loop
        for sel in [
            "[class*='canvas'] img",
            "[class*='canvas-image']",
            "[class*='project-image']",
            "[class*='result-image']",
            "[class*='generated'] img",
            "[class*='chat'] img",
            "[class*='message'] img",
            ".image-card img",
            "img[src*='buzzy']",
            "img[src*='cdn']",
            "img",
        ]:
            try:
                els = await page.query_selector_all(sel)
                best_el, best_area = None, 0
                for el in els:
                    if not await el.is_visible():
                        continue
                    box = await el.bounding_box()
                    if box:
                        area = box["width"] * box["height"]
                        if area > best_area and box["width"] > 80:
                            best_area = area
                            best_el = el
                if best_el:
                    best_img_el = best_el
                    await best_el.click(force=True, timeout=5_000)
                    clicked_box = True
                    print(f"[buzzy] clicked image box via {sel} (area={best_area:.0f})")
                    await page.wait_for_timeout(1_500)
                    break
            except Exception:
                pass

        if clicked_box:
            await _snap(page, "buzzy-after-box-click", snap)

            # ① Direct URL download — prefer the raw AI image found by natural dimensions
            dl_el = raw_img_el or best_img_el
            if dl_el:
                result = await _download_img_via_requests(dl_el)
                if result:
                    return result

            # ② Try the toolbar download icon (↓ button)
            result = await _click_toolbar_download_btn()
            if result:
                return result

            # ③ element.screenshot() on the image handle — captures exactly the img element
            if best_img_el:
                result = await _element_screenshot_bytes(best_img_el)
                if result:
                    await _snap(page, "buzzy-element-screenshot", snap)
                    return result

            # ④ Named selectors as backup
            for sel in [
                "button[aria-label*='download' i]",
                "[title*='download' i]",
                "button:has-text('Download')",
                "a[download]",
                "a[href$='.png']",
                "a[href$='.jpg']",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        result = await _try_download_click(el, f"post-box-click '{sel}'")
                        if result:
                            return result
                except Exception:
                    pass
    except Exception as e:
        print(f"[buzzy] strategy 0 failed: {e}")

    # ── Strategy 1: toolbar icon buttons (without box click) ─────────────────
    result = await _click_toolbar_download_btn()
    if result:
        return result

    # ── Strategy 2: aria-label/title download button or <a download> link ─────
    for sel in [
        "button[aria-label*='download' i]",
        "[title*='download' i]",
        "a[download]",
        "a[href$='.png']",
        "a[href$='.jpg']",
        "a[href$='.webp']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                result = await _try_download_click(el, f"aria-sel '{sel}'")
                if result:
                    return result
        except Exception:
            pass

    # ── Strategy 3: browser fetch() of the largest <img> src ─────────────────
    # No offsetParent check — the image may not be in normal document flow.
    try:
        img_data_uri = await page.evaluate(
            """async () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                let best = null, bestArea = 0;
                for (const img of imgs) {
                    const src = img.src || img.currentSrc || '';
                    if (!src || src.startsWith('data:')) continue;
                    const r = img.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area > bestArea && r.width > 100) {
                        bestArea = area;
                        best = img;
                    }
                }
                if (!best) {
                    // also check CSS background-image URLs
                    const els = Array.from(document.querySelectorAll('*'));
                    for (const el of els) {
                        const bg = getComputedStyle(el).backgroundImage;
                        if (bg && bg.startsWith('url(')) {
                            const url = bg.slice(5, -2).replace(/['"]/g, '');
                            if (url && !url.startsWith('data:')) {
                                try {
                                    const r2 = el.getBoundingClientRect();
                                    if (r2.width * r2.height > bestArea) {
                                        bestArea = r2.width * r2.height;
                                        best = {_bgUrl: url};
                                    }
                                } catch (_) {}
                            }
                        }
                    }
                }
                if (!best) return null;
                const src = best._bgUrl || best.src || best.currentSrc;
                try {
                    const resp = await fetch(src, {credentials: 'include'});
                    if (!resp.ok) return null;
                    const blob = await resp.blob();
                    return await new Promise((res, rej) => {
                        const r = new FileReader();
                        r.onload  = () => res(r.result);
                        r.onerror = () => rej(null);
                        r.readAsDataURL(blob);
                    });
                } catch (e) { return null; }
            }"""
        )
        if img_data_uri and "," in img_data_uri:
            import base64 as _b64
            raw = _b64.b64decode(img_data_uri.split(",", 1)[1])
            if len(raw) > 5_000:
                print(f"[buzzy] {len(raw)//1024} KB via browser fetch")
                return raw
        print("[buzzy] browser fetch: no usable image")
    except Exception as e:
        print(f"[buzzy] browser fetch failed: {e}")

    # ── Strategy 4: click every visible small button on the page ─────────────
    all_buttons = await page.query_selector_all("button")
    for btn in all_buttons:
        try:
            box = await btn.bounding_box()
            if not box or box["width"] > 80:
                continue   # skip large text buttons
            txt = (await btn.text_content() or "").strip()
            if any(w in txt.lower() for w in ("create", "video", "agent", "upgrade", "share", "new")):
                continue
            result = await _try_download_click(btn, f"small-btn '{txt or '?'}'")
            if result:
                return result
        except Exception:
            pass

    # ── Strategy 5: element.screenshot() / page screenshot fallback ─────────
    # All download strategies failed.  Try screenshotting the image element
    # directly, then fall back to a full viewport screenshot.
    await progress("📸 Download unavailable — screenshotting the image box…")
    print("[buzzy] all download strategies failed — trying element screenshot fallback")
    try:
        current_url = page.url
        if "buzzy.now" not in current_url:
            print(f"[buzzy] screenshot fallback: not on buzzy.now (url={current_url[:60]}) — navigating back")
            await page.go_back(timeout=10_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2_000)
            if "buzzy.now" not in page.url:
                print("[buzzy] go_back failed — raising instead of screenshotting wrong page")
                raise RuntimeError(f"Buzzy: browser navigated away from buzzy.now to {current_url[:80]}")

        # ① Use the element handle we already found (best_img_el from Strategy 0)
        if best_img_el:
            result = await _element_screenshot_bytes(best_img_el)
            if result:
                await _snap(page, "buzzy-element-screenshot-fallback", snap)
                return result

        # ② Re-scan for the largest img/canvas and element-screenshot it
        for sel in ["img[src*='buzzy']", "img[src*='cdn']", "img[src*='storage']", "img"]:
            try:
                els = await page.query_selector_all(sel)
                best_el2, best_area2 = None, 0
                for el in els:
                    if not await el.is_visible():
                        continue
                    box = await el.bounding_box()
                    if box:
                        src = await el.evaluate("e => e.src || ''")
                        if src.endswith(".svg") or "icon" in src:
                            continue
                        area = box["width"] * box["height"]
                        if area > best_area2 and box["width"] > 80:
                            best_area2 = area
                            best_el2 = el
                if best_el2:
                    result = await _element_screenshot_bytes(best_el2)
                    if result:
                        await _snap(page, "buzzy-element-screenshot-fallback", snap)
                        return result
                    break
            except Exception:
                pass

        # ③ Last resort: full viewport screenshot (still on buzzy.now)
        print("[buzzy] element screenshot failed — falling back to full viewport screenshot")
        vp_bytes = await page.screenshot(type="png", full_page=False)
        await _snap(page, "buzzy-viewport-screenshot-fallback", snap)
        if len(vp_bytes) > 5_000:
            print(f"[buzzy] viewport screenshot: {len(vp_bytes)//1024} KB")
            return vp_bytes

    except Exception as e:
        print(f"[buzzy] screenshot fallback failed: {e}")

    labels = await _list_visible_labels(page)
    print(f"[buzzy] all strategies failed — visible labels: {labels}")
    await _snap(page, "buzzy-no-download-btn", snap)
    raise RuntimeError("Buzzy: could not download or screenshot the generated image")


async def _download_from_folder(
    page: Page, mode: str, progress: ProgressCB, snap: Optional[ScreenshotCB]
) -> bytes:
    """Click the generated item in the folder, then download it."""
    await progress("⬇️ Selecting and downloading…")

    # Click the first result item in the folder
    await _click_first(page, [
        "[class*='result']:first-child",
        "[class*='item']:first-child",
        "[class*='thumbnail']:first-child",
        "img:first-of-type",
        "video:first-of-type",
        "[class*='grid'] > *:first-child",
    ], timeout=5000)
    await page.wait_for_timeout(1_500)
    await _snap(page, f"buzzy-{mode}-selected", snap)

    # Download
    ext = ".mp4" if mode == "video" else ".png"
    async with page.expect_download(timeout=90_000) as dl_info:
        downloaded = await _click_first(page, [
            "button:has-text('Download')",
            "a:has-text('Download')",
            "[aria-label*='download' i]",
            "[data-testid*='download']",
            "[class*='download']",
        ], timeout=5000)
        if not downloaded:
            print("[buzzy] WARNING: download button not found")

    download = await dl_info.value
    with tempfile.NamedTemporaryFile(
        suffix=Path(download.suggested_filename).suffix or ext, delete=False
    ) as tmp:
        tmp_path = tmp.name

    await download.save_as(tmp_path)
    data = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    print(f"[buzzy] downloaded {len(data)//1024} KB → {download.suggested_filename}")
    return data


# ── Main entry points ─────────────────────────────────────────────────────────

async def _run(
    prompt: str,
    mode: str,
    model: str,
    progress_cb: ProgressCB,
    screenshot_cb: Optional[ScreenshotCB],
) -> bytes:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            ,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox", "--no-zygote"],
        )
        mail_ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        buzzy_ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
        )
        try:
            mail_page  = await mail_ctx.new_page()
            buzzy_page = await buzzy_ctx.new_page()

            email = await _get_temp_email(mail_page, progress_cb, screenshot_cb)
            await _login_buzzy(buzzy_page, email, progress_cb, screenshot_cb)
            code  = await _get_verification_code(mail_page, progress_cb, screenshot_cb)
            await _enter_code_buzzy(buzzy_page, code, progress_cb, screenshot_cb)

            if mode == "image":
                return await _generate_image_buzzy(
                    buzzy_page, prompt, model, progress_cb, screenshot_cb
                )
            else:
                return await _generate_video_buzzy(
                    buzzy_page, prompt, model, progress_cb, screenshot_cb
                )
        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


async def generate_buzzy_image(
    prompt: str,
    model: str = "Nano Banana",
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    async def _noop(msg): pass
    return await _run(prompt, "image", model, progress_cb or _noop, screenshot_cb)


async def generate_buzzy_video(
    prompt: str,
    model: str = "Google Omni",
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    async def _noop(msg): pass
    return await _run(prompt, "video", model, progress_cb or _noop, screenshot_cb)
