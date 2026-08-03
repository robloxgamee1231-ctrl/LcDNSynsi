"""
test_generate_click.py — Full end-to-end test for the Artlist Generate button.

Uses fresh cookies from .artlist_session.json (no email/password required).
Tests Kling 3.0 with a reference image.

Saves screenshots to screenshots/gentest_* at every step,
and sends each screenshot + the final video to the owner via DM.
"""
import asyncio
import io
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

_COOKIE_FILE = Path(__file__).parent / ".artlist_session.json"
if not _COOKIE_FILE.exists():
    # Fall back to email/password if no cookie file
    if not os.environ.get("ARTLIST_EMAIL"):
        print("❌ No .artlist_session.json and ARTLIST_EMAIL not set — cannot proceed")
        import sys; sys.exit(1)

DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL = 736027479482826802   # owner DM user ID

import discord
from artlist_bot import generate_artlist_video

PROMPT       = "a sleek futuristic city at night with neon lights reflecting on wet streets"
MODEL        = "Kling 3.0"
RESOLUTION   = "720p"
ASPECT_RATIO = "16:9"
DURATION     = 5   # seconds

# Reference image to test image-to-video
_IMAGE_FILE  = Path(__file__).parent / "attached_assets" / "1780953284042_1783990481152.png"

SNAP_DIR = Path("screenshots")
SNAP_DIR.mkdir(exist_ok=True)

# Discord client (minimal, just for sending files)
_discord_client = None
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
    path = SNAP_DIR / f"gentest-{label}.jpg"
    path.write_bytes(data)
    print(f"[snap] 📸 {path}", flush=True)
    await _send_to_discord(f"📸 `{label}`", f"{label}.jpg", data)


async def main() -> None:
    print("=" * 60)
    print("Artlist Generate-button test (cookie login + Kling 3.0 + image)")
    print(f"  Prompt:       {PROMPT}")
    print(f"  Model:        {MODEL}")
    print(f"  Resolution:   {RESOLUTION}")
    print(f"  Aspect ratio: {ASPECT_RATIO}")
    print(f"  Duration:     {DURATION}s")
    print(f"  Cookie file:  {_COOKIE_FILE} ({'%d bytes' % _COOKIE_FILE.stat().st_size if _COOKIE_FILE.exists() else 'not present — will use password login'})")
    print("=" * 60)

    await _ensure_discord()

    # Load reference image if the file exists
    image_ref_bytes = None
    image_ref_ext   = ".png"
    if _IMAGE_FILE.exists():
        image_ref_bytes = _IMAGE_FILE.read_bytes()
        image_ref_ext   = _IMAGE_FILE.suffix or ".png"
        print(f"  Image ref:    {_IMAGE_FILE} ({len(image_ref_bytes):,} bytes)")
    else:
        print(f"  Image ref:    NOT FOUND ({_IMAGE_FILE}) — skipping image upload")

    video_bytes = await generate_artlist_video(
        prompt=PROMPT,
        model=MODEL,
        resolution=RESOLUTION,
        aspect_ratio=ASPECT_RATIO,
        duration=DURATION,
        progress_cb=progress,
        screenshot_cb=snap,
        image_ref_bytes=image_ref_bytes,
        image_ref_ext=image_ref_ext,
    )

    out = Path("test_output.mp4")
    out.write_bytes(video_bytes)
    mb = len(video_bytes) / 1024 / 1024
    print(f"\n✅ Video saved → {out}  ({len(video_bytes):,} bytes, {mb:.1f} MB)")

    # Send video to Discord
    label = f"{MODEL} · {RESOLUTION} · {DURATION}s"
    caption = f"🎬 **Video done!** ({label})\n> 🖊️ {PROMPT}"
    if mb <= 8:
        await _send_to_discord(caption, "video.mp4", video_bytes)
    else:
        # Upload to catbox for larger files
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
