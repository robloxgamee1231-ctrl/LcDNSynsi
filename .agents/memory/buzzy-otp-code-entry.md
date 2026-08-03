---
name: Buzzy.now OTP code entry must verify success before continuing
description: The Playwright flow for entering the Buzzy.now 6-digit email verification code must confirm the code screen actually advanced before moving on to image/video generation steps.
---

## What happened
The verification-code step clicked "Next" and waited a fixed 5s, then unconditionally
proceeded to the image/video generation flow. When the digit entry or the Next click
silently failed (e.g. typed into the wrong element, button click didn't register), the
browser stayed on the "Enter Code" screen but the bot kept going anyway — it just took
debug screenshots labeled `buzzy-home` / `buzzy-image-section` that actually still showed
the stuck code-entry screen, and eventually timed out much later with a confusing error.

**Why:** No verification step checked whether the code screen actually disappeared after
clicking Next. A UI click "succeeding" in Playwright (no exception) does not mean the site
accepted the input — always confirm the expected navigation/state change happened before
trusting a click and moving to the next stage of a multi-step automation.

## Resolution
`buzzy_bot.py`'s `_enter_code_buzzy` now: types each OTP digit into its own box element
(instead of relying on a single focused input's auto-advance), retries up to 3 times
(re-type + re-click Next) while polling for the "Enter Code" text to disappear, and raises
a clear `RuntimeError` if the code screen never advances — instead of silently continuing
on a broken page.

**How to apply:** When writing/debugging any multi-step browser automation (login flows,
OTP screens, multi-page wizards), always add an explicit "did the page actually advance"
check after an action that's supposed to cause navigation, with bounded retries and a loud
failure if it never does. Don't just sleep-and-hope.

## Related: scraping temp-mail sites (mailticking.com) for codes/addresses
Two follow-on bugs came from the same root cause — trusting raw `page.content()` /
loosely-matched selectors instead of confirming state:
1. **Truncated email address.** mailticking renders the randomized part of a generated
   address in its own element; regex-scanning raw HTML only sees the fragment after the
   last tag boundary (e.g. got `c@gmail.com` instead of `l.ot.s.t.u.p.idka.e.ly@gmail.com`).
   Fix: read the actual `<input>` value first (tags can't split an attribute value); if
   falling back to text-scraping, use `page.inner_text()` (flattened visible text), never
   raw `page.content()` HTML.
2. **Bogus verification code.** The row-click-to-open-email step silently failed (mailticking
   has a per-row "Check emails" action button, not a clickable row), so the email body never
   opened — yet the code extraction still ran a generic `\b(\d{6})\b` regex fallback against
   whatever was on screen (the bare inbox listing) and grabbed an unrelated 6-digit number.
   Fix: only apply the unqualified 6-digit fallback once you've confirmed the target content
   actually opened; otherwise skip and retry rather than returning a guessed value.
3. **Code visible on screen but never extracted (infinite reopen loop).** mailticking renders
   the opened email body inside an `<iframe>` (the raw HTML from the mail server). Screenshots
   showed the code clearly, but `page.inner_text('body')` only reads the *main* frame — it
   never sees iframe content — so extraction always came back empty and the loop just kept
   re-clicking/re-opening the same email forever without ever returning to Buzzy.
   Fix: iterate `page.frames` and collect `inner_text('body')` from every frame (not just the
   top-level page) before running the code regexes.

**Why:** All three bugs share one shape: a "did this succeed / can I actually see this?" check
was skipped, and either a permissive fallback (broad regex, wrong click target) masked the
failure with plausible-looking wrong data, or the extraction silently looked in the wrong place
(main frame instead of the iframe) and never found real data at all.

**How to apply:** When scraping any embedded-content viewer (temp-mail inboxes, PDF viewers,
help widgets), assume the interesting content may be rendered in a child `<iframe>` — check
`page.frames` for the actual DOM, don't assume `page.inner_text('body')` on the main frame is
enough just because the content is visible in a screenshot.

4. **OTP boxes never found at all ("no code input found").** Buzzy's 6-box code entry
   renders plain `<input>` elements with no `type` attribute set (default type is "text", but
   the DOM attribute is simply absent). CSS attribute selectors like `input[type='text']`,
   `input[type='tel']`, `input[maxlength='1']` all match on the literal attribute being
   present — they silently match zero elements when the attribute is missing, even though the
   element behaves like a text input. Fix: after specific selectors fail, fall back to
   scanning every visible `<input>` and reading its effective type via `get_attribute('type')
   or 'text'` in code, rather than only via CSS attribute selectors.

**How to apply:** For any custom form-field detection (OTP boxes, styled inputs), don't rely
solely on `input[type='...']` CSS selectors — always add a generic "all visible inputs, filter
by attribute in code" fallback, since bare `<input>` with no explicit type is common in custom
components.

## Buzzy.now redesigned its post-login homepage (2026-07-13)
Buzzy replaced the old "scroll down to a dedicated Image Generation / Video Generation
section" layout with a single "AI Director" agent-mode chat screen: an "Agent Mode" pill
(dropdown) lets you pick a mode, then you type the prompt and submit.
`_generate_video_buzzy` still uses the old scroll-to-section approach and has NOT been
updated — if video generation breaks next, apply the same redesign fix there (no confirmed
selectors for video exist yet; derive them live with a debug script the same way the image
flow was derived, see below).

**Why:** Third-party sites change their UI without notice; automation selectors need
periodic re-verification against fresh screenshots rather than assuming the original scaffold
still matches reality.

### Confirmed working image-generation selectors (as of 2026-07-13, corrected same day)
An earlier guess (dropdown item text "Image", generic "Generate" button, generic
folder/thumbnail scanning) was wrong and caused the live flow to stall after login. Iterative
debug scripts (`debug_buzzy_ui4.py` → `debug_buzzy_ui6.py`) plus live screenshot review across
several runs confirmed the real sequence, which `buzzy_bot.py`'s `_generate_image_buzzy` now
uses:
1. Dismiss the `.seedance-promo-modal` upsell dialog first (`.seedance-promo-modal__close`,
   `[aria-label='Close promotion']`, or `[data-slot='dialog-close']`).
2. Click `button:has-text('Agent Mode')`, then click `text=/Image Generator/i` — **not**
   "Image". Press `Escape` and pause ~1s after to let the dropdown's own overlay finish
   collapsing (it can keep intercepting the very next click otherwise).
3. Model tabs **do** exist and are labeled **"Seedream 4.0"** and **"GPT Image 1"** — not
   "Nano Banana". Match with aliases (requests for "Nano Banana" try "Seedream" too) and
   `force=True` (same header-interception issue as the prompt editor).
4. Click into `.prompt-editor-wrapper` and type the prompt — **but verify the text actually
   landed in the DOM afterward**; see the "prompt click can silently no-op" lesson below before
   trusting this step.
5. Click `.create-btn` — **not** a "Generate" button. `force=True` needed here too.
6. Poll for `text=/Image Done/i` as the completion signal. There is **no folder step and no
   lightbox** for images — the finished image renders directly in the chat feed as a card with
   two small circular icon buttons overlaid on its top-left corner (download on the left, expand
   on the right). Find the button via bounding-box proximity to the most recent real (non-data-URI)
   `<img>` and pick the leftmost of the pair, then click inside `page.expect_download()`.

**How to apply:** When Buzzy's UI shifts again, re-derive selectors empirically with a
throwaway debug script (reuse `_get_temp_email`/`_login_buzzy`/`_get_verification_code`/
`_enter_code_buzzy` from `buzzy_bot.py`, then screenshot + `page.content()` dump at each step)
rather than guessing from memory — this project already has ~6 `debug_buzzy_ui*.py` scripts
documenting this iterative process. Trust the user's own live debug screenshots over any
previous guess in this file when they conflict — the UI has shifted mid-session before.

### A click+type that doesn't throw is not proof the text landed (2026-07-13)
`.prompt-editor-wrapper` is a *container* div, not necessarily the focusable/editable node
itself. `wrapper.click(force=True)` followed by `page.keyboard.type(prompt)` ran with no
exception, and the code proceeded to click Create — but live screenshots showed the editor
still displaying its placeholder text, i.e. nothing was ever typed. The bot then sat through a
full generation-timeout poll waiting for a result that could never appear (empty prompt).

**Why:** `force=True` bypasses Playwright's actionability checks (visibility/interception), so
the click event fires on whatever element is literally at that point — but firing a click does
not guarantee that element (or the right child of it) actually gained keyboard focus. A fixed
header overlay silently stole focus back after the forced click, so the subsequent
`keyboard.type()` had nowhere to land.

**How to apply:** For any "click a container, then type" step in browser automation — especially
under a `force=True` click, which already signals the click path is unreliable — read the DOM
back afterward (`innerText`/`.value` of the target, checking it actually contains what you just
typed) before treating the step as successful and moving to the next stage. Prefer trying real
editable descendants (`textarea`, `input`, `[contenteditable]`) inside the container first,
falling back to the container itself only if none exist. `buzzy_bot.py`'s `_type_prompt_editor` +
`_editor_contains_text` implement this verify-before-proceeding pattern; reuse it as the template
for any other "type into a custom widget" step that turns out to be flaky.

### Long automated e2e runs can OOM-crash the Playwright Node driver here
A full live pipeline test (temp email → login → OTP → image generation → download) run via a
background console workflow crashed with `JavaScript heap out of memory` in the Playwright
Node driver process after ~10+ minutes, before generation even started (mailticking.com was
slow to deliver the verification email that run, requiring many poll/screenshot cycles).

**Why:** This sandbox's memory budget is limited; a single Playwright session that runs for
many minutes with dozens of full-page JPEG screenshots can exhaust the driver's default Node
heap, independent of any bug in the selectors being tested.

**How to apply:** For long e2e tests here, prefer running via a temporary console workflow
(survives across tool calls, unlike `nohup`/`disown` background shell jobs which get killed
when the sandbox session ends) and treat an OOM crash during a long run as an environment
resource issue to retry/shorten, not necessarily a code defect — remove the temp workflow once
done so it doesn't linger.
