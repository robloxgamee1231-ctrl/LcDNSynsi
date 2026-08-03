"""
send_test_video.py — generate a Seedance 2.0 test video and deliver it
via raw Discord REST API (no discord.py Client — safe to run while the
bot is already online with the same token).

Sends to channel 736027479482826802 if the bot has access, otherwise
falls back to owner DM (806976883774324746).

Run:  python3 send_test_video.py
"""

import asyncio
import io
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# Flush stdout immediately so background logs show up
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv(Path(__file__).parent / ".env")

TOKEN             = os.environ["DISCORD_TOKEN"]
OWNER_ID          = 806976883774324746
TARGET_CHANNEL_ID = 736027479482826802   # will fall back to owner DM on 404
PROMPT            = "a lone astronaut walking across a red desert at golden hour, cinematic"
MODEL             = "Seedance 2.0"

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent":    "DiscordBot (test, 1.0)",
}

# ── Discord REST helpers ───────────────────────────────────────────────────────

async def _get_channel_id(session: aiohttp.ClientSession) -> int:
    """Try the target channel; fall back to creating an owner DM."""
    # Quick probe
    async with session.get(f"{API}/channels/{TARGET_CHANNEL_ID}",
                           headers=HEADERS) as r:
        if r.status == 200:
            print(f"[discord] using channel {TARGET_CHANNEL_ID}")
            return TARGET_CHANNEL_ID

    print(f"[discord] channel {TARGET_CHANNEL_ID} not accessible "
          f"(status {r.status}) — falling back to owner DM")
    async with session.post(f"{API}/users/@me/channels", headers=HEADERS,
                            json={"recipient_id": str(OWNER_ID)}) as r:
        data = await r.json()
        dm_id = int(data["id"])
        print(f"[discord] using owner DM channel {dm_id}")
        return dm_id


async def _post(session: aiohttp.ClientSession, ch_id: int,
                content: str,
                file_bytes: bytes | None = None,
                filename: str = "debug.jpg") -> dict:
    url = f"{API}/channels/{ch_id}/messages"
    if file_bytes:
        form = aiohttp.FormData()
        form.add_field("payload_json", f'{{"content":{content!r}}}',
                       content_type="application/json")
        form.add_field("files[0]", io.BytesIO(file_bytes),
                       filename=filename, content_type="application/octet-stream")
        async with session.post(url, headers=HEADERS, data=form) as r:
            return await r.json()
    else:
        async with session.post(url, headers=HEADERS,
                                json={"content": content}) as r:
            return await r.json()


async def _edit(session: aiohttp.ClientSession, ch_id: int,
                msg_id: str, content: str) -> None:
    url = f"{API}/channels/{ch_id}/messages/{msg_id}"
    async with session.patch(url, headers=HEADERS,
                             json={"content": content}) as r:
        if r.status not in (200, 204):
            print(f"[discord] edit {r.status}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    import artlist_bot as _art

    async with aiohttp.ClientSession() as session:
        ch_id = await _get_channel_id(session)

        init = await _post(session, ch_id,
                           f"🎬 **Seedance 2.0 test starting…**\n> 🖊️ {PROMPT}")
        msg_id = init.get("id", "")
        print(f"[discord] status msg id: {msg_id}")

        async def _progress(msg: str) -> None:
            print(f"[progress] {msg}")
            if msg_id:
                await _edit(session, ch_id, msg_id,
                            f"🎬 **Seedance 2.0** | {msg}\n> 🖊️ {PROMPT}")

        async def _screenshot(label: str, img_bytes: bytes) -> None:
            try:
                await _post(session, ch_id, f"📸 `{label}`",
                            file_bytes=img_bytes)
            except Exception as e:
                print(f"[screenshot] {e}")

        try:
            video_bytes = await _art.generate_artlist_video(
                prompt=PROMPT,
                model=MODEL,
                resolution="720p",
                duration=5,
                aspect_ratio="16:9",
                progress_cb=_progress,
                screenshot_cb=_screenshot,
            )
            mb = len(video_bytes) / 1024 / 1024
            print(f"[test] ✅ {mb:.1f} MB video")

            caption = (
                f"✅ **Seedance 2.0 done!** ({mb:.1f} MB)\n"
                f"> 🖊️ {PROMPT}"
            )
            if mb <= 8:
                await _post(session, ch_id, caption,
                            file_bytes=video_bytes, filename="seedance_test.mp4")
                if msg_id:
                    await _edit(session, ch_id, msg_id, "✅ Video sent below ↓")
            else:
                form = aiohttp.FormData()
                form.add_field("reqtype", "fileupload")
                form.add_field("fileToUpload", video_bytes,
                               filename="video.mp4", content_type="video/mp4")
                async with session.post("https://catbox.moe/user/api.php",
                                        data=form,
                                        timeout=aiohttp.ClientTimeout(total=180)) as r:
                    link = (await r.text()).strip()
                if not link.startswith("https://"):
                    link = "(upload failed)"
                await _post(session, ch_id, f"{caption}\n📽️ {link}")
                if msg_id:
                    await _edit(session, ch_id, msg_id, f"✅ Done — {link}")

        except Exception as e:
            err = str(e)[:400]
            print(f"[test] ❌ {e}")
            if msg_id:
                await _edit(session, ch_id, msg_id,
                            f"❌ **Test FAILED**\n```{err}```")


asyncio.run(main())
