"""
Debug: navigate to the Artlist video generator, click through to a session,
and dump the DOM + screenshots to understand why Generate doesn't trigger.
"""
import asyncio, os, json
from playwright.async_api import async_playwright

COOKIES_FILE = "artlist_cookies.json"
SNAP_DIR = "screenshots"
os.makedirs(SNAP_DIR, exist_ok=True)


async def snap(page, label: str):
    path = f"{SNAP_DIR}/dbg-{label}.jpg"
    await page.screenshot(path=path, type="jpeg", quality=75, full_page=False)
    print(f"[snap] {path}")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})

        # Load saved cookies
        if os.path.exists(COOKIES_FILE):
            cookies = json.load(open(COOKIES_FILE))
            await ctx.add_cookies(cookies)
            print(f"[debug] loaded {len(cookies)} cookies")

        page = await ctx.new_page()

        # Step 1: Navigate to compose page
        print("[debug] navigating to compose page…")
        await page.goto("https://toolkit.artlist.io/image-video-generator?mode=video",
                        wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(3)
        await snap(page, "01-compose")

        # Step 2: Type a prompt
        print("[debug] typing prompt…")
        field = await page.query_selector('[contenteditable="true"]')
        if field:
            await field.click()
            await asyncio.sleep(0.5)
            await page.keyboard.type("test video prompt debug", delay=30)
            await asyncio.sleep(1)
        await snap(page, "02-prompt-typed")

        # Step 3: Find and dump the Generate button info on compose page
        gen_info = await page.evaluate(
            r"""() => {
                const results = [];
                for (const btn of document.querySelectorAll('button,[role="button"]')) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (!/generate/i.test(t)) continue;
                    const rect = btn.getBoundingClientRect();
                    const style = window.getComputedStyle(btn);
                    results.push({
                        text: t.slice(0, 60),
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        w: rect.width, h: rect.height,
                        opacity: style.opacity,
                        pointerEvents: style.pointerEvents,
                        disabled: btn.disabled,
                        className: btn.className.slice(0, 80),
                        offsetParent: !!btn.offsetParent,
                    });
                }
                return results;
            }"""
        )
        print(f"[debug] Generate buttons on compose page:")
        for g in gen_info:
            print(f"  {json.dumps(g)}")

        # Step 4: Click Generate directly by Playwright locator
        print("[debug] clicking Generate via Playwright locator (force=True)…")
        try:
            loc = page.locator('button:has-text("Generate")').first
            await loc.click(force=True, timeout=8_000)
            print("[debug] Generate locator click done")
        except Exception as e:
            print(f"[debug] locator click error: {e}")

        await asyncio.sleep(3)
        url_after = page.url
        print(f"[debug] URL after Generate click: {url_after}")
        await snap(page, "03-after-generate-click")

        # Step 5: Dump the page body & Generate button info on session page
        body = await page.evaluate("() => document.body.innerText")
        print(f"[debug] body (first 1000 chars):\n{body[:1000]}")

        gen_info2 = await page.evaluate(
            r"""() => {
                const results = [];
                for (const btn of document.querySelectorAll('button,[role="button"]')) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (!/generate/i.test(t)) continue;
                    const rect = btn.getBoundingClientRect();
                    const style = window.getComputedStyle(btn);
                    results.push({
                        text: t.slice(0, 60),
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        w: rect.width, h: rect.height,
                        opacity: style.opacity,
                        pointerEvents: style.pointerEvents,
                        disabled: btn.disabled,
                        className: btn.className.slice(0, 80),
                        offsetParent: !!btn.offsetParent,
                        zIndex: style.zIndex,
                        position: style.position,
                    });
                }
                return results;
            }"""
        )
        print(f"[debug] Generate buttons on session page:")
        for g in gen_info2:
            print(f"  {json.dumps(g)}")

        # Step 6: Check what element is actually at (1073, 852)
        hit = await page.evaluate(
            """() => {
                const el = document.elementFromPoint(1073, 852);
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {
                    tag: el.tagName,
                    text: (el.innerText || el.textContent || '').trim().slice(0, 60),
                    class: el.className.slice(0, 80),
                    rect: {l: r.left, t: r.top, w: r.width, h: r.height},
                };
            }"""
        )
        print(f"[debug] element at (1073, 852): {json.dumps(hit)}")

        # Step 7: Walk up to see the full parent chain at (1073, 852)
        chain = await page.evaluate(
            """() => {
                let el = document.elementFromPoint(1073, 852);
                const chain = [];
                while (el && chain.length < 8) {
                    const r = el.getBoundingClientRect();
                    chain.push({
                        tag: el.tagName,
                        text: (el.innerText || '').trim().slice(0, 40),
                        class: el.className.slice(0, 60),
                    });
                    el = el.parentElement;
                }
                return chain;
            }"""
        )
        print(f"[debug] DOM chain at (1073, 852):")
        for c in chain:
            print(f"  {json.dumps(c)}")

        # Step 8: Click Generate on session page (mouse click at computed coords)
        if gen_info2:
            target = gen_info2[0]
            print(f"\n[debug] clicking Generate on session page at ({target['x']:.0f},{target['y']:.0f})…")
            
            # First check what's at the button center
            at_btn = await page.evaluate(
                f"""() => {{
                    const el = document.elementFromPoint({target['x']}, {target['y']});
                    if (!el) return null;
                    return {{
                        tag: el.tagName,
                        text: (el.innerText || '').trim().slice(0, 60),
                        class: el.className.slice(0, 60),
                    }};
                }}"""
            )
            print(f"[debug] element at button center: {json.dumps(at_btn)}")

            await page.mouse.move(target["x"], target["y"])
            await asyncio.sleep(0.5)
            await snap(page, "04-before-session-click")
            await page.mouse.click(target["x"], target["y"])
            await asyncio.sleep(3)
            await snap(page, "05-after-session-click")

            body2 = await page.evaluate("() => document.body.innerText")
            print(f"[debug] body after session-page click:\n{body2[:800]}")
            print(f"[debug] URL after session-page click: {page.url}")
        else:
            print("[debug] no Generate button found on session page!")
            await snap(page, "04-no-btn-on-session")

        await browser.close()


asyncio.run(main())
