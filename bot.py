"""
bot.py — Discord Bot (AI Image & Video Generator)
Commands:
  /image prompt model  — Generate an AI image (~2–4 min)
  /video prompt        — Generate an AI video (~2–4 min)
  /sd2   prompt        — Generate a Seedance 2.0 video via SD2 API (~2–5 min)

Admin/Owner commands:
  /giverole  @user role          — Assign bot role (beta/admin) + reset credits
  /takerole  @user               — Reset user to regular role
  /addcredits   @user amount     — Add credits to a user
  /removecredits @user amount    — Remove credits from a user
  /setcredits   @user amount     — Set exact credit balance
  /credits  [@user]              — Check credit balance
  /botban   @user [reason]       — Ban a user from using the bot
  /botunban @user                — Unban a user
  /bottimeout @user minutes      — Temporarily block a user
  /settitle @user title          — Assign a title to a user
  /profile  [@user]              — View a user's bot profile
  /botstats                      — Bot usage statistics (owner only)
  /botlogs  [@user]              — Recent audit log (admin/owner)

Bot access role management (Admin/Owner):
  /addbotaccess @role            — Allow a server role to use the bot
  /removebotaccess @role         — Remove a server role's bot access
  /listbotaccess                 — Show which server roles can use the bot

Server security (Owner only):
  /allowserver [id]              — Whitelist current (or given) server
  /banserver   [id] [reason]     — Ban a server and immediately leave it
  /unbanserver [id]              — Unban a server (won't auto-rejoin)
  /servers                       — List all known servers + their status
  /myservers                     — Show every Discord server the bot is in
"""

import asyncio
import io
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ── Watermark ──────────────────────────────────────────────────────────────────
_WATERMARK_TEXT = "Aura ⁹⁹⁹⁺☠"
_WM_FONT_PATH   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

def _apply_watermark(image_bytes: bytes) -> bytes:
    """Stamp _WATERMARK_TEXT in a small tiled grid across an image and return new bytes."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import math
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        w, h = img.size

        wm   = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(wm)

        # Smaller font — readable but not overpowering
        font_size = max(11, min(w, h) // 40)
        try:
            font = ImageFont.truetype(_WM_FONT_PATH, font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), _WATERMARK_TEXT, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

        # Tile across the whole frame with enough gap to feel subtle
        step_x = tw + 50
        step_y = th + 35
        for row in range(-1, math.ceil(h / step_y) + 2):
            for col in range(-1, math.ceil(w / step_x) + 2):
                x = col * step_x + (row % 2) * (step_x // 2)
                y = row * step_y
                draw.text((x + 1, y + 1), _WATERMARK_TEXT, font=font, fill=(0, 0, 0, 55))
                draw.text((x,     y    ), _WATERMARK_TEXT, font=font, fill=(255, 255, 255, 55))

        out = Image.alpha_composite(img, wm)
        buf = io.BytesIO()
        out.convert("RGB").save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except Exception as _wm_err:
        print(f"[watermark] failed: {_wm_err}")
        return image_bytes

# Loading GIFs — cycle through them while generation runs.
# Falls back to the static PNG if no GIFs are found.
_ASSETS_DIR = Path(__file__).parent / "assets"
_PROGRESS_IMAGE = _ASSETS_DIR / "progress_placeholder.png"
_LOADING_GIFS: list[Path] = sorted(_ASSETS_DIR.glob("loading_*.gif"))
if not _LOADING_GIFS:
    _LOADING_GIFS = [_PROGRESS_IMAGE] if _PROGRESS_IMAGE.exists() else []

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import aiohttp
import discord
from discord import app_commands


import db
from progress import ProgressTracker

# ── Optional modules (require playwright — not available on Android/Termux) ────
_PLAYWRIGHT_AVAILABLE = True
try:
    import artlist_bot as _art
    import synthesia_bot as _syn
    import sd2_bot as _sd2
    import bypass as _bypass
except (ImportError, ModuleNotFoundError) as _e:
    _PLAYWRIGHT_AVAILABLE = False
    _art = None  # type: ignore
    _syn = None  # type: ignore
    _sd2 = None  # type: ignore
    _bypass = None  # type: ignore
    print(f"[bot] playwright not available — artlist/synthesia/sd2 commands disabled ({_e})")

# ── synthesia_api: direct HTTP approach (primary, no browser needed) ───────────
try:
    import synthesia_api as _syn_api
except ImportError as _e:
    _syn_api = None  # type: ignore
    print(f"[bot] synthesia_api not available ({_e})")

# ── Init ───────────────────────────────────────────────────────────────────────

db.init_db()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN not set — cannot start")
    sys.exit(1)

_OWNER_ID              = 1506411605766967417
_SCREENSHOT_USER_IDS   = [736027479482826802, 1506411605766967417]  # DM screenshots to all
_DISCORD_MAX_ATTACH_MB = 8

# ── Video generation concurrency ──────────────────────────────────────────────
# Up to _MAX_CONCURRENT videos generate at the same time — each in its own
# isolated Playwright browser context so user data never mixes.
# Extra requests wait in a queue and are shown their position.
_MAX_CONCURRENT = 3
_GEN_SEM   = asyncio.Semaphore(_MAX_CONCURRENT)
_GEN_DEPTH = 0   # total running + waiting; incremented on arrival, decremented on exit

# ── Single-server lock ─────────────────────────────────────────────────────────
# The bot only operates inside this one server. Everyone else gets a join button.
_TARGET_INVITE_URL = "https://discord.gg/pFD3Kna6Se"
_TARGET_GUILD_ID: int = 1517657288444346398   # hardcoded home server

# Ensure owner exists in DB with owner role
def _bootstrap_owner():
    db.ensure_user(_OWNER_ID, "Owner")
    if db.ROLE_OWNER not in db.get_user_roles(_OWNER_ID):
        db.add_role(_OWNER_ID, db.ROLE_OWNER)

_bootstrap_owner()

ALLOWED_GUILD_IDS: set[int] = {
    int(s.strip())
    for s in os.environ.get("ALLOWED_GUILD_IDS", "").split(",")
    if s.strip().isdigit()
}

# Role display names and emojis
_ROLE_DISPLAY = {
    db.ROLE_USER:  "👤 User",
    db.ROLE_BETA:  "⚡ Beta",
    db.ROLE_ADMIN: "🛡️ Admin",
    db.ROLE_OWNER: "👑 Owner",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

class _JoinView(discord.ui.View):
    """A persistent View with a single 'Join Server' link button."""
    def __init__(self):
        super().__init__()
        self.add_item(discord.ui.Button(
            label="Join the Server",
            style=discord.ButtonStyle.link,
            url=_TARGET_INVITE_URL,
            emoji="🔗",
        ))


# ══════════════════════════════════════════════════════════════════════════════
# TICKET SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

_TICKET_CATEGORY_NAME  = "🎟️ Tickets & Support"
_TICKET_LOG_CHANNEL    = "ticket-logs"


def _is_staff(member: discord.Member) -> bool:
    """True if owner or has administrator permission."""
    return member.id == _OWNER_ID or member.guild_permissions.administrator


def _next_ticket_number(guild: discord.Guild) -> int:
    count = sum(
        1 for ch in guild.text_channels
        if ch.name.startswith("ticket-") or ch.name.startswith("closed-")
    )
    return count + 1


async def _create_ticket_channel(
    interaction: discord.Interaction,
    kind: str,          # "ticket" | "report"
) -> discord.TextChannel | None:
    """Create a private ticket/report channel and return it."""
    guild  = interaction.guild
    member = interaction.user

    # ── Find or create the support category ─────────────────────────────────
    category = discord.utils.find(
        lambda c: "ticket" in c.name.lower() or "support" in c.name.lower(),
        guild.categories,
    )
    if not category:
        await interaction.followup.send(
            "❌ Couldn't find the **Tickets & Support** category. Ask an admin to run `/setup-tickets` first.",
            ephemeral=True,
        )
        return None

    # ── Guard: one open ticket per user ─────────────────────────────────────
    for ch in category.text_channels:
        if ch.topic and f"opener:{member.id}" in ch.topic and ch.name.startswith("ticket-"):
            await interaction.followup.send(
                f"⚠️ You already have an open ticket: {ch.mention}",
                ephemeral=True,
            )
            return None

    num = _next_ticket_number(guild)
    prefix = "ticket" if kind == "ticket" else "report"
    ch_name = f"{prefix}-{num:04d}"

    # ── Permissions ──────────────────────────────────────────────────────────
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            manage_channels=True, manage_messages=True,
        ),
    }
    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    topic = f"opener:{member.id} | kind:{kind} | status:open"
    channel = await guild.create_text_channel(
        name=ch_name,
        category=category,
        overwrites=overwrites,
        topic=topic,
        reason=f"Ticket opened by {member}",
    )

    # ── Opening embed ────────────────────────────────────────────────────────
    label   = "Ticket" if kind == "ticket"  else "Report"
    color   = 0x5865F2 if kind == "ticket"  else 0xED4245
    emoji   = "🎟️"    if kind == "ticket"  else "🚨"

    embed = discord.Embed(
        title=f"{emoji}  {label} #{num:04d}",
        description=(
            f"Hi {member.mention}! Staff have been notified and will be with you shortly. 💜\n\n"
            f"**Please describe your issue in as much detail as possible.**"
        ),
        color=color,
    )
    embed.add_field(name="📋  Status",    value="🟢 Open — waiting for staff",  inline=True)
    embed.add_field(name="👤  Opened by", value=member.mention,                  inline=True)
    embed.add_field(name="🛡️  Claimed by", value="*Unclaimed*",                 inline=True)
    embed.set_footer(text="Staff: use the buttons below to manage this ticket.")

    await channel.send(
        content=f"{member.mention} — your {label.lower()} has been created.",
        embed=embed,
        view=TicketActiveView(),
    )

    # ── Log ──────────────────────────────────────────────────────────────────
    log_ch = discord.utils.find(
        lambda c: c.name == _TICKET_LOG_CHANNEL and isinstance(c, discord.TextChannel),
        guild.channels,
    )
    if log_ch:
        log_embed = discord.Embed(
            title=f"📋  {label} Opened — #{num:04d}",
            color=color,
        )
        log_embed.add_field(name="User",    value=f"{member} (`{member.id}`)", inline=True)
        log_embed.add_field(name="Channel", value=channel.mention,             inline=True)
        await log_ch.send(embed=log_embed)

    return channel


# ── Persistent Views ──────────────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    """The panel posted in #create-ticket with the two action buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎟️  Create a Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_create",
    )
    async def create_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True)
        ch = await _create_ticket_channel(interaction, kind="ticket")
        if ch:
            await interaction.followup.send(
                f"✅ Your ticket has been created: {ch.mention}", ephemeral=True
            )

    @discord.ui.button(
        label="🚨  Report Someone",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_report",
    )
    async def report_someone(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True)
        ch = await _create_ticket_channel(interaction, kind="report")
        if ch:
            await interaction.followup.send(
                f"✅ Your report has been created: {ch.mention}", ephemeral=True
            )


