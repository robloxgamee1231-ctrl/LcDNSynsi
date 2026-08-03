"""Debug: login, land on home, wait a bit, dump HTML/screenshot to catch the
seedance-promo-modal popup and find its close button."""
import asyncio
from buzzy_bot import (
    _get_temp_email, _login_buzzy, _get_verification_code, _enter_code_buzzy,
)
from playwright.async_api import async_playwright


async def progress(msg):
    print(f"[progress] {msg}")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path="/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox", "--no-zygote"],
        )
        mail_ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        buzzy_ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        try:
            mail_page = await mail_ctx.new_page()
            buzzy_page = await buzzy_ctx.new_page()
            email = await _get_temp_email(mail_page, progress, None)
            await _login_buzzy(buzzy_page, email, progress, None)
            code = await _get_verification_code(mail_page, progress, None)
            await _enter_code_buzzy(buzzy_page, code, progress, None)

            for wait_s in [1, 2, 3, 4, 5]:
                await buzzy_page.wait_for_timeout(wait_s * 1000)
                html = await buzzy_page.content()
                has_promo = "seedance-promo-modal" in html or "dialog-overlay" in html
                print(f"[debug] after {wait_s}s cumulative wait, promo modal present: {has_promo}")
                if has_promo:
                    with open("/tmp/buzzy_promo.html", "w") as f:
                        f.write(html)
                    await buzzy_page.screenshot(path="/tmp/buzzy_promo.png", full_page=True)
                    print("[debug] saved buzzy_promo.html/png")
                    break
        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
