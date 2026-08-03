"""
One-off debug script: run the real login flow against Buzzy.now and, once on
the post-login "AI Director" home screen, dump the actual DOM (outerHTML of
likely containers) plus a full-page screenshot to /tmp so we can read real
selectors instead of guessing from blurry phone screenshots.

Usage: python3 debug_buzzy_ui.py
Outputs:
  /tmp/buzzy_home.png       — full page screenshot after login
  /tmp/buzzy_home.html      — full page HTML after login
  /tmp/buzzy_agent_click.png — screenshot after clicking the Agent Mode pill
  /tmp/buzzy_agent_click.html
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

            await buzzy_page.wait_for_timeout(4000)
            await buzzy_page.screenshot(path="/tmp/buzzy_home.png", full_page=True)
            html = await buzzy_page.content()
            with open("/tmp/buzzy_home.html", "w") as f:
                f.write(html)
            print("[debug] saved buzzy_home.png / .html")

            # Try to find and click something that looks like "Agent Mode"
            candidates = await buzzy_page.query_selector_all("text=/agent mode/i")
            print(f"[debug] found {len(candidates)} 'agent mode' text matches")
            if candidates:
                await candidates[0].click()
                await buzzy_page.wait_for_timeout(1500)
                await buzzy_page.screenshot(path="/tmp/buzzy_agent_click.png", full_page=True)
                html2 = await buzzy_page.content()
                with open("/tmp/buzzy_agent_click.html", "w") as f:
                    f.write(html2)
                print("[debug] saved buzzy_agent_click.png / .html")
            else:
                print("[debug] no 'Agent Mode' text found on page")

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
