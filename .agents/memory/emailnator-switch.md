---
name: Emailnator switch
description: mailticking.com is Cloudflare-blocked; switched to emailnator.com HTTP API for temp Gmail. Screenshots now post to channel.
---

## mailticking.com is dead — use emailnator.com

mailticking.com returns a Cloudflare challenge page ("Just a moment…") for all non-browser requests AND for Playwright headless sessions. Even `networkidle` + `wait_for_function` get empty strings every time.

**Why:** Cloudflare Turnstile blocks headless/server IPs.

## emailnator.com HTTP API (no browser needed)

Three endpoints, cookie-auth only:

1. `GET https://www.emailnator.com/` → sets `XSRF-TOKEN` + `gmailnator_session` cookies
2. `POST /generate-email` body `{"email":["googleMail"]}` → `{"email":["name@gmail.com"]}` or `@googlemail.com`
3. `POST /message-list` body `{"email":"..."}` → `{"messageData":[{messageID,from,subject,time}]}`
4. `POST /message-id` body `{"email":"...","messageID":"..."}` → full HTML body

**Auth:** every POST needs `X-XSRF-TOKEN: <urllib.parse.unquote(cookie)>` header + the cookie jar from the session.

**How to apply:** Use a single `aiohttp.ClientSession()` across the whole generation run (get email + poll inbox). The session stores cookies automatically.

**Domains returned:** `@gmail.com` and `@googlemail.com` — both are real Google inboxes. `_is_acceptable_email` now accepts both.

## Screenshots → channel (not DM)

Screenshots were previously DMed to a hardcoded user ID via `_send_debug_dm`. Removed that function. Each command handler now creates a local `_screenshot_to_channel` async closure that calls `interaction.followup.send()`. This posts screenshots directly in the channel where /image or /video was invoked.
