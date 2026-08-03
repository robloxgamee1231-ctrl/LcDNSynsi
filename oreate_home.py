"""
oreate_home.py — Alternative Oreate AI automation strategy

Instead of navigating straight to the signup form, this module:
  1. Gets a temp Gmail address from tempgbox.net (no + aliases)
  2. Opens https://www.oreateai.com/home/index
  3. Types the user's prompt into the homepage input bar (unauthenticated)
  4. Attempts to generate — Oreate shows a "Sign up / Log in" modal
  5. Signs up right there (in-context signup, avoids "Invalid parameter")
  6. Verifies the account via tempgbox inbox
  7. Re-submits the prompt and waits for the result

This approach is more resilient because:
  - The signup is triggered by a real user action (generation attempt)
  - The site is less likely to reject the email with "Invalid parameter"
  - No standalone signup page navigation needed

Public API (same as oreate_bot.py):
  image_bytes = await generate_oreate_image(prompt, progress_cb=..., screenshot_cb=...)
  video_bytes = await generate_oreate_video(prompt, progress_cb=..., screenshot_cb=...)
"""

import asyncio
import re
from typing import Callable, Awaitable

# Re-use all the shared helpers from oreate_bot to avoid duplication
from oreate_bot import (
    _cb,
    _screenshot,
    _new_context,
    _new_mobile_context,
    _click_first,
    _fill_first,
    _fill_react_first,
    _type_slow,
    _dismiss_popups,
    _get_temp_email,
    _verify_email,
    _password,
    _InvalidParameterError,
    _MAX_SIGNUP_RETRIES,
    _generate_image_on_page,
    _generate_video_on_page,
    _OREATE_URL,
)

_OREATE_HOME = "https://www.oreateai.com/home/index"


# ── Step 1+2: get email + open home/index ─────────────────────────────────────

async def _open_home(oreate_page, screenshot_cb=None) -> None:
    """Navigate to Oreate's homepage (shows the prompt bar without login)."""
    print("[home] navigating to oreateai.com/home/index")
    await oreate_page.goto(_OREATE_HOME, wait_until="domcontentloaded", timeout=40_000)
    await oreate_page.wait_for_timeout(2_500)
    await _dismiss_popups(oreate_page)  # close any cookie/ad banner on landing
    await oreate_page.wait_for_timeout(400)
    await _screenshot(oreate_page, "🌐 Oreate home/index loaded", screenshot_cb)


# ── Step 3: type prompt in the homepage input bar ────────────────────────────

async def _type_home_prompt(oreate_page, prompt: str, screenshot_cb=None) -> None:
    """Find the unauthenticated prompt input on home/index and type the prompt."""
    print(f"[home] typing prompt: {prompt[:60]!r}")
    typed = False
    for sel in [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea[placeholder*='create' i]",
        "textarea[placeholder*='imagine' i]",
        "textarea[placeholder*='type' i]",
        "input[placeholder*='prompt' i]",
        "input[placeholder*='describe' i]",
        "input[placeholder*='create' i]",
        "input[placeholder*='imagine' i]",
        "textarea",
        "input[type='text']",
    ]:
        try:
            el = oreate_page.locator(sel).first
            await el.click(timeout=5_000)
            await el.fill(prompt)
            typed = True
            print(f"[home] prompt typed via {sel!r}")
            break
        except Exception:
            continue
    if not typed:
        await _screenshot(oreate_page, "❌ home: prompt input not found", screenshot_cb)
        raise RuntimeError("oreateai.com/home/index: could not find prompt input")
    await _screenshot(oreate_page, "📝 Prompt typed — clicking Generate", screenshot_cb)


# ── Step 4: click generate / submit to trigger the auth modal ─────────────────

