"""Final leg: click the generated image thumbnail to open the lightbox
overlay, then click its Download button and confirm a real download fires."""
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
            await buzzy_page.keyboard.type("a cozy cabin in the mountains at sunset, painting", delay=20)
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

            # Click the result thumbnail card in the chat (e.g. "Red Fox in
            # Snow" style card with a small preview image) to open the
            # full-size lightbox.
            thumb = await buzzy_page.query_selector("[class*='result'] img, [class*='asset'] img, [class*='thumbnail'] img, [class*='canvas'] img")
            print("[debug] thumb via class selectors found:", bool(thumb))
            if not thumb:
                imgs = await buzzy_page.query_selector_all("img")
                print(f"[debug] total <img> count: {len(imgs)}")
                for im in imgs:
                    src = await im.get_attribute("src") or ""
                    alt = await im.get_attribute("alt") or ""
                    visible = await im.is_visible()
                    if visible and src and not src.startswith("data:"):
                        print("  candidate img:", alt, src[:80])

            await buzzy_page.screenshot(path="/tmp/d6_before_click_thumb.png", full_page=True)

            # Click on the canvas area where the image sits (center-right of
            # the screen based on earlier screenshot layout).
            box_center = None
            imgs = await buzzy_page.query_selector_all("img")
            for im in imgs:
                src = await im.get_attribute("src") or ""
                if src and not src.startswith("data:") and await im.is_visible():
                    bbox = await im.bounding_box()
                    if bbox and bbox["width"] > 100:
                        box_center = im
                        print("[debug] clicking image with src:", src[:100], "bbox:", bbox)
                        break
            if box_center:
                await box_center.click()
                await buzzy_page.wait_for_timeout(1000)
                await buzzy_page.screenshot(path="/tmp/d6_lightbox.png", full_page=True)
                with open("/tmp/d6_lightbox.html", "w") as f:
                    f.write(await buzzy_page.content())

                dl_btn = await buzzy_page.query_selector("button[title='Download']")
                visible = await dl_btn.is_visible() if dl_btn else False
                print("[debug] download button visible after click:", visible)
                if dl_btn and visible:
                    try:
                        async with buzzy_page.expect_download(timeout=20000) as dl_info:
                            await dl_btn.click()
                        download = await dl_info.value
                        print("[debug] DOWNLOAD SUCCESS:", download.suggested_filename)
                        data = await download.path()
                        print("[debug] saved temp path:", data)
                    except Exception as e:
                        print("[debug] download click failed:", e)
            else:
                print("[debug] no clickable result image found")

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