class TicketActiveView(discord.ui.View):
    """Buttons inside an open ticket: Claim + Close."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅  Claim Ticket",
        style=discord.ButtonStyle.success,
        custom_id="ticket_claim",
    )
    async def claim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Only staff can claim tickets.", ephemeral=True
            )
            return

        # Update embed to show claimer
        msg   = interaction.message
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        # Find and update the "Claimed by" field
        for i, f in enumerate(embed.fields):
            if "claimed" in f.name.lower():
                embed.set_field_at(i, name=f.name, value=interaction.user.mention, inline=True)
                break
        # Replace status field
        for i, f in enumerate(embed.fields):
            if "status" in f.name.lower():
                embed.set_field_at(i, name=f.name, value="🟡 In progress — claimed by staff", inline=True)
                break

        # Disable the claim button once claimed
        button.disabled = True
        button.label    = f"✅  Claimed by {interaction.user.display_name}"

        await msg.edit(embed=embed, view=self)
        await interaction.response.send_message(
            f"🎟️ {interaction.user.mention} has **claimed** this ticket!", ephemeral=False
        )

    @discord.ui.button(
        label="🔒  Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close",
    )
    async def close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Only staff can close tickets.", ephemeral=True
            )
            return

        ch       = interaction.channel
        guild    = ch.guild
        ch_name  = ch.name

        # ── Grab opener ID from topic before deleting ────────────────────────
        opener_id = None
        if ch.topic:
            import re as _tr
            m = _tr.search(r"opener:(\d+)", ch.topic)
            if m:
                opener_id = int(m.group(1))

        # ── Acknowledge the interaction immediately (channel is about to go) ─
        await interaction.response.send_message(
            "🔒 Closing ticket — this channel will be deleted in 5 seconds…",
            ephemeral=False,
        )

        # ── DM the opener ────────────────────────────────────────────────────
        if opener_id:
            opener = guild.get_member(opener_id)
            if not opener:
                try:
                    opener = await client.fetch_user(opener_id)
                except Exception:
                    opener = None
            if opener:
                try:
                    dm_embed = discord.Embed(
                        title="🔒  Your ticket has been closed",
                        description=(
                            f"Your ticket **#{ch_name}** in **{guild.name}** was closed "
                            f"by {interaction.user.mention}.\n\n"
                            "If you need further help, feel free to open a new ticket anytime. 💜"
                        ),
                        color=0xED4245,
                    )
                    dm_embed.set_footer(text=f"{guild.name} • Support Team")
                    await opener.send(embed=dm_embed)
                except Exception:
                    pass  # DMs may be disabled

        # ── Log closure ──────────────────────────────────────────────────────
        log_ch = discord.utils.find(
            lambda c: c.name == _TICKET_LOG_CHANNEL and isinstance(c, discord.TextChannel),
            guild.channels,
        )
        if log_ch:
            log_embed = discord.Embed(
                title="🔒  Ticket Closed & Deleted",
                color=0xED4245,
            )
            log_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Channel",   value=f"#{ch_name}",            inline=True)
            if opener_id:
                log_embed.add_field(name="Opener", value=f"<@{opener_id}>",       inline=True)
            await log_ch.send(embed=log_embed)

        # ── Wait a moment so the user sees the message, then delete ──────────
        await asyncio.sleep(5)
        try:
            await ch.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            pass


import re as _re_bypass

def _bypass_display_prompt(prompt: str) -> str:
    """
    Return a Discord-formatted version of the bypass prompt.
    Swapped character names and IP terms are shown in **bold** so users can
    see exactly which words were replaced. Nothing else is added.
    """
    if not _PLAYWRIGHT_AVAILABLE or _art is None:
        return prompt
    result = prompt

    # Bold-replace character names
    result = _art._BYPASS_CHAR_PATTERN.sub(
        lambda m: f"**{_art._BYPASS_CHARACTER_MAP[m.group(0).lower()]}**",
        result,
    )

    # Bold-replace IP-specific terms
    result = _art._BYPASS_TERMS_PATTERN.sub(
        lambda m: f"**{_art._BYPASS_TERMS_MAP[m.group(0).lower()]}**",
        result,
    )

    # Strip legacy tags
    result = _re_bypass.sub(r"11ii(.+?)11ii", r"\1", result)

    return result


async def _check_guild(interaction: discord.Interaction) -> bool:
    """
    Return True only if the interaction comes from the one allowed server.
    Everyone else gets an ephemeral message with a 'Join Server' button.
    The bot owner is allowed everywhere (DMs included).
    """
    if interaction.user.id == _OWNER_ID:
        return True

    gid = interaction.guild_id

    # Allow if we haven't resolved the target guild yet (startup race) —
    # fall through to the old whitelist logic as a safe fallback.
    if _TARGET_GUILD_ID is not None:
        if gid != _TARGET_GUILD_ID:
            await interaction.response.send_message(
                "bucky not so fast buddy 🐦\nhttps://discord.gg/pFD3Kna6Se",
                view=_JoinView(),
                ephemeral=True,
            )
            return False
        return True

    # ── Fallback (should not normally be reached since _TARGET_GUILD_ID is hardcoded) ──
    if gid is None:
        return False

    if gid != _TARGET_GUILD_ID:
        await interaction.response.send_message(
            "bucky not so fast buddy 🐦\nhttps://discord.gg/pFD3Kna6Se",
            view=_JoinView(),
            ephemeral=True,
        )
        return False

    return True


def _credits_str(user_id: int) -> str:
    if db.is_infinite(user_id):
        return "∞"
    bal = db.get_credits(user_id)
    return f"{bal:,}"


def _mask(text: str) -> str:
    """Strip all website / service names from user-facing output."""
    import re
    # Remove any URLs entirely
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    # Remove service / site names
    text = re.sub(r"\bartlist\.io\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bartlist\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\btoolkit\.artlist\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\btoolkit\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcatbox\.moe\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcatbox\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbuzzy\.now\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbuzzy\b", "", text, flags=re.IGNORECASE)
    # SD2 API server
    text = re.sub(r"14\.103\.8\.136(:\d+)?", "", text)
    # Clean up any double spaces left behind
    text = re.sub(r"  +", " ", text).strip()
    return text


async def _ensure_and_check(interaction: discord.Interaction) -> tuple[bool, str]:
    """Ensure user in DB. Returns (ok, error_message)."""
    u = interaction.user
    db.ensure_user(u.id, str(u))
    blocked, reason = db.is_blocked(u.id)
    if blocked:
        return False, reason
    return True, ""


async def _dm_owner(client: discord.Client, label: str, img_bytes: bytes) -> None:
    """Send a screenshot/video directly to all screenshot recipients via DM."""
    for uid in _SCREENSHOT_USER_IDS:
        try:
            user = client.get_user(uid) or await client.fetch_user(uid)
            dm = user.dm_channel or await user.create_dm()
            await dm.send(
                content=f"📸 `{label}`",
                file=discord.File(io.BytesIO(img_bytes), filename="debug.jpg"),
            )
        except Exception as e:
            print(f"[screenshot] ❌ DM send failed for {uid}: {e}")


async def _dm_owner_file(client: discord.Client, label: str, file_bytes: bytes, filename: str) -> None:
    """DM the raw video/image file to the owner only."""
    try:
        owner_user = client.get_user(_OWNER_ID) or await client.fetch_user(_OWNER_ID)
        dm = owner_user.dm_channel or await owner_user.create_dm()
        await dm.send(
            content=f"📥 `{label}`",
            file=discord.File(io.BytesIO(file_bytes), filename=filename),
        )
    except Exception as e:
        print(f"[dm_owner_file] ❌ {e}")


_INTRO_PATHS = [_ASSETS_DIR / "intro.mp4"]


def _run_prepend_intro(video_bytes: bytes) -> bytes:
    """Blocking: prepend a randomly chosen intro to video_bytes using ffmpeg. Returns final bytes."""
    import subprocess
    import tempfile
    import random
    import json

    available = [p for p in _INTRO_PATHS if p.exists()]
    if not available:
        print("[intro] ⚠️ no intro files found in assets — skipping")
        return video_bytes
    _INTRO_PATH = random.choice(available)
    print(f"[intro] 🎲 using {_INTRO_PATH.name}")

    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir)
        gen_path = p / "gen.mp4"
        out_path = p / "out.mp4"
        gen_path.write_bytes(video_bytes)

        # Probe intro dimensions
        probe_intro = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(_INTRO_PATH)],
            capture_output=True, timeout=30,
        )
        intro_info = json.loads(probe_intro.stdout or b"{}")
        intro_w, intro_h = 848, 480  # fallback
        for s in intro_info.get("streams", []):
            if s.get("codec_type") == "video":
                intro_w = s.get("width", intro_w)
                intro_h = s.get("height", intro_h)
                break
        intro_has_audio = any(s.get("codec_type") == "audio" for s in intro_info.get("streams", []))

        # Probe gen video audio
        probe_gen = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(gen_path)],
            capture_output=True, timeout=30,
        )
        gen_has_audio = b"audio" in probe_gen.stdout

        # Scale gen video to match intro resolution, then concat
        # [0] = intro, [1] = gen (scaled to intro size)
        if intro_has_audio and gen_has_audio:
            filter_str = (
                f"[1:v]scale={intro_w}:{intro_h}:force_original_aspect_ratio=decrease,"
                f"pad={intro_w}:{intro_h}:(ow-iw)/2:(oh-ih)/2[genv];"
                f"[0:v:0][0:a:0][genv][1:a:0]concat=n=2:v=1:a=1[outv][outa]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(_INTRO_PATH), "-i", str(gen_path),
                "-filter_complex", filter_str,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac",
                str(out_path),
            ]
        else:
            filter_str = (
                f"[1:v]scale={intro_w}:{intro_h}:force_original_aspect_ratio=decrease,"
                f"pad={intro_w}:{intro_h}:(ow-iw)/2:(oh-ih)/2[genv];"
                f"[0:v:0][genv]concat=n=2:v=1[outv]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(_INTRO_PATH), "-i", str(gen_path),
                "-filter_complex", filter_str,
                "-map", "[outv]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-an",
                str(out_path),
            ]

        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and out_path.exists():
            print(f"[intro] ✅ prepended intro ({len(video_bytes)//1024}KB → {out_path.stat().st_size//1024}KB)")
            return out_path.read_bytes()

        print(f"[intro] ⚠️ ffmpeg error (rc={result.returncode}): {result.stderr.decode()[-400:]}")
        return video_bytes


async def _prepend_intro(video_bytes: bytes) -> bytes:
    """Async wrapper: run ffmpeg intro prepend in a thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_prepend_intro, video_bytes)