async def _click_home_generate(oreate_page, screenshot_cb=None) -> None:
    """Click the generate button (unauthenticated). Oreate should show a signup modal."""
    print("[home] clicking Generate (unauthenticated) to trigger auth modal…")
    for sel in [
        "button:has-text('Generate')",
        "button[type='submit']",
        "button:has-text('Create')",
        "button:has-text('Run')",
        "button:has-text('Go')",
        "button[aria-label*='generate' i]",
        "button[aria-label*='submit' i]",
        # Keyboard fallback: pressing Enter in the textarea
    ]:
        try:
            await oreate_page.locator(sel).first.click(timeout=5_000)
            print(f"[home] Generate clicked via {sel!r}")
            await oreate_page.wait_for_timeout(2_500)
            await _screenshot(oreate_page, "🖱️ Generate clicked — waiting for auth modal", screenshot_cb)
            return
        except Exception:
            continue

    # Last resort: Enter key
    print("[home] button not found — pressing Enter in textarea")
    try:
        await oreate_page.keyboard.press("Enter")
        await oreate_page.wait_for_timeout(2_500)
        await _screenshot(oreate_page, "⌨️ Enter pressed — waiting for auth modal", screenshot_cb)
    except Exception:
        pass


# ── Step 5: fill in the auth modal that Oreate pops up ───────────────────────

async def _fill_auth_modal(
    oreate_page,
    email: str,
    password: str,
    progress_cb=None,
    screenshot_cb=None,
) -> None:
    """Wait for the signup/login modal, switch to Sign Up if needed, and fill the form."""
    print("[home] waiting for Oreate auth modal…")
    await _cb(progress_cb, "📝 Filling in Oreate AI sign-up form…")

    # Wait up to 15 s for a modal / dialog to appear
    modal_appeared = False
    for _ in range(15):
        try:
            modal = oreate_page.locator(
                ".modal, [role='dialog'], [class*='modal'], [class*='popup'], "
                "[class*='overlay'], [class*='auth'], [class*='signin'], [class*='signup']"
            ).first
            if await modal.is_visible(timeout=1_000):
                modal_appeared = True
                break
        except Exception:
            pass
        await oreate_page.wait_for_timeout(1_000)

    if not modal_appeared:
        # Maybe the whole page changed to a sign-in page instead of a modal
        url = oreate_page.url
        print(f"[home] no modal detected — current URL: {url}")

    await _dismiss_popups(oreate_page)  # clear any ad overlay before interacting with the modal
    await _screenshot(oreate_page, "📋 Auth modal/page visible", screenshot_cb)

    # If modal has a "Sign Up" tab / link, click it
    for su_sel in [
        "text=Sign up",
        "text=Sign Up",
        "text=Register",
        "a:has-text('Sign up')",
        "button:has-text('Sign up')",
        "[class*='signup-tab']",
        "[class*='register-tab']",
    ]:
        try:
            await oreate_page.locator(su_sel).first.click(timeout=3_000)
            print(f"[home] switched to Sign Up tab via {su_sel!r}")
            await oreate_page.wait_for_timeout(1_000)
            break
        except Exception:
            continue

    await _screenshot(oreate_page, "📋 Sign Up tab selected", screenshot_cb)

    # Fill email — React-aware: type char-by-char so onChange fires and
    # the controlled input's internal state matches what we typed
    await _fill_react_first(
        oreate_page,
        [
            "input[type='email']",
            "input[name='email']",
            "input[placeholder='Email']",
            "input[placeholder*='email' i]",
            "input[id*='email' i]",
        ],
        email,
        label="email field",
    )
    await oreate_page.wait_for_timeout(400)

    # Fill password — same React-aware approach
    await _fill_react_first(
        oreate_page,
        [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder='Password']",
            "input[placeholder*='password' i]",
            "input[id*='password' i]",
        ],
        password,
        label="password field",
    )
    await oreate_page.wait_for_timeout(400)

    # Accept terms checkbox (if present)
    for cb_sel in [
        "input[type='checkbox']",
        "[class*='terms'] input",
        "[class*='agree'] input",
        "label:has-text('Terms') input",
        "label:has-text('agree') input",
    ]:
        try:
            cb = oreate_page.locator(cb_sel).first
            if not await cb.is_checked(timeout=1_000):
                await cb.check(timeout=3_000)
                print(f"[home] terms checkbox checked via {cb_sel!r}")
            break
        except Exception:
            continue

    await _screenshot(oreate_page, "📋 Form filled — submitting Create Account", screenshot_cb)

    # Intercept "Invalid parameter" API response
    invalid_param_flag: list[bool] = [False]

    async def _on_response(resp) -> None:
        if resp.status not in (200, 201):
            return
        try:
            text = await resp.text()
            if "invalid parameter" in text.lower() or "invalid_parameter" in text.lower():
                invalid_param_flag[0] = True
        except Exception:
            pass

    oreate_page.on("response", _on_response)

    # Click Create Account
    await _click_first(
        oreate_page,
        [
            "button:has-text('Create Account')",
            "button:has-text('Sign Up')",
            "button:has-text('Register')",
            "button[type='submit']",
            "input[type='submit']",
        ],
        label="Create Account button",
    )
    await oreate_page.wait_for_timeout(4_000)
    await _screenshot(oreate_page, "🖱️ Create Account submitted — checking response", screenshot_cb)

    # Also check DOM for the "Invalid parameter" banner
    try:
        page_text = await oreate_page.inner_text("body", timeout=3_000)
        if "invalid parameter" in page_text.lower():
            invalid_param_flag[0] = True
    except Exception:
        pass

    if invalid_param_flag[0]:
        await _screenshot(oreate_page, "❌ Invalid parameter — retrying with fresh email", screenshot_cb)
        raise _InvalidParameterError("Oreate AI: Invalid parameter on home/index signup")

    print("[home] ✅ account created via home/index auth modal")


