"""Send all screenshots to a Discord user by ID."""
import asyncio
import io
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN   = os.environ.get("DISCORD_TOKEN", "")
USER_ID = 806976883774324746

SCREENSHOT_DIRS = [
    Path("screenshots"),
    Path("screenshots/buzzy_dom_dump"),
    Path("screenshots/buzzy_e2e"),
]

EXTS = {".png", ".jpg", ".jpeg"}
MAX_BYTES = 8 * 1024 * 1024   # 8 MB Discord limit
BATCH_SIZE = 10                 # max files per message


def collect_files():
    files = []
    for d in SCREENSHOT_DIRS:
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in EXTS and f.is_file():
                    files.append(f)
    return files


async def main():
    intents = discord.Intents.default()
    client  = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Bot logged in as {client.user}")
        try:
            user = await client.fetch_user(USER_ID)
            dm   = await user.create_dm()

            all_files = collect_files()
            print(f"Found {len(all_files)} screenshot(s) to send")

            batch: list[discord.File] = []
            batch_bytes = 0
            sent_batches = 0

            async def flush(label=""):
                nonlocal batch, batch_bytes, sent_batches
                if not batch:
                    return
                content = f"📸 Screenshots batch {sent_batches + 1}" + (f" — {label}" if label else "")
                await dm.send(content=content, files=batch)
                sent_batches += 1
                print(f"  Sent batch {sent_batches} ({len(batch)} file(s))")
                batch = []
                batch_bytes = 0

            for path in all_files:
                size = path.stat().st_size
                # If adding this file would exceed limits, flush first
                if batch and (len(batch) >= BATCH_SIZE or batch_bytes + size > MAX_BYTES):
                    await flush()
                data = path.read_bytes()
                batch.append(discord.File(io.BytesIO(data), filename=path.name))
                batch_bytes += size

            await flush("(final)")
            await dm.send(f"✅ Done — sent **{len(all_files)}** screenshot(s) in **{sent_batches}** message(s).")
            print(f"All done. {sent_batches} batch(es) sent.")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await client.close()

    await client.start(TOKEN)


asyncio.run(main())