async def _upload_streamable(
    video_bytes: bytes, filename: str = "video.mp4"
) -> tuple[str | None, bytes | None]:
    """
    Upload video bytes to Streamable.com.
    Polls until processing is complete, then downloads the final mp4 from Streamable.
    Returns (share_url, downloaded_bytes).  Either value may be None on failure.
    """
    import base64
    email    = os.environ.get("STREAMABLE_EMAIL", "")
    password = os.environ.get("STREAMABLE_PASSWORD", "")
    if not email or not password:
        print("[enteralizing] ⚠️ STREAMABLE_EMAIL / STREAMABLE_PASSWORD not set")
        return None, None
    auth_header = "Basic " + base64.b64encode(f"{email}:{password}".encode()).decode()
    headers = {"Authorization": auth_header}

    try:
        async with aiohttp.ClientSession() as session:
            # ── 1. Upload ────────────────────────────────────────────────────
            data = aiohttp.FormData()
            data.add_field("file", video_bytes, filename=filename, content_type="video/mp4")
            async with session.post(
                "https://api.streamable.com/upload",
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                body = await resp.json()
                shortcode = body.get("shortcode")
                if not shortcode:
                    print(f"[enteralizing] ⚠️ upload response: {body}")
                    return None, None
            share_url = f"https://streamable.com/{shortcode}"
            print(f"[enteralizing] ✅ uploaded → {share_url}")

            # ── 2. Poll until Streamable finishes processing ─────────────────
            # status: 0 = uploading, 1 = processing, 2 = ready, 3 = error
            mp4_url: str | None = None
            for _attempt in range(24):   # up to ~2 min
                await asyncio.sleep(5)
                async with session.get(
                    f"https://api.streamable.com/videos/{shortcode}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as info_resp:
                    info = await info_resp.json()
                status = info.get("status", -1)
                if status == 2:
                    files = info.get("files") or {}
                    # prefer mp4-mobile → mp4 → any key
                    for key in ("mp4-mobile", "mp4"):
                        if key in files and files[key].get("url"):
                            mp4_url = "https:" + files[key]["url"] if files[key]["url"].startswith("//") else files[key]["url"]
                            break
                    if not mp4_url:
                        # fallback: grab the first url in files
                        for f in files.values():
                            if f.get("url"):
                                mp4_url = "https:" + f["url"] if f["url"].startswith("//") else f["url"]
                                break
                    break
                elif status == 3:
                    print(f"[enteralizing] ⚠️ processing failed for {shortcode}")
                    return share_url, None
                print(f"[enteralizing] ⏳ status={status}, waiting…")
            else:
                print(f"[enteralizing] ⚠️ timed out waiting for {shortcode} to process")
                return share_url, None

            if not mp4_url:
                print(f"[enteralizing] ⚠️ no mp4 URL in response for {shortcode}")
                return share_url, None

            # ── 3. Download the processed video from Streamable ──────────────
            print(f"[enteralizing] ⬇️ downloading from {mp4_url}")
            async with session.get(
                mp4_url, timeout=aiohttp.ClientTimeout(total=180)
            ) as dl_resp:
                if dl_resp.status == 200:
                    dl_bytes = await dl_resp.read()
                    print(f"[enteralizing] ✅ downloaded {len(dl_bytes)//1024}KB from Streamable")
                    return share_url, dl_bytes
                print(f"[enteralizing] ⚠️ download HTTP {dl_resp.status}")
                return share_url, None

    except Exception as e:
        print(f"[enteralizing] ❌ {e}")
    return None, None


async def _upload_catbox(video_bytes: bytes, filename: str) -> str | None:
    try:
        data = aiohttp.FormData()
        data.add_field("reqtype", "fileupload")
        data.add_field(
            "fileToUpload", video_bytes,
            filename=filename, content_type="video/mp4",
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://catbox.moe/user/api.php",
                data=data,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                url = (await resp.text()).strip()
                if url.startswith("https://"):
                    return url
    except Exception as e:
        print(f"[catbox] error: {e}")
    return None


def _is_admin_or_owner(user_id: int) -> bool:
    role = db.get_role(user_id)
    return role in (db.ROLE_ADMIN, db.ROLE_OWNER)


def _is_owner(user_id: int) -> bool:
    return user_id == _OWNER_ID


def _has_discord_role_access(member: discord.Member | discord.User) -> bool:
    """
    Return True if the member is allowed to use the bot.
    - Owner is always allowed.
    - Bot admin/owner role is always allowed.
    - If no allowed Discord roles are configured → everyone is allowed.
    - Otherwise the member must have at least one of the configured Discord server roles.
    """
    if member.id == _OWNER_ID:
        return True
    if _is_admin_or_owner(member.id):
        return True
    allowed = db.get_allowed_discord_roles()
    if not allowed:
        return True  # No roles configured — allow everyone in the server
    if not isinstance(member, discord.Member):
        return False  # DM with no roles — can't verify
    return bool({r.id for r in member.roles}.intersection(allowed))


def _profile_embed(target: discord.User | discord.Member, u: dict) -> discord.Embed:
    role  = u.get("role", db.ROLE_USER)
    title = u.get("title") or ""
    cred  = "∞" if role == db.ROLE_OWNER else f"{u.get('credits', 0):,}"
    color_map = {
        db.ROLE_OWNER: 0xFFD700,
        db.ROLE_ADMIN: 0xFF4500,
        db.ROLE_BETA:  0x7289DA,
        db.ROLE_USER:  0x2ECC71,
    }
    color = color_map.get(role, 0x2ECC71)

    embed = discord.Embed(color=color)
    embed.set_author(name=str(target), icon_url=target.display_avatar.url)
    if title:
        embed.description = f"**{title}**"
    embed.add_field(name="Role",    value=_ROLE_DISPLAY.get(role, role), inline=True)
    embed.add_field(name="Credits", value=f"💳 {cred}",                   inline=True)
    if u.get("is_banned"):
        embed.add_field(
            name="⚠️ Status",
            value=f"Banned — {u.get('ban_reason') or 'No reason'}",
            inline=False,
        )
    embed.set_footer(text=f"ID: {target.id}")
    return embed


# ── Bot client ─────────────────────────────────────────────────────────────────

class _LockedTree(app_commands.CommandTree):
    """CommandTree that gate-checks EVERY slash command via _check_guild."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # _check_guild sends the error reply itself when returning False
        return await _check_guild(interaction)


class DiscordBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = _LockedTree(self)

    async def setup_hook(self):
        # Guild-only sync — avoids duplicate commands showing in Discord
        # (global + guild registration causes commands to appear twice/thrice)
        guild = discord.Object(id=_TARGET_GUILD_ID)

        # Copy the in-memory command tree to the guild scope and sync it.
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

        # Wipe any stale GLOBAL command registrations left over from before
        # this bot switched to guild-only sync. Global commands persist on
        # Discord's side until explicitly cleared, so leaving this out is
        # what causes commands to show up doubled/tripled in the client.
        self.tree.clear_commands(guild=None)
        await self.tree.sync()

        # ── Register persistent views so buttons survive restarts ────────────
        self.add_view(TicketPanelView())
        self.add_view(TicketActiveView())
        self.add_view(GiveawayView())

        print("✅ Slash commands synced (guild only — stale globals cleared)")
        print()
        print("╔══════════════════════════════════════════════╗")
        print("║         🤖  BOT INITIALIZED  🤖              ║")
        print("║                                              ║")
        print("║   Made by  ynx · ynix · Jimmy               ║")
        print("╚══════════════════════════════════════════════╝")


client = DiscordBot()


@client.event
async def on_ready():
    global _TARGET_GUILD_ID
    print("=" * 50)
    print(f"✅ Bot online — {client.user}")
    print("🖼️ /image · 🎬 /video · 💳 /credits · 👤 /profile")
    print("=" * 50)

    print(f"🔐 Locked to server ID: {_TARGET_GUILD_ID}")

    # Register guilds and leave any that aren't the target
    for g in client.guilds:
        db.ensure_guild(g.id, g.name)
        if _TARGET_GUILD_ID and g.id != _TARGET_GUILD_ID:
            print(f"[security] ⛔ leaving non-target guild: {g.name} ({g.id})")
            try:
                await g.leave()
            except Exception as e:
                print(f"[security] could not leave {g.id}: {e}")


@client.event
async def on_guild_join(guild: discord.Guild):
    """Leave immediately if not the target server."""
    print(f"[security] joined guild: {guild.name} ({guild.id})")
    db.ensure_guild(guild.id, guild.name)
    db.log_action(0, "guild_join", f"{guild.name} ({guild.id})", actor_id=None)

    if _TARGET_GUILD_ID and guild.id != _TARGET_GUILD_ID:
        print(f"[security] ⛔ not the target guild — leaving {guild.name} ({guild.id})")
        try:
            await guild.leave()
        except Exception as e:
            print(f"[security] could not leave {guild.id}: {e}")
        # Notify owner
        try:
            owner_user = client.get_user(_OWNER_ID) or await client.fetch_user(_OWNER_ID)
            dm = owner_user.dm_channel or await owner_user.create_dm()
            await dm.send(
                f"🔔 **Bot added to wrong server — left immediately**\n"
                f"**Name:** {guild.name}\n"
                f"**ID:** `{guild.id}`\n"
                f"**Members:** {guild.member_count}"
            )
        except Exception:
            pass
        return

    # It's the correct server — welcome
    try:
        owner_user = client.get_user(_OWNER_ID) or await client.fetch_user(_OWNER_ID)
        dm = owner_user.dm_channel or await owner_user.create_dm()
        await dm.send(
            f"✅ **Bot is now in the home server**\n"
            f"**Name:** {guild.name} (`{guild.id}`)"
        )
    except Exception:
        pass


@client.event
async def on_guild_remove(guild: discord.Guild):
    """Called when the bot leaves or is kicked from a server."""
    print(f"[security] left/removed from guild: {guild.name} ({guild.id})")
    db.log_action(0, "guild_leave", f"{guild.name} ({guild.id})", actor_id=None)


# ══════════════════════════════════════════════════════════════════════════════
# TICKET COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="setup-tickets",
    description="🎟️ Post the ticket panel in #create-ticket (Admin/Owner only)",
)
async def setup_tickets(interaction: discord.Interaction):
    if not _is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Only admins and the owner can run this command.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # Find #create-ticket channel
    ch = discord.utils.find(
        lambda c: "create-ticket" in c.name and isinstance(c, discord.TextChannel),
        guild.channels,
    )
    if not ch:
        await interaction.followup.send(
            "❌ Couldn't find a **#create-ticket** channel. Create the Tickets & Support "
            "category first (the bot created it earlier).",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🎟️  Support & Reports",
        description=(
            "Need help or want to report something? Click a button below to open a private ticket.\n\n"
            "**Our staff will respond as soon as possible.** 💜\n\u200b"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="🎟️  Create a Ticket",
        value="Questions, concerns, appeals, or general help.",
        inline=True,
    )
    embed.add_field(
        name="🚨  Report Someone",
        value="Report a member for breaking rules.",
        inline=True,
    )
    embed.set_footer(text="Your ticket will be private — only you and staff can see it.")

    # Delete any existing panel messages from the bot in this channel
    try:
        async for msg in ch.history(limit=20):
            if msg.author == client.user:
                await msg.delete()
    except Exception:
        pass

    await ch.send(embed=embed, view=TicketPanelView())
    await interaction.followup.send(
        f"✅ Ticket panel posted in {ch.mention}!", ephemeral=True
    )


# ══════════════════════════════════════════════════════════════════════════════
# GENERATION COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

_VIDEO_MODEL_CHOICES = [
    # ── Google / Veo  (fixed 8 s · text only) ──────────────────────
    app_commands.Choice(name="Veo 3.1  [8s]",                         value="Veo 3.1"),
    app_commands.Choice(name="Veo 3.1 Fast  [8s]",                    value="Veo 3.1 Fast"),
    app_commands.Choice(name="Veo 3.1 Lite  [8s]",                    value="Veo 3.1 Lite"),
    # ── OpenAI / Sora  (text only) ─────────────────────────────────
    app_commands.Choice(name="Sora 2  [10s]",                         value="Sora 2"),
    app_commands.Choice(name="Sora 2 Pro  [10s]",                     value="Sora 2 Pro"),
    # ── Google Gemini  (text only) ─────────────────────────────────
    app_commands.Choice(name="Gemini Omni Flash  [5–15s]",            value="Gemini Omni Flash"),
    # ── Kuaishou / Kling  🖼️ supports image reference ───────────────
    app_commands.Choice(name="Kling 3.0  [5s or 10s]  🖼️",           value="Kling 3.0"),
    app_commands.Choice(name="Kling 3.0 Pro  [5s or 10s]  🖼️",       value="Kling 3.0 Pro"),
    app_commands.Choice(name="Kling O3  [5s or 10s]  🖼️",            value="Kling O3"),
    app_commands.Choice(name="Kling 2.6 Pro  [5s or 10s]  🖼️",       value="Kling 2.6 Pro"),
    app_commands.Choice(name="Kling 2.5 Turbo Pro  [5s or 10s]  🖼️", value="Kling 2.5 Turbo Pro"),
    app_commands.Choice(name="Kling 2.1 Pro  [5s or 10s]  🖼️",       value="Kling 2.1 Pro"),
    app_commands.Choice(name="Kling 2.1  [5s or 10s]  🖼️",           value="Kling 2.1"),
    # ── ByteDance / Seedance  🖼️ supports image reference ───────────
    app_commands.Choice(name="Seedance 2.0  [5–15s]  🖼️",            value="Seedance 2.0"),
    app_commands.Choice(name="Seedance 2.0 Fast  [5–10s]  🖼️",       value="Seedance 2.0 Fast"),
    app_commands.Choice(name="Seedance 2.0 Mini  [5–10s]  🖼️",       value="Seedance 2.0 Mini"),
    app_commands.Choice(name="Seedance 1.5 Pro  [5–15s · Start Frame]  🖼️", value="Seedance 1.5 Pro"),
    # ── MiniMax / Hailuo  🖼️ supports image reference ───────────────
    app_commands.Choice(name="Hailuo 2.3 Pro  [6–10s]  🖼️",           value="Hailuo 2.3 Pro"),
    app_commands.Choice(name="Hailuo 2.3 Fast Pro  [6–10s]  🖼️",      value="Hailuo 2.3 Fast Pro"),
    app_commands.Choice(name="Hailuo 2.3 Standard  [6–10s]  🖼️",      value="Hailuo 2.3 Standard"),
    app_commands.Choice(name="Hailuo 2.3 Fast Standard  [6–10s]  🖼️", value="Hailuo 2.3 Fast Standard"),
    # ── Alibaba / Wan  🖼️ supports image reference ──────────────────
    app_commands.Choice(name="Wan 2.6  [3–10s]  🖼️",                  value="Wan 2.6"),
    # ── Specialty ──────────────────────────────────────────────────
    app_commands.Choice(name="HappyHorse 1.0  [7s]",                   value="HappyHorse 1.0"),
    app_commands.Choice(name="Grok Imagine Video  [10s]",               value="Grok Imagine Video"),
    app_commands.Choice(name="Runway  [5–10s]  🖼️",                    value="Runway"),
]

_VIDEO_RESOLUTION_CHOICES = [
    app_commands.Choice(name="480p",  value="480p"),
    app_commands.Choice(name="720p",  value="720p"),
    app_commands.Choice(name="1080p", value="1080p"),
    app_commands.Choice(name="4K",    value="4K"),
]

_VIDEO_DURATION_CHOICES = [
    # 5 and 10 work for all models; 7 for HappyHorse; 8 for Veo;
    # 15 for Seedance Pro/2.0; others snapped to nearest valid.
    app_commands.Choice(name="5 sec",  value="5"),
    app_commands.Choice(name="7 sec  (HappyHorse only)",  value="7"),
    app_commands.Choice(name="8 sec  (Veo only)",         value="8"),
    app_commands.Choice(name="10 sec", value="10"),
    app_commands.Choice(name="15 sec  (Seedance / Gemini)", value="15"),
]

_VIDEO_ASPECT_CHOICES = [
    app_commands.Choice(name="16:9",  value="16:9"),
    app_commands.Choice(name="9:16",  value="9:16"),
    app_commands.Choice(name="21:9",  value="21:9"),   # Seedance only (View All)
    app_commands.Choice(name="1:1",   value="1:1"),
    app_commands.Choice(name="4:3",   value="4:3"),    # Seedance only
    app_commands.Choice(name="3:4",   value="3:4"),    # Seedance only
    app_commands.Choice(name="Auto",  value="Auto"),   # Kling only
]

_IMAGE_MODEL_CHOICES = [
    # ── Google (Nano Banana) ─────────────────────────────────────
    app_commands.Choice(name="Nano Banana 2 Lite",  value="Nano Banana 2 Lite"),
    app_commands.Choice(name="Nano Banana 2",       value="Nano Banana 2"),
    app_commands.Choice(name="Nano Banana Pro",     value="Nano Banana Pro"),
    # ── ByteDance (Seedream) ─────────────────────────────────────
    app_commands.Choice(name="Seedream 5.0 Pro",    value="Seedream 5.0 Pro"),
    app_commands.Choice(name="Seedream 5.0",        value="Seedream 5.0"),
    # ── OpenAI ───────────────────────────────────────────────────
    app_commands.Choice(name="GPT Image 2",         value="GPT Image 2"),
    # ── Kuaishou ─────────────────────────────────────────────────
    app_commands.Choice(name="Kling 3.0",           value="Kling 3.0"),
]

_IMAGE_ASPECT_CHOICES = [
    app_commands.Choice(name="1:1",  value="1:1"),
    app_commands.Choice(name="16:9", value="16:9"),
    app_commands.Choice(name="9:16", value="9:16"),
    app_commands.Choice(name="4:3",  value="4:3"),
    app_commands.Choice(name="3:4",  value="3:4"),
    app_commands.Choice(name="21:9", value="21:9"),
]


@client.tree.command(name="video", description="🎬 Generate an AI video (~3–5 min)")
@app_commands.describe(
    prompt="Describe the video you want",
    hidden_prompt="Hide the prompt from the output caption (default: off)",
    bypass_prefix="Wrap character names with bypass tags so filters don't block them (default: off)",
    remove_watermark="Skip the watermark on the output video (default: off)",
    model="AI model to use (🖼️ = supports image reference)",
    image="Optional reference image — only works with 🖼️ models (Kling, Seedance, Wan, Hailuo, Runway)",
    resolution="Output resolution (default: 720p)",
    duration="Clip length in seconds (default: model default)",
    aspect_ratio="Aspect ratio (default: 16:9)",
    audio="Include generated audio (default: off)",
)
@app_commands.choices(
    model=_VIDEO_MODEL_CHOICES,
    resolution=_VIDEO_RESOLUTION_CHOICES,
    duration=_VIDEO_DURATION_CHOICES,
    aspect_ratio=_VIDEO_ASPECT_CHOICES,
)
async def video_cmd(
    interaction: discord.Interaction,
    prompt: str,
    hidden_prompt: bool = False,
    bypass_prefix: bool = False,
    remove_watermark: bool = False,
    model: app_commands.Choice[str] = None,
    image: discord.Attachment = None,
    resolution: app_commands.Choice[str] = None,
    duration: app_commands.Choice[str] = None,
    aspect_ratio: app_commands.Choice[str] = None,
    audio: bool = False,
):
    if not await _check_guild(interaction):
        return

    # /video is restricted to admins, the owner, or members with an allowed server role
    if not _has_discord_role_access(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have the required server role to use `/video`.",
            ephemeral=True,
        )
        return

    db.ensure_user(interaction.user.id, str(interaction.user))
    blocked, reason = db.is_blocked(interaction.user.id)
    if blocked:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if not db.has_credits(interaction.user.id, db.COST_VIDEO):
        bal = db.get_credits(interaction.user.id)
        await interaction.response.send_message(
            f"❌ Not enough credits! You have **{bal:,}** but this costs **{db.COST_VIDEO:,}**.\n"
            f"Ask an admin to top you up.",
            ephemeral=True,
        )
        return

    # Only admin/owner may skip the watermark
    if remove_watermark and not _is_admin_or_owner(interaction.user.id):
        remove_watermark = False

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        return

    mention      = interaction.user.mention
    # Bypass: wrap character names so content filters don't block them.
    # display_prompt is what appears in the Discord caption (names in `backticks`).
    # gen_prompt is what's sent to the AI generator (names in 11ii…11ii tags).
    display_prompt = prompt
    gen_prompt     = prompt
    if bypass_prefix:
        gen_prompt     = _art.apply_bypass_prompt(prompt)
        display_prompt = _bypass_display_prompt(prompt)
    # hidden_prompt only controls visibility — the prompt sent to the generator is unchanged
    model_name   = model.value       if model       else "Gemini Omni Flash"
    res_val      = resolution.value  if resolution  else None
    dur_val      = int(duration.value) if duration  else None
    aspect_val   = aspect_ratio.value if aspect_ratio else None

    # Check if the chosen model supports image reference before downloading it.
    _img_unsupported = image is not None and model_name in _art.MODELS_WITHOUT_IMAGE_REF

    # Download reference image if provided (only for models that support it)
    image_ref_bytes = None
    image_ref_ext   = ".png"
    if image is not None and not _img_unsupported:
        try:
            async with aiohttp.ClientSession() as _sess:
                async with _sess.get(image.url) as _resp:
                    image_ref_bytes = await _resp.read()
            ctype = image.content_type or ""
            if "jpeg" in ctype or "jpg" in ctype:
                image_ref_ext = ".jpg"
            elif "webp" in ctype:
                image_ref_ext = ".webp"
            elif "gif" in ctype:
                image_ref_ext = ".gif"
            else:
                ext = "." + (image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "png")
                image_ref_ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") else ".png"
        except Exception as _img_err:
            print(f"[video] ⚠️ Could not download reference image: {_img_err}")

    db.deduct_credits(interaction.user.id, db.COST_VIDEO)
    bal_after = db.get_credits(interaction.user.id)
    bal_str   = "∞" if bal_after is None else f"{bal_after:,}"

    settings_parts = [model_name]
    if aspect_val: settings_parts.append(aspect_val)
    if res_val:    settings_parts.append(res_val)
    if dur_val:    settings_parts.append(f"{dur_val}s")
    if audio:      settings_parts.append("audio")
    model_label = " · ".join(settings_parts)

    global _GEN_DEPTH
    _GEN_DEPTH += 1
    queue_pos = _GEN_DEPTH
    _in_queue = queue_pos > _MAX_CONCURRENT
    _queue_num = queue_pos - _MAX_CONCURRENT

    # ── Progress tracking ──────────────────────────────────────────────────────
    async def _update_video(content: str) -> None:
        try:
            await interaction.edit_original_response(content=content)
        except Exception:
            pass

    tracker = ProgressTracker(
        f"🎬 {mention} · {model_label}", "" if hidden_prompt else display_prompt, _update_video
    )

    async def _progress(msg: str) -> None:
        await tracker.step(_mask(msg))

    async def _screenshot(label: str, img_bytes: bytes) -> None:
        await _dm_owner(client, label, img_bytes)

    if _in_queue:
        gen_msg = (
            f"⏳ {mention} **Queue #{_queue_num}** — waiting for a slot…\n"
            f"> 🤖 {model_label}"
        )
    else:
        gen_msg = f"⏳ {mention} **Generating…**\n> 🤖 {model_label}"

    await interaction.edit_original_response(content=gen_msg)

    _t_start = time.monotonic()

    try:
        async with _GEN_SEM:
            if _in_queue:
                await interaction.edit_original_response(
                    content=f"⏳ {mention} **Generating…**\n> 🤖 {model_label}"
                )

            # ── Auto-retry once on transient failures ───────────────────────
            _last_err: Exception | None = None
            for _attempt in range(2):
                try:
                    video_result = await _art.generate_artlist_video(
                        prompt=gen_prompt,
                        model=model_name,
                        resolution=res_val,
                        duration=dur_val,
                        aspect_ratio=aspect_val,
                        audio=audio,
                        progress_cb=_progress,
                        screenshot_cb=_screenshot,
                        image_ref_bytes=image_ref_bytes,
                        image_ref_ext=image_ref_ext,
                        skip_watermark=remove_watermark,
                    )
                    _last_err = None
                    break
                except _art.CopyrightError:
                    raise  # never retry copyright blocks
                except Exception as _e:
                    _last_err = _e
                    if _attempt == 0:
                        print(f"[video] ⚠️ attempt 1 failed ({_e}), retrying…")
                        await tracker.step("⚠️ Hit a snag — retrying…")
                        await asyncio.sleep(6)
            if _last_err is not None:
                raise _last_err

        _dur_s = int(time.monotonic() - _t_start)
        db.log_action(
            interaction.user.id, "video",
            f"model={model_name} res={res_val} dur={dur_val} aspect={aspect_val} "
            f"audio={audio} time={_dur_s}s",
        )
        title     = db.get_title(interaction.user.id)
        title_str = f"\n> 🏷️ {title}" if title else ""

        caption  = f"{mention} Your {model_name} video is ready! 🎬\n"
        if not hidden_prompt:
            caption += f"📝 **Prompt:** {display_prompt[:180]}{title_str}\n"
        elif title_str:
            caption += f"{title_str}\n"
        if aspect_val: caption += f"📐 **Aspect Ratio:** **{aspect_val}**\n"
        if res_val:    caption += f"🖥️ **Resolution:** **{res_val}**\n"
        if dur_val:    caption += f"⏱️ **Duration:** **{dur_val}s**\n"
        caption += f"💳 **Credits:** {bal_str}\n"
        caption += f"🔨 Made by NDX and LOL"

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        # ── Normalise to bytes ─────────────────────────────────────────────
        if isinstance(video_result, str):
            msg = await interaction.followup.send(content=f"{mention} ⬇️ Downloading video…")
            try:
                async with aiohttp.ClientSession() as _dl:
                    async with _dl.get(video_result, timeout=aiohttp.ClientTimeout(total=180)) as _r:
                        video_bytes_raw = await _r.read() if _r.status == 200 else None
            except Exception as _dl_e:
                print(f"[video] ⚠️ download failed: {_dl_e}")
                video_bytes_raw = None
            if video_bytes_raw is None:
                await msg.edit(content=caption)
                return
        else:
            video_bytes_raw = video_result
            msg = await interaction.followup.send(content=f"{mention} 🎬 Preparing video…")

        # ── Prepend intro ──────────────────────────────────────────────────
        await msg.edit(content=f"{mention} 🎬 Adding intro…")
        final_bytes = await _prepend_intro(video_bytes_raw)

        # ── Upload to Streamable ──────────────────────────────────────────
        await msg.edit(content=f"{mention} 📤 Uploading to Streamable…")
        stream_url, dl_bytes = await _upload_streamable(final_bytes, "video.mp4")

        # ── Send final result ──────────────────────────────────────────────
        send_bytes = dl_bytes if dl_bytes else final_bytes
        caption_final = caption
        if not dl_bytes and stream_url:
            caption_final += f"\n📽️ **Video:** {stream_url}"

        video_file = None
        mb = len(send_bytes) / 1024 / 1024
        if mb <= _DISCORD_MAX_ATTACH_MB:
            video_file = discord.File(io.BytesIO(send_bytes), filename="video.mp4")

        try:
            if video_file:
                await msg.edit(content=caption_final, attachments=[video_file])
            else:
                await msg.edit(content=caption_final)
        except Exception:
            await interaction.followup.send(content=caption_final)

    except _art.CopyrightError:
        print(f"[video] 🚫 copyright block")
        db.refund_credits(interaction.user.id, db.COST_VIDEO)
        db.log_action(interaction.user.id, "gen_fail", f"copyright model={model_name}")
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"oops shit got copyrighted {mention} 🚫 try a different prompt\n"
                f"> 💳 No credits taken — balance: **{refund_str}**"
            ),
        )
    except Exception as e:
        _dur_s = int(time.monotonic() - _t_start)
        err_short = str(e)[:120]
        print(f"[video] ❌ {e}")
        db.refund_credits(interaction.user.id, db.COST_VIDEO)
        db.log_action(
            interaction.user.id, "gen_fail",
            f"model={model_name} time={_dur_s}s err={err_short}",
        )
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"lil bro shit did not work {mention} 💀\n"
                f"> 💳 Credits refunded — balance: **{refund_str}**"
            ),
        )
    finally:
        _GEN_DEPTH -= 1


# ── /omni ──────────────────────────────────────────────────────────────────────

@client.tree.command(name="omni", description="🌐 Generate a Google Omni video via Synthesia (~3–5 min)")
@app_commands.describe(
    prompt="Describe the video you want",
    hidden_prompt="Hide the prompt from the output caption (default: off)",
)
async def omni_cmd(
    interaction: discord.Interaction,
    prompt: str,
    hidden_prompt: bool = False,
):
    if not await _check_guild(interaction):
        return

    if not _has_discord_role_access(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have the required server role to use `/omni`.",
            ephemeral=True,
        )
        return

    db.ensure_user(interaction.user.id, str(interaction.user))
    blocked, reason = db.is_blocked(interaction.user.id)
    if blocked:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if not db.has_credits(interaction.user.id, db.COST_VIDEO):
        bal = db.get_credits(interaction.user.id)
        await interaction.response.send_message(
            f"❌ Not enough credits! You have **{bal:,}** but this costs **{db.COST_VIDEO:,}**.\n"
            f"Ask an admin to top you up.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        return

    mention   = interaction.user.mention
    _t_start  = time.monotonic()

    db.deduct_credits(interaction.user.id, db.COST_VIDEO)
    bal_after = db.get_credits(interaction.user.id)
    bal_str   = "∞" if bal_after is None else f"{bal_after:,}"

    global _GEN_DEPTH
    _GEN_DEPTH += 1
    queue_pos  = _GEN_DEPTH
    _in_queue  = queue_pos > _MAX_CONCURRENT
    _queue_num = queue_pos - _MAX_CONCURRENT

    if _in_queue:
        gen_msg = f"⏳ {mention} **Queue #{_queue_num}** — waiting for a slot…\n> 🌐 Google Omni"
    else:
        gen_msg = f"⏳ {mention} **Generating…**\n> 🌐 Google Omni"

    await interaction.edit_original_response(content=gen_msg)

    async def _update_omni(msg: str) -> None:
        try:
            await interaction.edit_original_response(content=msg)
        except Exception:
            pass

    tracker = ProgressTracker(
        f"🌐 {mention} · Google Omni", "" if hidden_prompt else prompt, _update_omni
    )

    async def _progress(msg: str) -> None:
        print(f"[omni] {msg}")
        await tracker.step(msg)

    async def _screenshot(label: str, img_bytes: bytes) -> None:
        await _dm_owner(client, label, img_bytes)

    try:
        async with _GEN_SEM:
            if _in_queue:
                await interaction.edit_original_response(
                    content=f"⏳ {mention} **Generating…**\n> 🌐 Google Omni"
                )

            # Try fast API path first; fall back to Playwright if it fails
            if _syn_api is not None:
                try:
                    video_result = await _syn_api.generate_synthesia_video(
                        prompt=prompt,
                        progress_cb=_progress,
                        screenshot_cb=_screenshot,
                    )
                except Exception as _api_e:
                    print(f"[omni] API path failed ({_api_e}), falling back to Playwright…")
                    if _syn is None:
                        raise RuntimeError(f"API failed and Playwright unavailable: {_api_e}") from _api_e
                    video_result = await _syn.generate_synthesia_video(
                        prompt=prompt,
                        progress_cb=_progress,
                        screenshot_cb=_screenshot,
                    )
            else:
                if _syn is None:
                    raise RuntimeError("No Synthesia backend available")
                video_result = await _syn.generate_synthesia_video(
                    prompt=prompt,
                    progress_cb=_progress,
                    screenshot_cb=_screenshot,
                )

        _dur_s = int(time.monotonic() - _t_start)
        db.log_action(
            interaction.user.id, "omni",
            f"time={_dur_s}s",
        )
        title     = db.get_title(interaction.user.id)
        title_str = f"\n> 🏷️ {title}" if title else ""

        caption  = f"{mention} Your Google Omni video is ready! 🌐\n"
        if not hidden_prompt:
            caption += f"📝 **Prompt:** {prompt[:180]}{title_str}\n"
        elif title_str:
            caption += f"{title_str}\n"
        caption += f"💳 **Credits:** {bal_str}\n"
        caption += f"🔨 Made by NDX and LOL"

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        # ── Normalise to bytes ─────────────────────────────────────────────
        if isinstance(video_result, str):
            msg = await interaction.followup.send(content=f"{mention} ⬇️ Downloading video…")
            try:
                async with aiohttp.ClientSession() as _dl:
                    async with _dl.get(video_result, timeout=aiohttp.ClientTimeout(total=180)) as _r:
                        video_bytes_raw = await _r.read() if _r.status == 200 else None
            except Exception as _dl_e:
                print(f"[omni] ⚠️ download failed: {_dl_e}")
                video_bytes_raw = None
            if video_bytes_raw is None:
                await msg.edit(content=caption)
                return
        else:
            video_bytes_raw = video_result
            msg = await interaction.followup.send(content=f"{mention} 🎬 Preparing video…")

        # ── Prepend intro ──────────────────────────────────────────────────
        await msg.edit(content=f"{mention} 🎬 Adding intro…")
        final_bytes = await _prepend_intro(video_bytes_raw)

        # ── Upload to Streamable ──────────────────────────────────────────
        await msg.edit(content=f"{mention} 📤 Uploading to Streamable…")
        stream_url, dl_bytes = await _upload_streamable(final_bytes, "omni.mp4")

        # ── Send final result ──────────────────────────────────────────────
        send_bytes = dl_bytes if dl_bytes else final_bytes
        caption_final = caption
        if not dl_bytes and stream_url:
            caption_final += f"\n📽️ **Video:** {stream_url}"

        video_file = None
        mb = len(send_bytes) / 1024 / 1024
        if mb <= _DISCORD_MAX_ATTACH_MB:
            video_file = discord.File(io.BytesIO(send_bytes), filename="omni.mp4")

        try:
            if video_file:
                await msg.edit(content=caption_final, attachments=[video_file])
            else:
                await msg.edit(content=caption_final)
        except Exception:
            await interaction.followup.send(content=caption_final)

        # DM owner the raw video
        asyncio.create_task(_dm_owner_file(
            client,
            f"omni · {interaction.user} · {prompt[:60]}",
            final_bytes,
            "omni.mp4",
        ))

    except Exception as e:
        _dur_s = int(time.monotonic() - _t_start)
        err_short = str(e)[:120]
        print(f"[omni] ❌ {e}")
        db.refund_credits(interaction.user.id, db.COST_VIDEO)
        db.log_action(
            interaction.user.id, "omni_fail",
            f"time={_dur_s}s err={err_short}",
        )
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"lil bro shit did not work {mention} 💀\n"
                f"> 💳 Credits refunded — balance: **{refund_str}**"
            ),
        )
    finally:
        _GEN_DEPTH -= 1


# ── /sd2 ───────────────────────────────────────────────────────────────────────

_SD2_MODEL_CHOICES = [
    app_commands.Choice(name="Seedance 2.0  [standard]", value="doubao-seedance-2-0"),
    app_commands.Choice(name="Seedance 2.0 Fast",         value="doubao-seedance-2-0-fast"),
]

_SD2_RATIO_CHOICES = [
    app_commands.Choice(name="16:9",  value="16:9"),
    app_commands.Choice(name="9:16",  value="9:16"),
    app_commands.Choice(name="1:1",   value="1:1"),
    app_commands.Choice(name="4:3",   value="4:3"),
    app_commands.Choice(name="3:4",   value="3:4"),
    app_commands.Choice(name="21:9",  value="21:9"),
]

_SD2_DURATION_CHOICES = [
    app_commands.Choice(name="5 sec",  value="5"),
    app_commands.Choice(name="10 sec", value="10"),
    app_commands.Choice(name="15 sec", value="15"),
    app_commands.Choice(name="20 sec", value="20"),
]


@client.tree.command(name="sd2", description="🌱 Generate a Seedance 2.0 video via CometAPI (~2–5 min)")
@app_commands.describe(
    prompt="Describe the video you want",
    model="AI model to use (default: Seedance 2.0 standard)",
    ratio="Aspect ratio (default: 16:9)",
    duration="Clip length in seconds (default: 5s)",
    audio="Include generated audio (default: on)",
    image="Paste or upload an image to use as the starting frame (image-to-video)",
    image_url="Image URL to use as the starting frame (alternative to uploading)",
    hidden_prompt="Hide the prompt from the output caption (default: off)",
    bypass_prompt="Replace IP names/techniques with visual descriptions to bypass filters (default: on)",
)
@app_commands.choices(
    model=_SD2_MODEL_CHOICES,
    ratio=_SD2_RATIO_CHOICES,
    duration=_SD2_DURATION_CHOICES,
)
async def sd2_cmd(
    interaction: discord.Interaction,
    prompt: str,
    model: app_commands.Choice[str] = None,
    ratio: app_commands.Choice[str] = None,
    duration: app_commands.Choice[str] = None,
    audio: bool = True,
    image: discord.Attachment = None,
    image_url: str = None,
    hidden_prompt: bool = False,
    bypass_prompt: bool = True,
):
    if not await _check_guild(interaction):
        return

    if not _has_discord_role_access(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have the required server role to use `/sd2`.",
            ephemeral=True,
        )
        return

    db.ensure_user(interaction.user.id, str(interaction.user))
    blocked, reason = db.is_blocked(interaction.user.id)
    if blocked:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if not db.has_credits(interaction.user.id, db.COST_VIDEO):
        bal = db.get_credits(interaction.user.id)
        await interaction.response.send_message(
            f"❌ Not enough credits! You have **{bal:,}** but this costs **{db.COST_VIDEO:,}**.\n"
            f"Ask an admin to top you up.",
            ephemeral=True,
        )
        return

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        return

    mention       = interaction.user.mention
    _t_start      = time.monotonic()
    model_name    = model.value         if model    else "doubao-seedance-2-0"
    ratio_val     = ratio.value         if ratio    else "16:9"
    dur_val       = int(duration.value) if duration else 5
    model_display = "Seedance 2.0 Fast" if "fast" in model_name else "Seedance 2.0"

    settings_parts = [model_display, ratio_val, f"{dur_val}s"]
    if audio:
        settings_parts.append("audio")
    model_label = " · ".join(settings_parts)

    db.deduct_credits(interaction.user.id, db.COST_VIDEO)
    bal_after = db.get_credits(interaction.user.id)
    bal_str   = "∞" if bal_after is None else f"{bal_after:,}"

    await interaction.edit_original_response(
        content=f"⏳ {mention} **Generating…**\n> 🌱 {model_label}"
    )

    async def _update_sd2(content: str) -> None:
        try:
            await interaction.edit_original_response(content=content)
        except Exception:
            pass

    tracker = ProgressTracker(
        f"🌱 {mention} · {model_label}",
        "" if hidden_prompt else prompt,
        _update_sd2,
    )

    async def _progress(msg: str) -> None:
        await tracker.step(_mask(msg))

    # Attachment (pasted image) takes priority over typed URL
    ref_image_url = (image.url if image else None) or image_url

    # Bypass: transform the generation prompt but keep original for display
    gen_prompt = _bypass.apply_bypass_prompt(prompt) if bypass_prompt else prompt

    try:
        video_bytes_raw, _ = await _sd2.generate_sd2_video(
            prompt=gen_prompt,
            model=model_name,
            size=ratio_val,
            seconds=dur_val,
            audio=audio,
            image_url=ref_image_url,
            progress_cb=_progress,
        )

        _dur_s = int(time.monotonic() - _t_start)
        db.log_action(
            interaction.user.id, "sd2",
            f"model={model_name} ratio={ratio_val} dur={dur_val} "
            f"audio={audio} time={_dur_s}s",
        )
        title     = db.get_title(interaction.user.id)
        title_str = f"\n> 🏷️ {title}" if title else ""

        caption  = f"{mention} Your {model_display} video is ready! 🌱\n"
        if not hidden_prompt:
            caption += f"📝 **Prompt:** {prompt[:180]}{title_str}\n"
        elif title_str:
            caption += f"{title_str}\n"
        caption += f"📐 **Ratio:** **{ratio_val}** · ⏱️ **{dur_val}s**\n"
        caption += f"💳 **Credits:** {bal_str}\n"
        caption += f"🔨 Made by NDX and LOL"

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        # ── Prepend intro ──────────────────────────────────────────────────
        msg = await interaction.followup.send(content=f"{mention} 🎬 Adding intro…")
        final_bytes = await _prepend_intro(video_bytes_raw)

        # ── Upload to Streamable ──────────────────────────────────────────
        await msg.edit(content=f"{mention} 📤 Uploading to Streamable…")
        stream_url, dl_bytes = await _upload_streamable(final_bytes, "sd2.mp4")

        # ── Send final result ──────────────────────────────────────────────
        send_bytes = dl_bytes if dl_bytes else final_bytes
        caption_final = caption
        if not dl_bytes and stream_url:
            caption_final += f"\n📽️ **Video:** {stream_url}"

        video_file = None
        mb = len(send_bytes) / 1024 / 1024
        if mb <= _DISCORD_MAX_ATTACH_MB:
            video_file = discord.File(io.BytesIO(send_bytes), filename="sd2.mp4")

        try:
            if video_file:
                await msg.edit(content=caption_final, attachments=[video_file])
            else:
                await msg.edit(content=caption_final)
        except discord.HTTPException:
            await msg.edit(content=caption_final)

    except Exception as e:
        _dur_s    = int(time.monotonic() - _t_start)
        err_short = str(e)[:120]
        print(f"[sd2] ❌ {e}")
        db.refund_credits(interaction.user.id, db.COST_VIDEO)
        db.log_action(
            interaction.user.id, "sd2_fail",
            f"model={model_name} time={_dur_s}s err={err_short}",
        )
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"lil bro shit did not work {mention} 💀\n"
                f"> 💳 Credits refunded — balance: **{refund_str}**"
            ),
        )


@client.tree.command(name="image", description="🖼️ Generate an AI image (~1–3 min)")
@app_commands.describe(
    prompt="Describe the image you want",
    model="AI model to use",
    aspect_ratio="Aspect ratio (default: model default)",
    remove_watermark="Skip the watermark on the output image (default: off)",
)
@app_commands.choices(
    model=_IMAGE_MODEL_CHOICES,
    aspect_ratio=_IMAGE_ASPECT_CHOICES,
)
async def image_cmd(
    interaction: discord.Interaction,
    prompt: str,
    model: app_commands.Choice[str] = None,
    aspect_ratio: app_commands.Choice[str] = None,
    remove_watermark: bool = False,
):
    if not await _check_guild(interaction):
        return

    if not _has_discord_role_access(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have the required server role to use `/image`.",
            ephemeral=True,
        )
        return

    db.ensure_user(interaction.user.id, str(interaction.user))
    blocked, reason = db.is_blocked(interaction.user.id)
    if blocked:
        await interaction.response.send_message(reason, ephemeral=True)
        return

    if not db.has_credits(interaction.user.id, db.COST_IMAGE):
        bal = db.get_credits(interaction.user.id)
        await interaction.response.send_message(
            f"❌ Not enough credits! You have **{bal:,}** but this costs **{db.COST_IMAGE:,}**.\n"
            f"Ask an admin to top you up.",
            ephemeral=True,
        )
        return

    # Only admin/owner may skip the watermark
    if remove_watermark and not _is_admin_or_owner(interaction.user.id):
        remove_watermark = False

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        return

    mention      = interaction.user.mention
    model_name   = model.value        if model        else "FLUX 1.1 Pro"
    aspect_val   = aspect_ratio.value if aspect_ratio else None
    model_label  = model_name + (f" · {aspect_val}" if aspect_val else "")

    db.deduct_credits(interaction.user.id, db.COST_IMAGE)
    bal_after = db.get_credits(interaction.user.id)
    bal_str   = "∞" if bal_after is None else f"{bal_after:,}"

    global _GEN_DEPTH
    _GEN_DEPTH += 1
    queue_pos = _GEN_DEPTH
    _in_queue  = queue_pos > _MAX_CONCURRENT
    _queue_num = queue_pos - _MAX_CONCURRENT

    # Simple generating message — no GIFs, no live updates
    if _in_queue:
        gen_msg = (
            f"⏳ {mention} **Queue #{_queue_num}** — waiting for a slot…\n"
            f"> 🤖 {model_label}"
        )
    else:
        gen_msg = f"⏳ {mention} **Generating…**\n> 🤖 {model_label}"

    await interaction.edit_original_response(content=gen_msg)

    async def _screenshot(label: str, img_bytes: bytes) -> None:
        await _dm_owner(client, label, img_bytes)

    try:
        async with _GEN_SEM:
            if _in_queue:
                await interaction.edit_original_response(
                    content=f"⏳ {mention} **Generating…**\n> 🤖 {model_label}"
                )
            image_bytes = await _art.generate_artlist_image(
                prompt=prompt,
                model=model_name,
                aspect_ratio=aspect_val,
                progress_cb=None,
                screenshot_cb=_screenshot,
            )

        # Apply watermark before sending (skip if remove_watermark is set)
        if not remove_watermark:
            image_bytes = _apply_watermark(image_bytes)
        ext, mime = "jpg", "image/jpeg"

        db.log_action(interaction.user.id, "image", f"model={model_name} aspect={aspect_val} size={len(image_bytes)//1024}KB")
        title     = db.get_title(interaction.user.id)
        title_str = f"\n> 🏷️ {title}" if title else ""

        caption  = f"{mention} Your image is ready! 🖼️\n"
        caption += f"📝 **Prompt:** *{prompt[:120]}*{title_str}\n"
        caption += f"🤖 **Model:** **{model_name}**\n"
        if aspect_val: caption += f"📐 **Aspect Ratio:** **{aspect_val}**\n"
        caption += f"💳 **Credits:** {bal_str}\n"
        caption += f"🔨 Made by NDX and LOL also wwtv"

        # Delete the "generating" message, then post a fresh message with the image
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        mb = len(image_bytes) / 1024 / 1024
        msg = await interaction.followup.send(content=f"{caption}\n📤 Uploading…")
        if mb <= _DISCORD_MAX_ATTACH_MB:
            # Send directly as a Discord attachment in the channel
            img_file = discord.File(io.BytesIO(image_bytes), filename=f"image.{ext}")
            try:
                await msg.edit(content=caption, attachments=[img_file])
            except Exception:
                img_file2 = discord.File(io.BytesIO(image_bytes), filename=f"image.{ext}")
                await interaction.followup.send(content=caption, file=img_file2)
        else:
            # File too large for Discord — try catbox as fallback
            img_url = await _upload_catbox(image_bytes, f"image.{ext}")
            if img_url:
                await msg.edit(content=f"{caption}\n🖼️ **Image:** {img_url}")
            else:
                await msg.edit(content=f"{caption}\n⚠️ Image is {mb:.1f} MB — too large to attach.")

        # DM owner the raw file (owner-only download)
        asyncio.create_task(_dm_owner_file(
            client,
            f"image · {interaction.user} · {model_name}",
            image_bytes,
            f"image.{ext}",
        ))

    except _art.CopyrightError:
        print(f"[image] 🚫 copyright block")
        db.refund_credits(interaction.user.id, db.COST_IMAGE)
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"that bitch got copyrighted {mention} 🚫\n"
                f"> 💳 Credits refunded — balance: **{refund_str}**"
            ),
        )
    except Exception as e:
        print(f"[image] ❌ {e}")
        db.refund_credits(interaction.user.id, db.COST_IMAGE)
        refund_bal = db.get_credits(interaction.user.id)
        refund_str = "∞" if refund_bal is None else f"{refund_bal:,}"
        await interaction.edit_original_response(
            content=(
                f"lil bro shit did not work {mention} 💀\n"
                f"> 💳 Credits refunded — balance: **{refund_str}**"
            ),
        )
    finally:
        _GEN_DEPTH -= 1


# ══════════════════════════════════════════════════════════════════════════════
# CREDIT COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="credits", description="💳 Check your credit balance (or another user's)")
@app_commands.describe(user="User to check (leave blank for yourself)")
async def credits_cmd(
    interaction: discord.Interaction,
    user: discord.User | None = None,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    target = user or interaction.user
    db.ensure_user(target.id, str(target))

    u     = db.get_user(target.id)
    role  = u["role"]
    cred  = "∞" if role == db.ROLE_OWNER else f"{u['credits']:,}"
    title = u.get("title") or ""

    lines = [
        f"{'👤' if target.id == interaction.user.id else '🔍'} **{target.display_name}**",
    ]
    if title:
        lines.append(f"> 🏷️ {title}")
    lines.append(f"> {_ROLE_DISPLAY.get(role, role)}")
    lines.append(f"> 💳 **{cred}** credits")
    lines.append(f"> 🖼️ Image costs **{db.COST_IMAGE:,}** · 🎬 Video costs **{db.COST_VIDEO:,}**")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@client.tree.command(name="addcredits", description="💳 Add credits to a user (Admin/Owner)")
@app_commands.describe(user="Target user", amount="Credits to add")
async def addcredits_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    amount: int,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    new_bal = db.add_credits(user.id, amount)
    bal_str = "∞" if new_bal is None else f"{new_bal:,}"
    db.log_action(user.id, "credits_add", f"+{amount}", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ Added **{amount:,}** credits to {user.mention}.\n"
        f"> 💳 New balance: **{bal_str}**",
        ephemeral=True,
    )


@client.tree.command(name="removecredits", description="💳 Remove credits from a user (Admin/Owner)")
@app_commands.describe(user="Target user", amount="Credits to remove")
async def removecredits_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    amount: int,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    new_bal = db.remove_credits(user.id, amount)
    db.log_action(user.id, "credits_remove", f"-{amount}", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ Removed **{amount:,}** credits from {user.mention}.\n"
        f"> 💳 New balance: **{new_bal:,}**",
        ephemeral=True,
    )


@client.tree.command(name="setcredits", description="💳 Set a user's exact credit balance (Admin/Owner)")
@app_commands.describe(user="Target user", amount="New credit amount")
async def setcredits_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    amount: int,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return
    if amount < 0:
        await interaction.response.send_message("❌ Amount cannot be negative.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.set_credits(user.id, amount)
    db.log_action(user.id, "credits_set", f"={amount}", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ Set {user.mention}'s credits to **{amount:,}**.",
        ephemeral=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROLE COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

_ROLE_CHOICES = [
    app_commands.Choice(name="⚡ Beta  (90,000 credits)",  value="beta"),
    app_commands.Choice(name="🛡️ Admin (150,000 credits)", value="admin"),
    app_commands.Choice(name="👤 User  (1,500 credits)",   value="user"),
]


@client.tree.command(name="giverole", description="🎖️ Assign a bot role to a user (Owner only)")
@app_commands.describe(user="Target user", role="Role to assign")
@app_commands.choices(role=_ROLE_CHOICES)
async def giverole_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    role: app_commands.Choice[str],
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    # Only owner can set admin; admin can set beta/user
    actor_role = db.get_role(interaction.user.id)
    if actor_role == db.ROLE_OWNER:
        pass  # can set anything
    elif actor_role == db.ROLE_ADMIN and role.value in (db.ROLE_BETA, db.ROLE_USER):
        pass
    else:
        await interaction.response.send_message(
            "❌ You don't have permission to assign that role.", ephemeral=True
        )
        return

    if user.id == _OWNER_ID:
        await interaction.response.send_message("❌ Cannot change the owner's role.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.set_role(user.id, role.value)
    db.log_action(user.id, "role_set", role.value, actor_id=interaction.user.id)

    credits_given = db.ROLE_CREDITS.get(role.value, 1500) or 0
    display       = _ROLE_DISPLAY.get(role.value, role.value)

    await interaction.response.send_message(
        f"✅ {user.mention} is now **{display}**.\n"
        f"> 💳 Credits reset to **{credits_given:,}**",
        ephemeral=True,
    )


@client.tree.command(name="takerole", description="🎖️ Reset a user back to regular User role (Owner only)")
@app_commands.describe(user="Target user")
async def takerole_cmd(
    interaction: discord.Interaction,
    user: discord.User,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    if user.id == _OWNER_ID:
        await interaction.response.send_message("❌ Cannot change the owner's role.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.set_role(user.id, db.ROLE_USER)
    db.log_action(user.id, "role_remove", "→ user", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ {user.mention} has been reset to **👤 User**.\n"
        f"> 💳 Credits reset to **{db.ROLE_CREDITS[db.ROLE_USER]:,}**",
        ephemeral=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# BAN / TIMEOUT COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="botban", description="🚫 Ban a user from using the bot (Admin/Owner)")
@app_commands.describe(user="User to ban", reason="Reason for the ban")
async def botban_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    reason: str = "No reason provided.",
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return
    if user.id == _OWNER_ID:
        await interaction.response.send_message("❌ Cannot ban the owner.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.ban_user(user.id, reason)
    db.log_action(user.id, "bot_ban", reason, actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"🚫 **{user}** has been banned from the bot.\n> {reason}",
        ephemeral=True,
    )


@client.tree.command(name="botunban", description="✅ Unban a user from the bot (Admin/Owner)")
@app_commands.describe(user="User to unban")
async def botunban_cmd(interaction: discord.Interaction, user: discord.User):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.unban_user(user.id)
    db.log_action(user.id, "bot_unban", "", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ **{user}** has been unbanned from the bot.",
        ephemeral=True,
    )


@client.tree.command(name="bottimeout", description="⏳ Temporarily block a user from the bot (Admin/Owner)")
@app_commands.describe(
    user="User to timeout",
    minutes="Duration in minutes",
    reason="Reason for the timeout",
)
async def bottimeout_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    minutes: int,
    reason: str = "No reason provided.",
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return
    if user.id == _OWNER_ID:
        await interaction.response.send_message("❌ Cannot timeout the owner.", ephemeral=True)
        return
    if minutes <= 0:
        await interaction.response.send_message("❌ Duration must be positive.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    until = db.timeout_user(user.id, minutes)
    db.log_action(user.id, "bot_timeout", f"{minutes}m: {reason}", actor_id=interaction.user.id)

    until_ts = int(until)
    await interaction.response.send_message(
        f"⏳ **{user}** timed out for **{minutes} minute(s)**.\n"
        f"> Expires <t:{until_ts}:R>\n"
        f"> {reason}",
        ephemeral=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TITLE COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

_TITLE_CHOICES = [
    app_commands.Choice(name=v, value=k)
    for k, v in db.PRESET_TITLES.items()
]


@client.tree.command(name="settitle", description="🏷️ Assign a title to a user (Admin/Owner)")
@app_commands.describe(
    user="Target user",
    title="Title to assign (or 'none' to remove)",
)
@app_commands.choices(title=_TITLE_CHOICES)
async def settitle_cmd(
    interaction: discord.Interaction,
    user: discord.User,
    title: app_commands.Choice[str],
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    display = db.PRESET_TITLES.get(title.value, title.value)
    db.set_title(user.id, display)
    db.log_action(user.id, "title_set", display, actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ {user.mention} has been given the title **{display}**!",
        ephemeral=True,
    )


@client.tree.command(name="removetitle", description="🏷️ Remove a user's title (Admin/Owner)")
@app_commands.describe(user="Target user")
async def removetitle_cmd(interaction: discord.Interaction, user: discord.User):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    db.set_title(user.id, None)
    db.log_action(user.id, "title_remove", "", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ Removed {user.mention}'s title.",
        ephemeral=True,
    )


@client.tree.command(name="bypassprefix", description="🔖 Toggle the ii11goku11ii name prefix for a user (Admin/Owner)")
@app_commands.describe(user="Target user to toggle the bypass prefix for")
async def bypassprefix_cmd(interaction: discord.Interaction, user: discord.User):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    db.ensure_user(user.id, str(user))
    current = db.get_bypass_prefix(user.id)
    new_state = not current
    db.set_bypass_prefix(user.id, new_state)
    db.log_action(user.id, "bypass_prefix", f"enabled={new_state}", actor_id=interaction.user.id)

    status = "✅ enabled" if new_state else "❌ disabled"
    await interaction.response.send_message(
        f"{status} — {user.mention}'s name will {'now' if new_state else 'no longer'} appear as "
        f"**ii11goku11ii** in video messages.",
        ephemeral=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PROFILE / INFO COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="profile", description="👤 View a user's bot profile")
@app_commands.describe(user="User to look up (leave blank for yourself)")
async def profile_cmd(
    interaction: discord.Interaction,
    user: discord.User | None = None,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    target = user or interaction.user
    db.ensure_user(target.id, str(target))

    u     = db.get_user(target.id)
    embed = _profile_embed(target, u)
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="botstats", description="📊 Bot usage statistics (Owner only)")
async def botstats_cmd(interaction: discord.Interaction):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return

    s = db.get_stats()
    embed = discord.Embed(title="📊 Bot Statistics", color=0xFFD700)
    embed.add_field(name="👤 Total Users",  value=str(s["total"]),  inline=True)
    embed.add_field(name="⚡ Betas",        value=str(s["betas"]),  inline=True)
    embed.add_field(name="🛡️ Admins",       value=str(s["admins"]), inline=True)
    embed.add_field(name="🚫 Banned",       value=str(s["banned"]), inline=True)
    embed.add_field(name="🖼️ Images Made",  value=str(s["images"]), inline=True)
    embed.add_field(name="🎬 Videos Made",  value=str(s["videos"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="botlogs", description="📋 Recent audit log (Admin/Owner)")
@app_commands.describe(user="Filter by user (optional)")
async def botlogs_cmd(
    interaction: discord.Interaction,
    user: discord.User | None = None,
):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    logs = db.get_logs(user_id=user.id if user else None, limit=15)
    if not logs:
        await interaction.response.send_message("No logs found.", ephemeral=True)
        return

    lines = []
    for lg in logs:
        ts  = int(lg["timestamp"])
        act = lg["action"]
        det = lg.get("details", "")[:60]
        uid = lg["user_id"]
        lines.append(f"<t:{ts}:t> `{act}` — <@{uid}> {det}")

    embed = discord.Embed(
        title=f"📋 Audit Log{' for ' + str(user) if user else ''}",
        description="\n".join(lines),
        color=0xFF4500,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# SERVER SECURITY COMMANDS (owner only)
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="allowserver", description="✅ Whitelist a server (Owner only)")
@app_commands.describe(server_id="Server ID to allow (leave blank = current server)")
async def allowserver_cmd(interaction: discord.Interaction, server_id: str = ""):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    gid = int(server_id.strip()) if server_id.strip().isdigit() else interaction.guild_id
    if gid is None:
        await interaction.response.send_message("❌ No server ID provided.", ephemeral=True)
        return
    # Try to get the guild name
    g = client.get_guild(gid)
    name = g.name if g else f"ID {gid}"
    db.allow_guild(gid, name)
    db.log_action(0, "guild_allow", f"{name} ({gid})", actor_id=interaction.user.id)
    await interaction.response.send_message(
        f"✅ **{name}** (`{gid}`) is now whitelisted.\n"
        f"> The whitelist is now active — all other servers will be blocked.",
        ephemeral=True,
    )


@client.tree.command(name="banserver", description="🚫 Ban a server and leave it (Owner only)")
@app_commands.describe(
    server_id="Server ID to ban (leave blank = current server)",
    reason="Reason for the ban",
)
async def banserver_cmd(interaction: discord.Interaction, server_id: str = "", reason: str = "Unauthorised use."):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    gid = int(server_id.strip()) if server_id.strip().isdigit() else interaction.guild_id
    if gid is None:
        await interaction.response.send_message("❌ No server ID provided.", ephemeral=True)
        return
    g = client.get_guild(gid)
    name = g.name if g else f"ID {gid}"
    db.ban_guild_db(gid, name, reason)
    db.log_action(0, "guild_ban", f"{name} ({gid}): {reason}", actor_id=interaction.user.id)
    await interaction.response.send_message(
        f"🚫 **{name}** (`{gid}`) has been banned.\n> {reason}\n> Leaving the server now…",
        ephemeral=True,
    )
    if g:
        try:
            await g.leave()
            print(f"[security] left banned guild: {name} ({gid})")
        except Exception as e:
            print(f"[security] failed to leave {gid}: {e}")


@client.tree.command(name="unbanserver", description="🔓 Unban a server (Owner only)")
@app_commands.describe(server_id="Server ID to unban")
async def unbanserver_cmd(interaction: discord.Interaction, server_id: str):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    if not server_id.strip().isdigit():
        await interaction.response.send_message("❌ Invalid server ID.", ephemeral=True)
        return
    gid = int(server_id.strip())
    db.unban_guild_db(gid)
    db.log_action(0, "guild_unban", str(gid), actor_id=interaction.user.id)
    await interaction.response.send_message(
        f"🔓 Server `{gid}` has been unbanned.\n"
        f"> Note: the bot won't automatically rejoin — they must re-add it.",
        ephemeral=True,
    )


@client.tree.command(name="servers", description="🔐 List all known servers + their whitelist status (Owner only)")
async def servers_cmd(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    guilds = db.get_all_guilds()
    if not guilds:
        await interaction.response.send_message("No servers recorded yet.", ephemeral=True)
        return
    lines = []
    for g in guilds:
        if g["is_banned"]:
            icon = "🚫"
        elif g["is_allowed"]:
            icon = "✅"
        else:
            icon = "⚠️"
        lines.append(f"{icon} **{g['guild_name'] or 'Unknown'}** `{g['guild_id']}`")
        if g["is_banned"] and g["ban_reason"]:
            lines.append(f"   └ reason: {g['ban_reason']}")
    embed = discord.Embed(
        title="🔐 Server Registry",
        description="\n".join(lines[:30]),
        color=0xFF4500,
    )
    embed.set_footer(text=f"✅ Whitelisted  ⚠️ Unknown  🚫 Banned  |  Whitelist active: {db.any_guild_allowed()}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="myservers", description="📋 List every server the bot is currently in (Owner only)")
async def myservers_cmd(interaction: discord.Interaction):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return
    lines = []
    for g in client.guilds:
        allowed = g.id in ALLOWED_GUILD_IDS or db.is_guild_allowed(g.id)
        banned  = db.is_guild_banned(g.id)
        icon = "🚫" if banned else ("✅" if allowed else "⚠️")
        lines.append(f"{icon} **{g.name}** `{g.id}` — {g.member_count} members")
    embed = discord.Embed(
        title=f"📋 Active Servers ({len(client.guilds)})",
        description="\n".join(lines) or "None",
        color=0x7289DA,
    )
    embed.set_footer(text="✅ Allowed  ⚠️ Not yet whitelisted  🚫 Banned")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BOT ACCESS ROLE COMMANDS (owner / admin only)
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="addbotaccess", description="🔑 Allow a server role to use the bot (Admin/Owner)")
@app_commands.describe(role="The server role to grant bot access")
async def addbotaccess_cmd(interaction: discord.Interaction, role: discord.Role):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    db.add_allowed_discord_role(role.id)
    db.log_action(0, "botaccess_add", f"{role.name} ({role.id})", actor_id=interaction.user.id)

    await interaction.response.send_message(
        f"✅ **{role.name}** (`{role.id}`) can now use the bot.\n"
        f"> Members with this role will have access to `/image` and `/video`.",
        ephemeral=True,
    )


@client.tree.command(name="removebotaccess", description="🔒 Remove a server role's bot access (Admin/Owner)")
@app_commands.describe(role="The server role to remove bot access from")
async def removebotaccess_cmd(interaction: discord.Interaction, role: discord.Role):
    db.ensure_user(interaction.user.id, str(interaction.user))
    if not _is_admin_or_owner(interaction.user.id):
        await interaction.response.send_message("❌ Admin or Owner only.", ephemeral=True)
        return

    remaining = db.remove_allowed_discord_role(role.id)
    db.log_action(0, "botaccess_remove", f"{role.name} ({role.id})", actor_id=interaction.user.id)

    open_note = "\n> ⚠️ No access roles set — bot is open to everyone." if not remaining else ""
    await interaction.response.send_message(
        f"✅ Removed **{role.name}** from the bot access list.{open_note}",
        ephemeral=True,
    )


@client.tree.command(name="listbotaccess", description="📋 See which server roles can use the bot")
async def listbotaccess_cmd(interaction: discord.Interaction):
    db.ensure_user(interaction.user.id, str(interaction.user))
    await interaction.response.defer()

    allowed = db.get_allowed_discord_roles()
    if not allowed:
        await interaction.followup.send(
            "📋 **Bot access roles:** *(none — locked to owner only)*",
        )
        return

    guild = interaction.guild
    lines = []
    for rid in allowed:
        r = guild.get_role(rid) if guild else None
        name = r.mention if r else f"`{rid}` *(unknown role)*"
        lines.append(f"• {name}")

    embed = discord.Embed(
        title="📋 Bot Access Roles",
        description="\n".join(lines),
        color=0x7289DA,
    )
    embed.set_footer(text="Only members with these roles can use /image and /video")
    await interaction.followup.send(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
# TEST COMMAND (owner only)
# ══════════════════════════════════════════════════════════════════════════════

@client.tree.command(name="testvideo", description="🧪 Owner-only: generate a Seedance 2.0 test video here")
@app_commands.describe(
    prompt="Prompt (default: '67')",
    duration="Duration in seconds (default: 15)",
    aspect_ratio="Aspect ratio: 16:9 | 9:16 | 1:1 | 21:9 (default: 21:9)",
)
async def testvideo_cmd(
    interaction: discord.Interaction,
    prompt: str = "67",
    duration: int = 15,
    aspect_ratio: str = "21:9",
):
    if not _is_owner(interaction.user.id):
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return

    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        return

    mention    = interaction.user.mention
    model_name = "Seedance 2.0"
    settings   = f"Seedance 2.0 · 720p · {duration}s · {aspect_ratio}"

    global _GEN_DEPTH
    _GEN_DEPTH += 1
    tv_queue_pos = _GEN_DEPTH
    _tv_in_queue = tv_queue_pos > _MAX_CONCURRENT
    _tv_queue_num = tv_queue_pos - _MAX_CONCURRENT

    _tv_initial = (
        f"⏳ {mention} **Queue #{_tv_queue_num}** — waiting for a slot to open…\n> 🖊️ {prompt[:120]}"
        if _tv_in_queue else
        f"🧪 {mention} **Test video generating…** ({settings})\n> 🖊️ {prompt[:120]}"
    )
    if _LOADING_GIFS:
        first_gif = _LOADING_GIFS[0]
        await interaction.edit_original_response(
            content=_tv_initial,
            attachments=[discord.File(io.BytesIO(first_gif.read_bytes()), filename=first_gif.name)],
        )

    _tv_gif_stop = asyncio.Event()

    async def _tv_cycle_gifs() -> None:
        if len(_LOADING_GIFS) < 2:
            return
        idx = 1
        while not _tv_gif_stop.is_set():
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.sleep(8)), timeout=8)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            if _tv_gif_stop.is_set():
                break
            gif = _LOADING_GIFS[idx % len(_LOADING_GIFS)]
            idx += 1
            try:
                await interaction.edit_original_response(
                    attachments=[discord.File(io.BytesIO(gif.read_bytes()), filename=gif.name)],
                )
            except Exception:
                pass

    async def _update(content: str) -> None:
        try:
            await interaction.edit_original_response(content=content)
        except Exception:
            pass

    tracker = ProgressTracker(
        f"🧪 {mention} **Test** ({settings})", prompt, _update, emoji="🧪"
    )

    async def _progress(msg: str) -> None:
        await tracker.step(_mask(msg))

    async def _screenshot(label: str, img_bytes: bytes) -> None:
        await _dm_owner(client, label, img_bytes)

    _tv_gif_task = asyncio.create_task(_tv_cycle_gifs())

    try:
        async with _GEN_SEM:
            if _tv_in_queue:
                await _update(f"🧪 {mention} **Test video generating…** ({settings})\n> 🖊️ {prompt[:120]}")
            video_bytes = await _art.generate_artlist_video(
                prompt=prompt,
                model=model_name,
                resolution="720p",
                duration=duration,
                aspect_ratio=aspect_ratio,
                progress_cb=_progress,
                screenshot_cb=_screenshot,
            )
        # ── Prepend intro ──────────────────────────────────────────────────
        await _update(f"🧪 {mention} **Adding intro…**")
        final_bytes = await _prepend_intro(video_bytes)

        # ── Upload to Streamable ──────────────────────────────────────────
        await _update(f"🧪 {mention} **Uploading to Streamable…**")
        stream_url, dl_bytes = await _upload_streamable(final_bytes, "test_video.mp4")

        send_bytes = dl_bytes if dl_bytes else final_bytes
        caption = f"that shit worked {mention} 🎬 ({settings})\n> 🖊️ {prompt[:120]}"
        if not dl_bytes and stream_url:
            caption += f"\n📽️ **Video:** {stream_url}"

        video_file = None
        mb = len(send_bytes) / 1024 / 1024
        if mb <= _DISCORD_MAX_ATTACH_MB:
            video_file = discord.File(io.BytesIO(send_bytes), filename="test_video.mp4")

        if video_file:
            await interaction.edit_original_response(
                content=caption,
                attachments=[video_file],
            )
        else:
            await interaction.edit_original_response(content=caption, attachments=[])
    except Exception as e:
        import re as _re
        err = _mask(_re.sub(r"https?://\S+", "[URL]", str(e))[:400])
        print(f"[testvideo] ❌ {e}")
        await interaction.edit_original_response(
            content=f"lil bro shit did not work {mention} 💀",
            attachments=[],
        )
    finally:
        _tv_gif_stop.set()
        _tv_gif_task.cancel()
        try:
            await _tv_gif_task
        except asyncio.CancelledError:
            pass
        _GEN_DEPTH -= 1


# ══════════════════════════════════════════════════════════════════════════════
# GIVEAWAY SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

# message_id → set of user_ids who entered
_GIVEAWAY_ENTRIES: dict[int, set[int]] = {}
# message_id → prize string
_GIVEAWAY_PRIZES:  dict[int, str]      = {}


def _giveaway_embed(prize: str, entries: set[int], ended: bool = False, winner_id: int | None = None) -> discord.Embed:
    if ended and winner_id:
        embed = discord.Embed(
            title="🏆  Giveaway Ended!",
            description=f"**Prize:** {prize}",
            color=0xFFD700,
        )
        embed.add_field(name="🎉  Winner", value=f"<@{winner_id}> **Congratulations!** 🥳", inline=False)
        embed.add_field(name="📊  Total entries", value=str(len(entries)), inline=True)
        embed.set_footer(text="Thanks to everyone who entered! 🎊")
    elif ended:
        embed = discord.Embed(
            title="❌  Giveaway Cancelled",
            description=f"**Prize:** {prize}",
            color=0x808080,
        )
        embed.set_footer(text="This giveaway was cancelled by staff.")
    else:
        embed = discord.Embed(
            title="🎉  Giveaway!",
            description=(
                f"**Prize:** {prize}\n\n"
                f"Click **🎉 Enter** below to join!\n"
                f"Staff can pick a winner at any time with **🏆 Pick Winner**."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="📊  Entries so far", value=str(len(entries)), inline=True)
        embed.set_footer(text="Good luck to everyone! 🍀")
    return embed


class GiveawayView(discord.ui.View):
    """Persistent giveaway buttons — enter, pick winner, cancel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎉  Enter Giveaway",
        style=discord.ButtonStyle.primary,
        custom_id="giveaway_enter",
    )
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id  = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in _GIVEAWAY_ENTRIES:
            _GIVEAWAY_ENTRIES[msg_id] = set()

        if user_id in _GIVEAWAY_ENTRIES[msg_id]:
            await interaction.response.send_message(
                "⚠️ You've already entered this giveaway!", ephemeral=True
            )
            return

        _GIVEAWAY_ENTRIES[msg_id].add(user_id)
        entries = _GIVEAWAY_ENTRIES[msg_id]
        prize   = _GIVEAWAY_PRIZES.get(msg_id, "a prize")

        # Update entry count in embed
        embed = _giveaway_embed(prize, entries)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(
            f"✅ You're in! **{len(entries)}** {'person has' if len(entries) == 1 else 'people have'} entered so far. Good luck! 🍀",
            ephemeral=True,
        )

    @discord.ui.button(
        label="🏆  Pick Winner",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_pick",
    )
    async def pick(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Only staff can pick a winner.", ephemeral=True
            )
            return

        msg_id  = interaction.message.id
        entries = _GIVEAWAY_ENTRIES.get(msg_id, set())
        prize   = _GIVEAWAY_PRIZES.get(msg_id, "a prize")

        if not entries:
            await interaction.response.send_message(
                "❌ Nobody has entered the giveaway yet!", ephemeral=True
            )
            return

        import random
        winner_id = random.choice(list(entries))

        # Lock the buttons
        for item in self.children:
            item.disabled = True

        ended_embed = _giveaway_embed(prize, entries, ended=True, winner_id=winner_id)
        await interaction.message.edit(embed=ended_embed, view=self)

        await interaction.response.send_message(
            f"🎊 **Giveaway over!** The winner of **{prize}** is <@{winner_id}>! Congratulations! 🏆🥳",
        )

        # Clean up
        _GIVEAWAY_ENTRIES.pop(msg_id, None)
        _GIVEAWAY_PRIZES.pop(msg_id, None)

    @discord.ui.button(
        label="❌  Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="giveaway_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Only staff can cancel giveaways.", ephemeral=True
            )
            return

        msg_id  = interaction.message.id
        entries = _GIVEAWAY_ENTRIES.get(msg_id, set())
        prize   = _GIVEAWAY_PRIZES.get(msg_id, "a prize")

        for item in self.children:
            item.disabled = True

        cancelled_embed = _giveaway_embed(prize, entries, ended=True, winner_id=None)
        await interaction.message.edit(embed=cancelled_embed, view=self)
        await interaction.response.send_message("❌ Giveaway cancelled.", ephemeral=True)

        _GIVEAWAY_ENTRIES.pop(msg_id, None)
        _GIVEAWAY_PRIZES.pop(msg_id, None)


@client.tree.command(name="giveaway", description="🎉 Start a giveaway — members enter with a button, you pick the winner (Admin/Owner)")
@app_commands.describe(
    prize="What are you giving away?",
)
async def giveaway_cmd(interaction: discord.Interaction, prize: str):
    if not _is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Only staff can start giveaways.", ephemeral=True
        )
        return

    await interaction.response.defer()

    embed = _giveaway_embed(prize, set())
    view  = GiveawayView()

    msg = await interaction.channel.send(embed=embed, view=view)

    # Track this giveaway
    _GIVEAWAY_ENTRIES[msg.id] = set()
    _GIVEAWAY_PRIZES[msg.id]  = prize

    await interaction.delete_original_response()


# ══════════════════════════════════════════════════════════════════════════════
# KEEP-ALIVE
# ══════════════════════════════════════════════════════════════════════════════

class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot alive")

    def log_message(self, *args):
        pass


def _start_keep_alive():
    port = int(os.environ.get("PORT", 8082))
    server = HTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    print(f"[keep-alive] HTTP server on port {port}")
    server.serve_forever()


threading.Thread(target=_start_keep_alive, daemon=True).start()
client.run(DISCORD_TOKEN)
