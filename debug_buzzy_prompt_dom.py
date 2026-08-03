"""Dump the actual DOM structure around Buzzy's prompt editor once we reach
the Image Generator screen, so we can find the real focusable/editable node
instead of guessing selectors. Does NOT try to type or submit anything —
just logs in, opens Image Generator mode, and dumps HTML + a list of every
input/textarea/contenteditable element with its attributes and bounding box."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from buzzy_bot import (
    _get_temp_email, _login_buzzy, _get_verification_code, _enter_code_buzzy,
    _dismiss_promo_popup, _dismiss_popup,
)
from playwright.async_api import async_playwright

OUT_DIR = Path("screenshots/buzzy_dom_dump")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def progress(msg: str) -> None:
    print(f"[progress] {msg}", flush=True)


async def snap(label: str, img_bytes: bytes) -> None:
    safe = label.replace("[buzzy] ", "").replace("/", "_")
    path = OUT_DIR / f"{safe}.jpg"
    path.write_bytes(img_bytes)
    print(f"[snap] saved {path}", flush=True)


async def main() -> None:
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

            email = await _get_temp_email(mail_page, progress, snap)
            await _login_buzzy(buzzy_page, email, progress, snap)
            code = await _get_verification_code(mail_page, progress, snap)
            await _enter_code_buzzy(buzzy_page, code, progress, snap)

            await buzzy_page.wait_for_timeout(1500)
            await _dismiss_promo_popup(buzzy_page)
            await _dismiss_popup(buzzy_page)
            await buzzy_page.wait_for_timeout(500)

            trigger = await buzzy_page.query_selector("button:has-text('Agent Mode')")
            if trigger:
                await trigger.click()
                await buzzy_page.wait_for_timeout(700)
                img_item = await buzzy_page.query_selector("text=/Image Generator/i")
                if img_item:
                    await img_item.click()
                    await buzzy_page.wait_for_timeout(1500)
            await buzzy_page.keyboard.press("Escape")
            await buzzy_page.wait_for_timeout(1000)

            await snap("dom-dump-screen", await buzzy_page.screenshot(type="jpeg", quality=70))

            # Dump full outer HTML of the wrapper (and surroundings) plus a
            # structured list of every input/textarea/contenteditable on the
            # page with attributes + bounding box, so we can pick the real
            # target without guessing.
            info = await buzzy_page.evaluate(
                """() => {
                    const wrap = document.querySelector('.prompt-editor-wrapper');
                    const wrapHtml = wrap ? wrap.outerHTML : null;

                    function describe(el) {
                        const r = el.getBoundingClientRect();
                        return {
                            tag: el.tagName,
                            id: el.id || null,
                            className: (el.className && el.className.toString) ? el.className.toString() : null,
                            contentEditable: el.getAttribute('contenteditable'),
                            placeholder: el.getAttribute('placeholder'),
                            role: el.getAttribute('role'),
                            ariaLabel: el.getAttribute('aria-label'),
                            visible: !!(r.width || r.height),
                            box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                        };
                    }

                    const nodes = Array.from(document.querySelectorAll(
                        'textarea, input, [contenteditable], [contenteditable=\"true\"], [role=\"textbox\"]'
                    )).map(describe);

                    return { wrapHtml, nodes, bodyLen: document.body.innerHTML.length };
                }"""
            )

            dump_path = OUT_DIR / "prompt_wrapper.html"
            dump_path.write_text(info["wrapHtml"] or "NO .prompt-editor-wrapper FOUND")
            print(f"[dump] wrapper HTML -> {dump_path} (len={len(info['wrapHtml'] or '')})", flush=True)

            print("[dump] editable-ish nodes on page:", flush=True)
            for n in info["nodes"]:
                print(f"  {n}", flush=True)

            print(f"[dump] body innerHTML length: {info['bodyLen']}", flush=True)

            # Also try actually clicking the wrapper and checking document.activeElement
            wrapper = await buzzy_page.query_selector(".prompt-editor-wrapper")
            if wrapper:
                await wrapper.click(force=True, timeout=5000)
                await buzzy_page.wait_for_timeout(300)
                active = await buzzy_page.evaluate(
                    """() => {
                        const a = document.activeElement;
                        if (!a) return null;
                        const r = a.getBoundingClientRect();
                        return {
                            tag: a.tagName, id: a.id || null,
                            className: (a.className && a.className.toString) ? a.className.toString() : null,
                            contentEditable: a.getAttribute('contenteditable'),
                            box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                        };
                    }"""
                )
                print(f"[dump] document.activeElement after force-clicking wrapper: {active}", flush=True)

            print("[dump] DONE", flush=True)

        finally:
            await mail_ctx.close()
            await buzzy_ctx.close()
            await browser.close()


asyncio.run(main())
