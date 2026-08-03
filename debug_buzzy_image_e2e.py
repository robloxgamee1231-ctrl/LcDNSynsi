"""End-to-end test of the updated _generate_image_buzzy flow: full pipeline
(temp email -> login -> code -> Image Generator -> prompt -> Create -> wait
-> download), saving every debug screenshot to disk instead of DMing so it
can be inspected without spamming Discord."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from buzzy_bot import generate_buzzy_image

OUT_DIR = Path("screenshots/buzzy_e2e")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def progress(msg: str) -> None:
    print(f"[progress] {msg}", flush=True)


async def snap(label: str, img_bytes: bytes) -> None:
    safe = label.replace("[buzzy] ", "").replace("/", "_")
    path = OUT_DIR / f"{safe}.jpg"
    path.write_bytes(img_bytes)
    print(f"[snap] saved {path}", flush=True)


async def main() -> None:
    data = await generate_buzzy_image(
        prompt="a small red fox curled up asleep in a pile of autumn leaves, warm painterly light",
        model="Nano Banana",
        progress_cb=progress,
        screenshot_cb=snap,
    )
    out = OUT_DIR / "final_image.png"
    out.write_bytes(data)
    print(f"[result] SUCCESS — downloaded {len(data)//1024} KB -> {out}", flush=True)


asyncio.run(main())
