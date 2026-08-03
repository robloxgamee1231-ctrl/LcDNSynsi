"""Full live pipeline debug with promo-popup dismissal fixed, to nail the
prompt-entry and post-generation folder/download UI."""
import asyncio
from buzzy_bot import (
    _get_temp_email, _login_buzzy, _get_verification_code, _enter_code_buzzy,
)
from playwright.async_api import async_playwright


async def progress(msg):
    print(f"[progress] {msg}")


async def dismiss_promo(page):
    for sel in [".seedance-promo-modal__close", "[aria-label='Close promotion']", "[data-slot='dialog-close']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(500)
                print(f"[debug] dismissed promo via {sel}")
                return True
        except Exception:
            pass
    return False


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

            await buzzy_page.wait_for_timeout(2000)
            await dismiss_promo(buzzy_page)
            await buzzy_page.wait_for_timeout(500)

            trigger = await buzzy_page.query_selector("button:has-text('Agent Mode')")
            print("[debug] trigger found:", bool(trigger))
            await trigger.click()
            await buzzy_page.wait_for_timeout(700)
            img_item = await buzzy_page.query_selector("text=/Image Generator/i")
            await img_item.click()
            await buzzy_page.wait_for_timeout(1500)
            await buzzy_page.screenshot(path="/tmp/d4_image_mode.png", full_page=True)

            # Focus the prompt editor via its wrapper/placeholder, then type
            wrapper = await buzzy_page.query_selector(".prompt-editor-wrapper")
            print("[debug] wrapper found:", bool(wrapper))
            await wrapper.click()
            await buzzy_page.wait_for_timeout(300)
            await buzzy_page.keyboard.type("a small red fox sitting in snow, digital art", delay=20)
            await buzzy_page.wait_for_timeout(500)
            await buzzy_page.screenshot(path="/tmp/d4_prompt_typed.png", full_page=True)
            with open("/tmp/d4_prompt_typed.html", "w") as f:
                f.write(await buzzy_page.content())

            create_btn = await buzzy_page.query_selector(".create-btn")
            print("[debug] create_btn found:", bool(create_btn))
            await create_btn.click()
            print("[debug] clicked create")

            for i in range(20):
                await buzzy_page.wait_for_timeout(6000)
                await buzzy_page.screenshot(path=f"/tmp/d4_gen_{i}.png", full_page=True)
                html = await buzzy_page.content()
                has_folder = "folder" in html.lower()
                has_download = "download" in html.lower()
                print(f"[debug] tick {i}: folder_kw={has_folder} download_kw={has_download}")
                if i % 5 == 4:
                    with open(f"/tmp/d4_gen_{i}.html", "w") as f:
                        f.write(html)

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