# ── Step 6+7: verify + re-submit prompt ──────────────────────────────────────

async def _submit_and_collect_image(oreate_page, prompt: str, progress_cb=None, screenshot_cb=None) -> bytes:
    """After verification, re-submit the prompt and wait for the image."""
    await _cb(progress_cb, "🖼️ Re-submitting prompt on home/index…")
    try:
        await oreate_page.goto(_OREATE_HOME, wait_until="domcontentloaded", timeout=30_000)
        await oreate_page.wait_for_timeout(2_000)
    except Exception:
        pass

    # Try the dedicated image generation flow first (logged in now)
    return await _generate_image_on_page(oreate_page, prompt, progress_cb, screenshot_cb)


async def _submit_and_collect_video(oreate_page, prompt: str, image_bytes: bytes | None, progress_cb=None, screenshot_cb=None) -> bytes:
    """After verification, generate a video (re-uses the standard generation pipeline)."""
    await _cb(progress_cb, "🎬 Starting video generation…")
    ref = image_bytes
    if ref is None:
        await _cb(progress_cb, "🖼️ Generating reference image first…")
        ref = await _generate_image_on_page(oreate_page, prompt, progress_cb, screenshot_cb)
    return await _generate_video_on_page(oreate_page, prompt, ref, progress_cb, screenshot_cb)


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_oreate_image(
    prompt: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """home/index approach: type prompt → auth modal → sign up → verify → image."""
    pw_val = _password()
    print("[home] ════════════════════════════════════════")
    print("[home]  IMAGE — home/index prompt-first flow")
    print("[home] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()

                # ① Temp email (no + aliases)
                await _cb(progress_cb, "📧 Getting temporary Gmail from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email: {email}")

                # ② Open home/index + type prompt
                await _open_home(oreate_page, screenshot_cb)
                await _type_home_prompt(oreate_page, prompt, screenshot_cb)
                await _click_home_generate(oreate_page, screenshot_cb)

                # ③ Fill the auth modal that appears
                try:
                    await _fill_auth_modal(oreate_page, email, pw_val, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"home/index: 'Invalid parameter' after {attempt} attempts"
                        ) from exc
                    print(f"[home] ⚠️  Invalid parameter attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Email rejected (attempt {attempt}) — retrying…")
                    continue

                # ④ Verify email via tempgbox inbox
                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)

                # ⑤ Generate image (now authenticated)
                return await _submit_and_collect_image(oreate_page, prompt, progress_cb, screenshot_cb)

            finally:
                await browser.close()

    raise RuntimeError("home/index image generation failed — all attempts exhausted")


async def generate_oreate_video(
    prompt: str,
    image_bytes: bytes | None = None,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """home/index approach: type prompt → auth modal → sign up → verify → video."""
    pw_val = _password()
    print("[home] ════════════════════════════════════════")
    print("[home]  VIDEO — home/index prompt-first flow")
    print("[home] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()

                # ① Temp email (no + aliases)
                await _cb(progress_cb, "📧 Getting temporary Gmail from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email: {email}")

                # ② Open home/index + type prompt
                await _open_home(oreate_page, screenshot_cb)
                await _type_home_prompt(oreate_page, prompt, screenshot_cb)
                await _click_home_generate(oreate_page, screenshot_cb)

                # ③ Fill the auth modal
                try:
                    await _fill_auth_modal(oreate_page, email, pw_val, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"home/index: 'Invalid parameter' after {attempt} attempts"
                        ) from exc
                    print(f"[home] ⚠️  Invalid parameter attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Email rejected (attempt {attempt}) — retrying…")
                    continue

                # ④ Verify email
                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)

                # ⑤ Generate video (now authenticated)
                return await _submit_and_collect_video(oreate_page, prompt, image_bytes, progress_cb, screenshot_cb)

            finally:
                await browser.close()

    raise RuntimeError("home/index video generation failed — all attempts exhausted")


# ── Mobile variants (same flow, Pixel 5 viewport + touch UA) ─────────────────

async def generate_oreate_image_mobile(
    prompt: str,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """home/index flow using a mobile (Pixel 5) browser context."""
    pw_val = _password()
    print("[home/mobile] ════════════════════════════════════════")
    print("[home/mobile]  IMAGE — mobile Pixel 5 viewport")
    print("[home/mobile] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_mobile_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()

                await _cb(progress_cb, "📱 [Mobile] Getting temporary Gmail from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email: {email}")

                await _open_home(oreate_page, screenshot_cb)
                await _type_home_prompt(oreate_page, prompt, screenshot_cb)
                await _click_home_generate(oreate_page, screenshot_cb)

                try:
                    await _fill_auth_modal(oreate_page, email, pw_val, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"home/mobile: 'Invalid parameter' after {attempt} attempts"
                        ) from exc
                    print(f"[home/mobile] ⚠️  Invalid parameter attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Email rejected (attempt {attempt}) — retrying…")
                    continue

                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)
                return await _submit_and_collect_image(oreate_page, prompt, progress_cb, screenshot_cb)

            finally:
                await browser.close()

    raise RuntimeError("home/mobile image generation failed — all attempts exhausted")


async def generate_oreate_video_mobile(
    prompt: str,
    image_bytes: bytes | None = None,
    progress_cb: Callable[[str], Awaitable[None]] | None = None,
    screenshot_cb: Callable[[str, bytes], Awaitable[None]] | None = None,
) -> bytes:
    """home/index flow using a mobile (Pixel 5) browser context."""
    pw_val = _password()
    print("[home/mobile] ════════════════════════════════════════")
    print("[home/mobile]  VIDEO — mobile Pixel 5 viewport")
    print("[home/mobile] ════════════════════════════════════════")
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for attempt in range(1, _MAX_SIGNUP_RETRIES + 1):
            browser, context = await _new_mobile_context(pw)
            try:
                tgbox_page  = await context.new_page()
                oreate_page = await context.new_page()

                await _cb(progress_cb, "📱 [Mobile] Getting temporary Gmail from tempgbox.net…")
                email = await _get_temp_email(tgbox_page, screenshot_cb)
                await _cb(progress_cb, f"📧 Temp email: {email}")

                await _open_home(oreate_page, screenshot_cb)
                await _type_home_prompt(oreate_page, prompt, screenshot_cb)
                await _click_home_generate(oreate_page, screenshot_cb)

                try:
                    await _fill_auth_modal(oreate_page, email, pw_val, progress_cb, screenshot_cb)
                except _InvalidParameterError as exc:
                    if attempt >= _MAX_SIGNUP_RETRIES:
                        raise RuntimeError(
                            f"home/mobile: 'Invalid parameter' after {attempt} attempts"
                        ) from exc
                    print(f"[home/mobile] ⚠️  Invalid parameter attempt {attempt}/{_MAX_SIGNUP_RETRIES} — retrying")
                    await _cb(progress_cb, f"⚠️ Email rejected (attempt {attempt}) — retrying…")
                    continue

                await _verify_email(email, tgbox_page, oreate_page, progress_cb, screenshot_cb)
                return await _submit_and_collect_video(oreate_page, prompt, image_bytes, progress_cb, screenshot_cb)

            finally:
                await browser.close()

    raise RuntimeError("home/mobile video generation failed — all attempts exhausted")
