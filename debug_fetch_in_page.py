"""
debug_fetch_in_page.py — call createUserGeneration from inside the Playwright
browser page context (uses the page's own cookies/auth) and print the result.
Also intercepts all trpc calls during page load to find costQuoteDigitalSignature.
"""
import asyncio, json, os, random, sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

COOKIES_FILE = Path(".artlist_session.json")

async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        )

        # Load cookies
        cookies = json.loads(COOKIES_FILE.read_text())
        await ctx.add_cookies(cookies)
        print(f"Loaded {len(cookies)} cookies")

        page = await ctx.new_page()

        # Capture ALL trpc and artlist API calls
        api_calls = []
        def on_req(req):
            if "artlist.io" in req.url and ("trpc" in req.url or "api" in req.url.lower()):
                try:
                    body = req.post_data or ""
                except:
                    body = ""
                api_calls.append({"method": req.method, "url": req.url, "body": body[:300]})

        async def on_resp(res):
            if "artlist.io" in res.url and ("trpc" in res.url or "api" in res.url.lower()):
                try:
                    body = await res.text()
                except:
                    body = "(unreadable)"
                api_calls.append({"status": res.status, "url": res.url, "body": body[:300]})

        page.on("request", on_req)
        page.on("response", on_resp)

        print("Navigating to toolkit...")
        await page.goto("https://toolkit.artlist.io/image-video-generator?mode=video", wait_until="networkidle", timeout=30000)
        print(f"Page URL: {page.url}")

        # Check if logged in
        body_text = await page.evaluate("() => document.body.innerText.slice(0, 200)")
        print(f"Page body: {body_text!r}")

        # Check what cookies the browser actually has for this domain
        ctx_cookies = await ctx.cookies("https://toolkit.artlist.io")
        print(f"\nCookies for toolkit.artlist.io: {len(ctx_cookies)}")
        for c in ctx_cookies:
            if any(k in c['name'].lower() for k in ['session','token','csrf','user']):
                print(f"  {c['name']} = {c['value'][:50]}...")

        # Dump API calls captured during page load
        print(f"\nAPI calls during page load: {len(api_calls)}")
        for c in api_calls:
            if "status" in c:
                print(f"  [{c['status']}] {c['url'][:80]}")
                if c['body'] and c['status'] not in (200,):
                    print(f"       body: {c['body'][:100]}")
            else:
                print(f"  {c['method']} {c['url'][:80]}")
        api_calls.clear()

        # Now try making the createUserGeneration call from inside the page
        print("\n=== Making createUserGeneration from INSIDE the page ===")
        result = await page.evaluate("""async () => {
            // Find any existing session ID from the URL or create one
            const url = window.location.href;
            const sessionMatch = url.match(/([0-9a-f-]{36})/);
            const sessionId = sessionMatch ? sessionMatch[1] : crypto.randomUUID();

            const payload = {
                json: {
                    chatSessionId: sessionId,
                    inputs: {prompt: "a lone astronaut walking across a red desert"},
                    modelGroupId: 2524,
                    feature: "text-to-video",
                    price: 1500,
                    settings: {
                        prompt: "a lone astronaut walking across a red desert",
                        resolution: "720p",
                        duration: 5,
                        generate_audio: true,
                        aspect_ratio: "16:9"
                    },
                    artifacts: []
                }
            };

            try {
                const resp = await fetch('/api/trpc/userGenerationRouter.createUserGeneration', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
                const text = await resp.text();
                return {status: resp.status, body: text.slice(0, 500)};
            } catch(e) {
                return {error: String(e)};
            }
        }""")
        print(f"Result: {result}")

        # Also try to find the costQuote endpoint
        print("\n=== Looking for cost quote / signed quote API ===")
        result2 = await page.evaluate("""async () => {
            // Search React fiber for any costQuoteDigitalSignature
            const walk = (obj, depth=0) => {
                if (!obj || depth > 5 || typeof obj !== 'object') return null;
                for (const k of Object.keys(obj)) {
                    if (k === 'costQuoteDigitalSignature' || k === 'digitalSignature') {
                        return {key: k, value: String(obj[k]).slice(0,100)};
                    }
                    const r = walk(obj[k], depth+1);
                    if (r) return r;
                }
                return null;
            };
            // Try to find in window.__NEXT_DATA__ or similar
            const nextData = window.__NEXT_DATA__;
            if (nextData) return {source: '__NEXT_DATA__', found: walk(nextData)};
            return {source: 'none', found: null};
        }""")
        print(f"Cost quote search: {result2}")

        # Dump API calls from the in-page fetch
        await asyncio.sleep(2)
        print(f"\nAPI calls from in-page fetch: {len(api_calls)}")
        for c in api_calls:
            if "status" in c:
                print(f"  [{c['status']}] {c['url'][:80]}")
                if c['body']:
                    print(f"       body: {c['body'][:200]}")
            else:
                print(f"  {c['method']} {c['url'][:80]}")

        await browser.close()

asyncio.run(main())
