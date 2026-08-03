---
name: Discord bot.py missing modules
description: bot.py imported local modules (vidu_bot, seedance_bot, wan_bot, freebeat_bot, ihtx_bot, log_filter) that never existed anywhere in the repo or git history — the bot could not start until stripped down.
---

## What happened
`bot.py`'s docstring/commands referenced many features (Vidu video, Seedance, Wan2.1/2.6,
G Major/IHTX effects, deep-fry, movies) backed by local modules that were never committed
or present on disk. Only `oreate_bot.py` (Oreate AI image/video via Playwright) actually
existed. Attempting to run the bot failed at import time.

**Why:** Likely leftover/aspirational code from a template or prior environment that was
never fully migrated into this project. Don't assume every module a large bot file imports
actually exists — verify with `python3 -c "import ast; ast.parse(open('bot.py').read())"`
(syntax only) plus a grep for local `import X` / `from X import` lines, then check each
file exists before assuming the bot can run.

## Resolution
Per user's explicit choice, `bot.py` was stripped down to only the commands with satisfied
dependencies: `/image`, `/video` (via `oreate_bot.py`), `/ban`, `/kick`, `/timeout`, `/help`.
A minimal `progress.py` (`ProgressTracker` class — just Discord message text + elapsed
timer, no external API) was added since `/image`/`/video` genuinely need it.
`log_filter.py` and the other 5 missing modules were NOT recreated — those commands are
gone until/unless the user provides the original implementations.

**How to apply:** If the user asks to re-add /viduvideo, /seedance, /wan, /gmajor, /major,
/deepfry, /movie, etc., they need to supply (or have you build from scratch with their
guidance) the corresponding backing module — these are not just "missing files", they wrap
distinct external services (Vidu, Seedance API, self-hosted Wan Gradio, video-effects
pipeline) that require credentials/specs you don't have by default.

## Workflow setup
The bot runs as a `console`-type Replit workflow (`Discord Bot`): `PORT=8000 python3 bot.py`.
It binds a tiny keep-alive HTTP server on `$PORT` (used only so Replit doesn't sleep it) —
pick an unused port from the allowed workflow port list (project already had 8080/8081 taken
by other artifact services).
