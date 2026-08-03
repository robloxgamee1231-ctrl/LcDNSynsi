"""
test_cookie_login.py — Test Artlist video generation using saved browser cookies.
No email/password needed. Cookies must be in .artlist_session.json
"""
import asyncio
from pathlib import Path

PROMPT       = "a calm ocean wave rolling onto a sandy beach, golden hour"
MODEL        = "Seedance 2.0"
RESOLUTION   = "720p"
ASPECT_RATIO = "16:9"
DURATION     = 15

SNAP_DIR = Path("screenshots")
SNAP_DIR.mkdir(exist_ok=True)


async def progress(msg: str) -> None:
    print(f"[progress] {msg}", flush=True)


async def snap(label: str, data: bytes) -> None:
    # Strip "[artlist] " prefix that _snap() prepends
    safe = label.replace("[artlist] ", "").replace(" ", "-").replace("/", "-")
    path = SNAP_DIR / f"cookietest-{safe}.jpg"
    path.write_bytes(data)
    print(f"[snap] 📸 {path}", flush=True)


async def main() -> None:
    if not Path(".artlist_session.json").exists():
        print("❌ .artlist_session.json not found — put your cookies there first")
        return

    from artlist_bot import generate_artlist_video

    print("=" * 60)
    print("Artlist cookie-login generate test")
    print(f"  Prompt:  {PROMPT}")
    print(f"  Model:   {MODEL}")
    print(f"  Res:     {RESOLUTION}  AR: {ASPECT_RATIO}  Dur: {DURATION}s")
    print("=" * 60)

    video_bytes = await generate_artlist_video(
        prompt=PROMPT,
        model=MODEL,
        resolution=RESOLUTION,
        aspect_ratio=ASPECT_RATIO,
        duration=DURATION,
        progress_cb=progress,
        screenshot_cb=snap,
    )

    out = Path("test_output.mp4")
    out.write_bytes(video_bytes)
    print(f"\n✅ Video saved → {out}  ({len(video_bytes):,} bytes / {len(video_bytes)/1024/1024:.1f} MB)")


asyncio.run(main())
