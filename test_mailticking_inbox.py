"""Diagnose mailticking.com modal/inbox behavior in isolation (no Oreate signup)."""
import asyncio, shutil
from pathlib import Path
from playwright.async_api import async_playwright

SHOTS = Path("screenshots"); SHOTS.mkdir(exist_ok=True)
_CHROMIUM = shutil.which("chromium") or None

async def main():
    kw = dict(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    if _CHROMIUM:
        kw["executable_path"] = _CHROMIUM
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kw)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto("https://mailticking.com/", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_500)
        await page.screenshot(path="screenshots/diag_01_loaded.png")

        # Dump the modal's HTML
        try:
            modal_html = await page.evaluate("""() => {
                const closeBtn = document.querySelector('svg, .close, [class*="close" i]');
                const modal = document.querySelector('[class*="modal" i], [role="dialog"]');
                return modal ? modal.outerHTML.slice(0, 2000) : 'NO MODAL FOUND';
            }""")
            print("MODAL HTML:\n", modal_html)
        except Exception as e:
            print("eval failed:", e)

        # Try clicking the X close icon (top-right of modal)
        for sel in ["[class*='close' i]", "svg", ".ant-modal-close", "button[aria-label='Close']"]:
            try:
                await page.click(sel, timeout=2_000)
                print(f"clicked close via {sel}")
                break
            except Exception:
                continue
        await page.wait_for_timeout(1_500)
        await page.screenshot(path="screenshots/diag_02_after_close.png")

        # Now click Refresh
        try:
            await page.click("text=Refresh", timeout=3_000)
            print("clicked Refresh")
        except Exception as e:
            print("Refresh click failed:", e)
        await page.wait_for_timeout(2_000)
        await page.screenshot(path="screenshots/diag_03_after_refresh.png")

        body_text = await page.inner_text("body")
        print("\nBODY TEXT SNIPPET:\n", body_text[:800])

        await browser.close()

asyncio.run(main())
