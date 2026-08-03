"""
Debug: login, switch to Image Generator mode, open the model-selector
dropdown to see real option labels, then type a prompt and click Create,
then watch for the results folder / download UI so we can nail down the
final leg of the pipeline. Full live run.
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
            await _login_buzzy(buzzy_page, email, progress, None)
            code = await _get_verification_code(mail_page, progress, None)
            await _enter_code_buzzy(buzzy_page, code, progress, None)
            await buzzy_page.wait_for_timeout(3000)

            trigger = (await buzzy_page.query_selector_all("text=/agent mode/i"))[0]
            await trigger.click()
            await buzzy_page.wait_for_timeout(800)
            img_item = await buzzy_page.query_selector("text=/Image Generator/i")
            await img_item.click()
            await buzzy_page.wait_for_timeout(1500)

            # Open model selector dropdown (msb__btn)
            model_btn = await buzzy_page.query_selector(".msb__btn")
            print("[debug] model_btn found:", bool(model_btn))
            if model_btn:
                await model_btn.click()
                await buzzy_page.wait_for_timeout(800)
                html_menu = await buzzy_page.content()
                with open("/tmp/buzzy_model_menu.html", "w") as f:
                    f.write(html_menu)
                await buzzy_page.screenshot(path="/tmp/buzzy_model_menu.png", full_page=True)
                # close dropdown with a neutral mouse click far from any menu/header
                await buzzy_page.mouse.click(5, 400)
                await buzzy_page.wait_for_timeout(500)

            # Type a prompt into the contenteditable prompt editor
            editors = await buzzy_page.query_selector_all(".prompt-editor[contenteditable='true']")
            print(f"[debug] editor candidates: {len(editors)}")
            editor = None
            for e in editors:
                if await e.is_visible():
                    editor = e
                    break
            print("[debug] visible editor found:", bool(editor))
            if editor:
                await editor.click()
                await buzzy_page.keyboard.type("a small red fox sitting in snow, digital art", delay=20)
                await buzzy_page.wait_for_timeout(500)
            else:
                print("[debug] no visible editor — dumping all prompt-editor elements")
                for e in editors:
                    box = await e.bounding_box()
                    print("  box:", box)

            await buzzy_page.screenshot(path="/tmp/buzzy_prompt_typed.png", full_page=True)
            with open("/tmp/buzzy_prompt_typed.html", "w") as f:
                f.write(await buzzy_page.content())

            # Click Create
            create_btn = await buzzy_page.query_selector(".create-btn")
            print("[debug] create_btn found:", bool(create_btn))
            if create_btn:
                await create_btn.click()
                print("[debug] clicked Create")

            # Poll for up to ~90s watching for progress/results
            for i in range(18):
                await buzzy_page.wait_for_timeout(5000)
                await buzzy_page.screenshot(path=f"/tmp/buzzy_gen_progress_{i}.png", full_page=True)
                print(f"[debug] progress screenshot {i} saved")

            with open("/tmp/buzzy_gen_final.html", "w") as f:
                f.write(await buzzy_page.content())

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
