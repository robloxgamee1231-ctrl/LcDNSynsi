"""
Standalone test — full network interception, correct DOM IDs, React checkbox.
Run: OREATE_PASSWORD=... python3 test_oreate.py
"""
import asyncio, os, re, shutil
from pathlib import Path
from playwright.async_api import async_playwright

SHOTS = Path("screenshots"); SHOTS.mkdir(exist_ok=True)
_PASSWORD = os.environ.get("OREATE_PASSWORD", "")
_CHROMIUM  = shutil.which("chromium") or shutil.which("chromium-browser") or None

shot_n = 0
async def shot(page, label):
    global shot_n; shot_n += 1
    p = SHOTS / f"{shot_n:02d}_{label}.png"
    await page.screenshot(path=str(p), full_page=False)
    print(f"  📸 {p}")

_REACT_CHECKBOX_JS = """() => {
    const cb = document.querySelector('input[type="checkbox"]');
    if (!cb) return 'NOT_FOUND';
    // React overrides checked setter — use the native one to bypass
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
    nativeSetter.call(cb, true);
    cb.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
    cb.dispatchEvent(new Event('change', {bubbles:true}));
    return cb.checked ? 'CHECKED' : 'STILL_UNCHECKED';
}"""

async def main():
    if not _PASSWORD:
        print("❌ Set OREATE_PASSWORD"); return

    kw = dict(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
    if _CHROMIUM: kw["executable_path"] = _CHROMIUM

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kw)
        ctx = await browser.new_context(
            viewport={"width":1280,"height":900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        mail  = await ctx.new_page()
        opage = await ctx.new_page()

        # Intercept ALL POST/PUT/PATCH requests on oreate page
        api_log: list[dict] = []
        async def on_request(req):
            if req.method in ("POST","PUT","PATCH"):
                try:
                    post_data = req.post_data or ""
                    api_log.append({"type":"REQ","method":req.method,"url":req.url[-80:],"body":post_data[:200]})
                except: pass
        async def on_response(resp):
            if resp.request.method in ("POST","PUT","PATCH"):
                try:
                    body = await resp.text()
                    api_log.append({"type":"RESP","url":resp.url[-80:],"status":resp.status,"body":body[:300]})
                except: pass
        opage.on("request", on_request)
        opage.on("response", on_response)

        # ── 1. mailticking ────────────────────────────────────────────────────
        print("\n[1] mailticking.com")
        await mail.goto("https://mailticking.com/", wait_until="domcontentloaded", timeout=30_000)
        await mail.wait_for_timeout(2_500)

        # The email field has placeholder='Your Gmail address'
        email = ""
        for sel in ["input[placeholder*='Gmail' i]", "input[placeholder*='address' i]",
                    "input[type='text']", "input[readonly]", "input"]:
            try:
                for el in await mail.locator(sel).all():
                    v = (await el.input_value(timeout=1_500)).strip()
                    if "@" in v: email=v; break
                if email: break
            except: continue

        if not email:
            content = await mail.content()
            for m in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", content):
                if "oreate" not in m.lower() and "mailticking" not in m.lower():
                    email = m; break

        if not email:
            # Click Change/Refresh to trigger email generation
            for sel in ["button:has-text('Change')", "text=Refresh"]:
                try: await mail.click(sel, timeout=3_000); await mail.wait_for_timeout(2_000); break
                except: continue
            for sel in ["input[placeholder*='Gmail' i]", "input[type='text']", "input"]:
                try:
                    for el in await mail.locator(sel).all():
                        v = (await el.input_value(timeout=1_500)).strip()
                        if "@" in v: email=v; break
                    if email: break
                except: continue

        print(f"  {'✅' if email else '❌'} Email: {email or 'NOT FOUND'}")
        if not email: await browser.close(); return

        try: await mail.click("button:has-text('Activate')", timeout=3_000)
        except: pass
        await shot(mail, "mailticking")

        # ── 2. Oreate AI sign-up ──────────────────────────────────────────────
        print("\n[2] Oreate AI sign-up")
        await opage.goto("https://www.oreateai.com/", wait_until="networkidle", timeout=40_000)
        await opage.wait_for_timeout(3_000)

        # Human-like mouse movements
        await opage.mouse.move(400, 400); await opage.wait_for_timeout(300)
        await opage.mouse.move(600, 300); await opage.wait_for_timeout(200)

        await opage.click("text=Log in", timeout=10_000)
        await opage.wait_for_timeout(2_500)
        await shot(opage, "modal")

        # Use the confirmed field IDs from DOM inspection
        print(f"  ⌨️  Typing email into #form_item_email")
        await opage.click("#form_item_email", timeout=6_000)
        await opage.wait_for_timeout(400)
        for ch in email:
            await opage.keyboard.type(ch); await opage.wait_for_timeout(45)
        await opage.wait_for_timeout(700)

        print("  ⌨️  Typing password into #form_item_password")
        await opage.click("#form_item_password", timeout=6_000)
        await opage.wait_for_timeout(400)
        for ch in _PASSWORD:
            await opage.keyboard.type(ch); await opage.wait_for_timeout(45)
        await opage.wait_for_timeout(800)

        # React-compatible checkbox check
        result = await opage.evaluate(_REACT_CHECKBOX_JS)
        print(f"  🔲 Checkbox JS result: {result}")
        await opage.wait_for_timeout(500)

        # Also try physical click as a double-tap
        try:
            await opage.click("#form_item_check", timeout=3_000)
            await opage.wait_for_timeout(300)
        except: pass

        checked_val = await opage.evaluate("() => document.querySelector('#form_item_check')?.checked")
        print(f"  {'✅' if checked_val else '❌'} Checkbox state after click: {checked_val}")

        # If still not checked, try clicking the label
        if not checked_val:
            try:
                await opage.click("label[for='form_item_check']", timeout=3_000)
                await opage.wait_for_timeout(400)
            except:
                # Find label that wraps or is adjacent to the checkbox
                try:
                    lbl_info = await opage.evaluate("""() => {
                        const labels = document.querySelectorAll('label');
                        return [...labels].map(l => ({for: l.htmlFor, text: l.innerText.substring(0,40)}));
                    }""")
                    print(f"  🔍 Labels: {lbl_info}")
                except: pass
                # Click near the checkbox visually
                try:
                    bbox = await opage.locator("#form_item_check").bounding_box()
                    if bbox:
                        await opage.mouse.click(bbox['x']+bbox['width']/2, bbox['y']+bbox['height']/2)
                except: pass

            checked_val = await opage.evaluate("() => document.querySelector('#form_item_check')?.checked")
            print(f"  {'✅' if checked_val else '❌'} Checkbox after label click: {checked_val}")

        await shot(opage, "form_filled")

        # Review pause
        await opage.wait_for_timeout(1_500)

        # Submit
        print("  🖱️  Clicking Create Account…")
        await opage.click("button:has-text('Create Account')", timeout=8_000)
        await opage.wait_for_timeout(5_000)
        await shot(opage, "after_submit")

        # Print all API traffic
        print("\n  📡 ALL POST traffic:")
        for entry in api_log:
            if entry["type"] == "REQ":
                print(f"\n  → {entry['method']} ...{entry['url']}")
                print(f"    body: {entry['body']}")
            else:
                print(f"  ← [{entry['status']}] ...{entry['url']}")
                print(f"    resp: {entry['body']}")
        if not api_log:
            print("    (none)")

        # Page state
        pg_text = await opage.inner_text("body", timeout=5_000)
        print(f"\n  📄 Page keywords check:")
        for kw in ["verification", "verify", "email sent", "check your", "invalid parameter",
                   "error", "already", "welcome", "success"]:
            if kw.lower() in pg_text.lower():
                idx = pg_text.lower().index(kw.lower())
                print(f"  ✦ '{kw}': …{pg_text[max(0,idx-20):idx+60]}…")

        print(f"\n✅ Done — {shot_n} screenshots in ./screenshots/")
        await browser.close()

asyncio.run(main())
