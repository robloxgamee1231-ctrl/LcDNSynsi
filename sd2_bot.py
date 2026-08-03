"""
sd2_bot.py — Seedance 2.0 video generation via CometAPI.

API base: https://api.cometapi.com
Auth:     Bearer token read from COMETAPI_KEY env var.
Docs:     https://apidoc.cometapi.com

Flow:
  1. POST /v1/videos  (multipart/form-data) → get task id
  2. Poll GET /v1/videos/{id}  until status in {success, completed, failed, error}
  3. GET /v1/videos/{id}/content  → download video bytes
"""

import asyncio
import os

import aiohttp

_COMET_BASE    = "https://api.cometapi.com"
_POLL_INTERVAL = 10   # seconds between status checks
_MAX_WAIT      = 600  # 10 minutes total timeout
_RETRY_DELAY   = 5    # seconds to wait before retrying a transient error

_TERMINAL = {"success", "completed", "failed", "error"}
_SUCCESS  = {"success", "completed"}


def _auth_headers() -> dict:
    token = os.environ.get("OPENAI_API_KEY", "")
    if not token:
        raise RuntimeError("OPENAI_API_KEY env var is not set")
    return {"Authorization": f"Bearer {token}"}


def _progress_int(progress) -> int:
    """Normalise a progress value (int/float/str/None) to 0-100."""
    if isinstance(progress, (int, float)):
        return int(progress)
    if isinstance(progress, str):
        try:
            return int(float(progress.rstrip("%")))
        except ValueError:
            pass
    return 0


async def generate_sd2_video(
    prompt: str,
    model: str = "doubao-seedance-2-0",
    size: str = "16:9",
    seconds: int = 5,
    audio: bool = True,
    seed: int | None = None,
    image_url: str | None = None,
    progress_cb=None,
) -> tuple[bytes, None]:
    """
    Submit a Seedance 2.0 task on CometAPI, poll until done, return
    (video_bytes, None).  Raises RuntimeError on any failure.
    Pass image_url for image-to-video (reference image) generation.
    """

    async def _report(msg: str) -> None:
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    await _report("⚙️ Initializing…")

    headers = _auth_headers()
    session_timeout = aiohttp.ClientTimeout(total=_MAX_WAIT + 180)

    async with aiohttp.ClientSession(timeout=session_timeout) as session:

        # ── 0. Pre-download reference image (if provided) ─────────────────────
        image_bytes = None
        image_ctype = "image/png"
        image_fname = "reference.png"
        if image_url:
            try:
                async with session.get(
                    image_url, timeout=aiohttp.ClientTimeout(total=60)
                ) as r:
                    if r.status == 200:
                        image_bytes = await r.read()
                        ct = r.headers.get("Content-Type", "")
                        if "jpeg" in ct or "jpg" in ct:
                            image_ctype, image_fname = "image/jpeg", "reference.jpg"
                        elif "webp" in ct:
                            image_ctype, image_fname = "image/webp", "reference.webp"
                        elif "gif" in ct:
                            image_ctype, image_fname = "image/gif",  "reference.gif"
            except Exception:
                pass  # proceed without image if download fails

        # ── 1. Submit task ─────────────────────────────────────────────────────
        await _report("⚙️ Initializing…")

        def _build_form() -> aiohttp.FormData:
            d = aiohttp.FormData()
            d.add_field("model",   model)
            d.add_field("prompt",  prompt)
            d.add_field("seconds", str(seconds))
            d.add_field("size",    size)
            if not audio:
                d.add_field("generate_audio", "false")
            if seed is not None:
                d.add_field("seed", str(seed))
            if image_bytes:
                d.add_field(
                    "input_reference", image_bytes,
                    filename=image_fname, content_type=image_ctype,
                )
            return d

        task_id: str = ""
        for attempt in range(1, 4):
            async with session.post(
                f"{_COMET_BASE}/v1/videos",
                data=_build_form(),
                headers=headers,
            ) as resp:
                if resp.status in (200, 201):
                    job      = await resp.json()
                    task_id  = job.get("id") or job.get("task_id") or ""
                    break
                body = await resp.text()
                if resp.status == 429 or 500 <= resp.status < 600:
                    if attempt < 3:
                        await asyncio.sleep(_RETRY_DELAY)
                        continue
                raise RuntimeError(
                    f"Task submit failed (HTTP {resp.status}): {body[:300]}"
                )

        if not task_id:
            raise RuntimeError(f"No task ID returned: {job}")

        # ── 2. Poll until success / failure ────────────────────────────────────
        elapsed = 0
        status  = "queued"

        while elapsed < _MAX_WAIT:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

            task: dict = {}
            for attempt in range(1, 4):
                async with session.get(
                    f"{_COMET_BASE}/v1/videos/{task_id}",
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        task = await resp.json()
                        break
                    body = await resp.text()
                    if resp.status == 429 or 500 <= resp.status < 600:
                        if attempt < 3:
                            await asyncio.sleep(_RETRY_DELAY)
                            continue
                    raise RuntimeError(f"Poll failed (HTTP {resp.status}): {body[:200]}")

            status   = str(task.get("status") or "unknown").lower()
            progress = _progress_int(task.get("progress"))

            if status in _SUCCESS or (status == "unknown" and progress >= 100):
                await _report("⚙️ Initializing… 100%")
                break
            elif status in _TERMINAL:
                err = task.get("error") or task.get("message") or "unknown error"
                raise RuntimeError(f"Generation failed: {err}")
            else:
                pct = progress if progress else min(94, int(95 * elapsed / (elapsed + 80)))
                await _report(f"⚙️ Initializing… {pct}%")
        else:
            raise RuntimeError(
                f"Timed out after {_MAX_WAIT}s — last status was '{status}'"
            )

        # ── 3. Download video bytes ────────────────────────────────────────────
        await _report("⚙️ Initializing… 100%")
        async with session.get(
            f"{_COMET_BASE}/v1/videos/{task_id}/content",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Video download failed (HTTP {resp.status}): {body[:200]}"
                )
            video_bytes = await resp.read()

    return video_bytes, None
