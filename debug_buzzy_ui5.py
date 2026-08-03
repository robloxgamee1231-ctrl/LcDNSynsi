"""Full live pipeline: login -> image mode -> prompt -> create -> wait for
done -> click the generated image -> click Download -> confirm a real
download fires."""
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
            await trigger.click()
            await buzzy_page.wait_for_timeout(700)
            img_item = await buzzy_page.query_selector("text=/Image Generator/i")
            await img_item.click()
            await buzzy_page.wait_for_timeout(1500)

            wrapper = await buzzy_page.query_selector(".prompt-editor-wrapper")
            await wrapper.click()
            await buzzy_page.wait_for_timeout(300)
            await buzzy_page.keyboard.type("a tiny robot watering a plant, cute illustration", delay=20)
            await buzzy_page.wait_for_timeout(500)

            create_btn = await buzzy_page.query_selector(".create-btn")
            await create_btn.click()
            print("[debug] clicked create, waiting for done...")

            done = False
            for i in range(24):
                await buzzy_page.wait_for_timeout(5000)
                el = await buzzy_page.query_selector("text=/Image Done/i")
                if el:
                    done = True
                    print(f"[debug] Image Done detected at tick {i}")
                    break
            print("[debug] done:", done)
            await buzzy_page.wait_for_timeout(1500)
            await buzzy_page.screenshot(path="/tmp/d5_done.png", full_page=True)

            # Click the generated image thumbnail in the chat/canvas
            img_thumb = await buzzy_page.query_selector("img[alt]:not([alt='download'])")
            print("[debug] img_thumb found:", bool(img_thumb))

            # Hover the canvas image area to try to reveal the toolbar, then
            # look for the Download button directly (Playwright click works
            # even at opacity:0 as long as display != none).
            dl_btn = await buzzy_page.query_selector("button[title='Download']")
            print("[debug] download button present in DOM:", bool(dl_btn))
            if dl_btn:
                box = await dl_btn.bounding_box()
                print("[debug] download button bbox:", box)

            with open("/tmp/d5_before_download.html", "w") as f:
                f.write(await buzzy_page.content())

            if dl_btn:
                try:
                    async with buzzy_page.expect_download(timeout=15000) as dl_info:
                        await dl_btn.click(force=True)
                    download = await dl_info.value
                    print("[debug] DOWNLOAD SUCCESS:", download.suggested_filename)
                except Exception as e:
                    print("[debug] direct download click failed:", e)
                    # try hovering the image first
                    if img_thumb:
                        await img_thumb.hover()
                        await buzzy_page.wait_for_timeout(500)
                        await buzzy_page.screenshot(path="/tmp/d5_hover.png", full_page=True)
                        try:
                            async with buzzy_page.expect_download(timeout=15000) as dl_info2:
                                await dl_btn.click(force=True)
                            download = await dl_info2.value
                            print("[debug] DOWNLOAD SUCCESS after hover:", download.suggested_filename)
                        except Exception as e2:
                            print("[debug] download after hover also failed:", e2)

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
