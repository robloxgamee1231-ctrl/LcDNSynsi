"""
Quick smoke-test for artlist_bot.py
Runs a real generation with a simple prompt and saves the output video.
"""
import asyncio
import sys
from pathlib import Path

# Make sure env vars are available (bot.py normally loads these)
import os
if not os.environ.get("ARTLIST_EMAIL"):
    print("❌ ARTLIST_EMAIL not set"); sys.exit(1)
if not os.environ.get("ARTLIST_PASSWORD"):
    print("❌ ARTLIST_PASSWORD not set"); sys.exit(1)

from artlist_bot import generate_artlist_video

LOG_LINES: list[str] = []

async def progress(msg: str) -> None:
    print(f"[progress] {msg}", flush=True)

async def snap(label: str, data: bytes) -> None:
    path = Path(f"screenshots/test_{label}.jpg")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    print(f"[snap] saved {path}", flush=True)

async def main() -> None:
    print("=" * 60)
    print("artlist_bot smoke test")
    print("=" * 60)

    video_bytes = await generate_artlist_video(
        prompt="a calm ocean wave rolling onto a sandy beach, golden hour",
        model="Gemini Omni Flash",
        duration=5,
        progress_cb=progress,
        screenshot_cb=snap,
    )

    out = Path("test_output.mp4")
    out.write_bytes(video_bytes)
    print(f"\n✅ Video saved to {out} ({len(video_bytes):,} bytes)")

asyncio.run(main())
