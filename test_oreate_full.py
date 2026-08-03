"""
Full end-to-end test — temp email → signup → wait for verification email →
click verify link → screenshot the logged-in dashboard → try to discover the
AI Image navigation/prompt/generate selectors.

Run: OREATE_PASSWORD=... python3 test_oreate_full.py
"""
import asyncio, os, re, shutil, time
from pathlib import Path
from playwright.async_api import async_playwright

SHOTS = Path("screenshots"); SHOTS.mkdir(exist_ok=True)
_PASSWORD = os.environ.get("OREATE_PASSWORD", "")
_CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or None

shot_n = 0
async def shot(page, label):
    global shot_n; shot_n += 1
    p = SHOTS / f"{shot_n:02d}_{label}.png"
    try:
        await page.screenshot(path=str(p), full_page=False)
        print(f"  📸 {p}")
    except Exception as e:
        print(f"  ⚠️ screenshot failed ({label}): {e}")


async def main():
    if not _PASSWORD:
        print("❌ Set OREATE_PASSWORD"); return

    kw = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    if _CHROMIUM:
        kw["executable_path"] = _CHROMIUM

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kw)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        mail = await ctx.new_page()
        opage = await ctx.new_page()

        # ── 1. mailticking ────────────────────────────────────────────────────
        print("\n[1] mailticking.com")
        await mail.goto("https://mailticking.com/", wait_until="domcontentloaded", timeout=30_000)
        await mail.wait_for_timeout(2_500)

        email = ""
        for sel in ["input[placeholder*='Gmail' i]", "input[placeholder*='address' i]",
                    "input[type='text']", "input[readonly]", "input"]:
            try:
                for el in await mail.locator(sel).all():
                    v = (await el.input_value(timeout=1_500)).strip()
                    if "@" in v:
                        email = v; break
                if email:
                    break
            except Exception:
                continue

        print(f"  {'✅' if email else '❌'} Email: {email or 'NOT FOUND'}")
        if not email:
            await browser.close(); return

        try:
            await mail.click("button:has-text('Activate')", timeout=3_000)
        except Exception:
            pass

        # ── 2. Oreate AI sign-up ──────────────────────────────────────────────
        print("\n[2] Oreate AI sign-up")
        await opage.goto("https://www.oreateai.com/", wait_until="networkidle", timeout=40_000)
        await opage.wait_for_timeout(3_000)
        await opage.click("text=Log in", timeout=10_000)
        await opage.wait_for_timeout(2_000)

        await opage.click("#form_item_email", timeout=6_000)
        await opage.wait_for_timeout(300)
        await opage.type("#form_item_email", email, delay=45)
        await opage.wait_for_timeout(500)

        await opage.click("#form_item_password", timeout=6_000)
        await opage.wait_for_timeout(300)
        await opage.type("#form_item_password", _PASSWORD, delay=45)
        await opage.wait_for_timeout(500)

        try:
            await opage.click("#form_item_check", timeout=3_000)
        except Exception:
            pass
        await opage.wait_for_timeout(300)
        checked = await opage.evaluate("() => document.querySelector('#form_item_check')?.checked")
        if not checked:
            try:
                await opage.click("#form_item_check", timeout=3_000, force=True)
            except Exception:
                pass
        await shot(opage, "form_filled")

        print("  🖱️  Clicking Create Account…")
        await opage.click("button:has-text('Create Account')", timeout=8_000)
        await opage.wait_for_timeout(4_000)
        await shot(opage, "after_submit")

        pg_text = await opage.inner_text("body", timeout=5_000)
        if "already exists" in pg_text.lower() or "already registered" in pg_text.lower():
            print(f"  ❌ Account rejected: {pg_text[:300]}")
            await browser.close(); return
        print("  ✅ Submitted (ignoring any 'Invalid parameter' toast — known frontend bug)")

        # ── 3. Wait for verification email ──────────────────────────────────
        print("\n[3] Waiting for verification email (deadline 200s)…", flush=True)
        verify_url = None
        t0 = time.monotonic()
        deadline = t0 + 200
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            # NEVER reload/goto here — mailticking.com hands out a brand-new
            # random temp email on every fresh page load, which silently
            # breaks verification. Use the in-page "Refresh" sidebar control.
            try:
                await mail.click("text=Refresh", timeout=3_000)
            except Exception:
                pass
            await mail.wait_for_timeout(3_000)
            try:
                await mail.click("button:has-text('Activate')", timeout=800)
            except Exception:
                pass

            for row_sel in ["li:has-text('Oreate')", "tr:has-text('Oreate')"]:
                try:
                    await mail.click(row_sel, timeout=800)
                    await mail.wait_for_timeout(1_000)
                    break
                except Exception:
                    continue

            content = await mail.content()
            for u in re.findall(r'https?://[^\s"\'<>]+', content):
                if any(k in u.lower() for k in ("verify", "confirm", "activate")) and "oreateai" in u.lower():
                    verify_url = u; break
            if not verify_url:
                for u in re.findall(r'https?://[^\s"\'<>]+', content):
                    if any(k in u.lower() for k in ("verify", "confirm")):
                        verify_url = u; break

            elapsed = int(time.monotonic() - t0)
            if verify_url:
                print(f"  ✅ Verification link found after {elapsed}s (attempt {attempt}): {verify_url[:90]}…", flush=True)
                break
            print(f"  ⏳ still waiting… {elapsed}s (attempt {attempt})", flush=True)

        await shot(mail, "inbox_state")

        if not verify_url:
            print("  ❌ Verification email did not arrive in time")
            await browser.close(); return

        # ── 4. Click verification link, land on dashboard ───────────────────
        print("\n[4] Verifying email + exploring dashboard")
        await opage.goto(verify_url, wait_until="domcontentloaded", timeout=30_000)
        await opage.wait_for_timeout(4_000)
        await shot(opage, "post_verify")

        body_text = await opage.inner_text("body", timeout=5_000)
        print(f"  📄 Post-verify body snippet: {body_text[:200]!r}")

        # Go to homepage / logged-in dashboard
        await opage.goto("https://www.oreateai.com/", wait_until="networkidle", timeout=30_000)
        await opage.wait_for_timeout(3_000)
        await shot(opage, "dashboard_home")

        # Dump nav-like text to help find AI Image / AI Video selectors
        try:
            nav_texts = await opage.evaluate("""() => {
                const els = document.querySelectorAll('a, button, [role="menuitem"], nav *');
                const seen = new Set();
                const out = [];
                for (const el of els) {
                    const t = (el.innerText || '').trim();
                    if (t && t.length < 40 && !seen.has(t)) { seen.add(t); out.push(t); }
                    if (out.length > 60) break;
                }
                return out;
            }""")
            print(f"  🔍 Nav/button text sample: {nav_texts}")
        except Exception as e:
            print(f"  ⚠️ nav text scrape failed: {e}")

        # Try clicking something that looks like AI Image
        for sel in ["text=AI Image", "text=Image", "a[href*='image']", "text=Nano Banana"]:
            try:
                await opage.click(sel, timeout=4_000)
                await opage.wait_for_timeout(2_500)
                print(f"  ✅ Clicked '{sel}' successfully")
                await shot(opage, "ai_image_page")
                break
            except Exception:
                continue

        print(f"\n✅ Done — {shot_n} screenshots in ./screenshots/")
        await browser.close()

asyncio.run(main())
