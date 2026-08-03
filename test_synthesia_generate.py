"""
test_synthesia_generate.py — Full end-to-end test for the Synthesia Gemini
Omni video flow (fresh account -> My Media -> Prompt settings -> generate ->
download).

Sends every step screenshot + the final video to the owner via Discord DM,
same pattern as test_generate_click.py for Artlist.
"""
import asyncio
import io
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL = 736027479482826802   # owner DM user ID

import discord
from synthesia_bot import generate_synthesia_video

PROMPT = "a sleek futuristic city at night with neon lights reflecting on wet streets"

SNAP_DIR = Path("screenshots")
SNAP_DIR.mkdir(exist_ok=True)

_discord_client  = None
_discord_channel = None


async def _ensure_discord() -> None:
    global _discord_client, _discord_channel
    if _discord_channel is not None:
        return
    if not DISCORD_TOKEN:
        print("[discord] ⚠️ DISCORD_TOKEN not set — Discord sending disabled")
        return
    try:
        intents = discord.Intents.default()
        _discord_client = discord.Client(intents=intents)
        await _discord_client.login(DISCORD_TOKEN)
        _discord_channel = await _discord_client.fetch_channel(DISCORD_CHANNEL)
        print(f"[discord] ✅ Connected — will send to #{_discord_channel.name}")
    except Exception as e:
        print(f"[discord] ⚠️ Could not connect: {e} — continuing without Discord")
        _discord_client = None
        _discord_channel = None


async def _send_to_discord(content: str, filename: str, data: bytes) -> None:
    if _discord_channel is None:
        return
    try:
        await _discord_channel.send(
            content=content,
            file=discord.File(io.BytesIO(data), filename=filename),
        )
    except Exception as e:
        print(f"[discord] ⚠️ send failed: {e}")


async def progress(msg: str) -> None:
    print(f"[progress] {msg}", flush=True)


async def snap(label: str, data: bytes) -> None:
    path = SNAP_DIR / f"synthtest-{label}.jpg"
    path.write_bytes(data)
    print(f"[snap] 📸 {path}", flush=True)
    await _send_to_discord(f"📸 `{label}`", f"{label}.jpg", data)


async def main() -> None:
    print("=" * 60)
    print("Synthesia Gemini Omni generate test (fresh account, full flow)")
    print(f"  Prompt: {PROMPT}")
    print("=" * 60)

    await _ensure_discord()

    t0 = time.monotonic()
    video_bytes = await generate_synthesia_video(
        prompt=PROMPT,
        progress_cb=progress,
        screenshot_cb=snap,
    )
    dur = time.monotonic() - t0

    out = Path("test_output_synthesia.mp4")
    out.write_bytes(video_bytes)
    mb = len(video_bytes) / 1024 / 1024
    print(f"\n✅ Video saved → {out}  ({len(video_bytes):,} bytes, {mb:.1f} MB, {dur:.0f}s)")

    caption = f"🎬 **Synthesia test done!** ({dur:.0f}s)\n> 🖊️ {PROMPT}"
    if mb <= 8:
        await _send_to_discord(caption, "video.mp4", video_bytes)
    else:
        import aiohttp
        try:
            form = aiohttp.FormData()
            form.add_field("reqtype", "fileupload")
            form.add_field("fileToUpload", video_bytes, filename="video.mp4", content_type="video/mp4")
            async with aiohttp.ClientSession() as sess:
                async with sess.post("https://catbox.moe/user/api.php", data=form, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                    url = (await resp.text()).strip()
            if _discord_channel and url.startswith("https://"):
                await _discord_channel.send(f"{caption}\n📽️ {url}")
        except Exception as e:
            print(f"[catbox] upload failed: {e}")

    if _discord_client:
        await _discord_client.close()


asyncio.run(main())
