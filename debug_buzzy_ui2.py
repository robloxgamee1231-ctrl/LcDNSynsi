"""
Continuation debug: login, open Agent Mode dropdown, click "Image Generator",
screenshot + dump HTML so we can see the real model-selection / prompt UI.
"""
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
            print(f"[debug] email: {email}")
            await _login_buzzy(buzzy_page, email, progress, None)
            code = await _get_verification_code(mail_page, progress, None)
            print(f"[debug] code: {code}")
            await _enter_code_buzzy(buzzy_page, code, progress, None)
            await buzzy_page.wait_for_timeout(3000)

            # Open Agent Mode dropdown
            trigger = await buzzy_page.query_selector("text=/^Agent Mode$/")
            if not trigger:
                trigger = (await buzzy_page.query_selector_all("text=/agent mode/i"))[0]
            await trigger.click()
            await buzzy_page.wait_for_timeout(1000)

            # Click "Image Generator"
            img_item = await buzzy_page.query_selector("text=/Image Generator/i")
            print("[debug] found Image Generator item:", bool(img_item))
            if img_item:
                await img_item.click()
                await buzzy_page.wait_for_timeout(2000)

            await buzzy_page.screenshot(path="/tmp/buzzy_image_mode.png", full_page=True)
            html = await buzzy_page.content()
            with open("/tmp/buzzy_image_mode.html", "w") as f:
                f.write(html)
            print("[debug] saved buzzy_image_mode.png / .html")

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
