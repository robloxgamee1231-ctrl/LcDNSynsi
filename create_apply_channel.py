"""
Create a #staff-applications channel in the Tickets & Support category
with an embed explaining how to apply for Admin or Moderator.
"""
import asyncio, os, discord
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN    = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = 1517657288444346398

intents = discord.Intents.default()
client  = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Connected as {client.user}")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Guild not found"); await client.close(); return

    # Find the Tickets & Support category
    category = discord.utils.find(
        lambda c: "ticket" in c.name.lower() or "support" in c.name.lower(),
        guild.categories,
    )
    if not category:
        print("❌ Tickets & Support category not found"); await client.close(); return

    # Check if channel already exists
    existing = discord.utils.find(
        lambda c: "apply" in c.name.lower() or "application" in c.name.lower(),
        category.text_channels,
    )
    if existing:
        ch = existing
        print(f"ℹ️  Channel already exists: #{ch.name} — refreshing embed")
        try:
            async for msg in ch.history(limit=20):
                if msg.author == client.user:
                    await msg.delete()
        except Exception:
            pass
    else:
        # Make the channel read-only for regular members
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            ),
        }
        # Admins can still type
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                )

        ch = await guild.create_text_channel(
            name="staff-applications",
            category=category,
            overwrites=overwrites,
            topic="Want to become an Admin or Moderator? Read this channel and open a ticket!",
            reason="Staff applications info channel",
        )
        print(f"✅ Created channel: #{ch.name}")

    # ── Main info embed ───────────────────────────────────────────────────────
    main_embed = discord.Embed(
        title="📋  Want to Join the Staff Team?",
        description=(
            "We're always looking for dedicated and trustworthy members to help keep the server "
            "safe and fun for everyone! 🌟\n\n"
            "Read the requirements below, then **open a ticket** in <#{}> to apply."
        ).format(
            next(
                (c.id for c in category.text_channels if "create-ticket" in c.name),
                0,
            )
        ),
        color=0x7C4DFF,
    )

    # Admin requirements
    main_embed.add_field(
        name="🛡️  Admin Requirements",
        value=(
            "• Must have been a member for **30+ days**\n"
            "• Have **zero** active warns or bans\n"
            "• Already holding a **Moderator** role\n"
            "• Trusted and well-known in the community\n"
            "• Must be **active daily**\n"
            "• Minimum age: **13+**"
        ),
        inline=True,
    )

    # Moderator requirements
    main_embed.add_field(
        name="🚨  Moderator Requirements",
        value=(
            "• Must have been a member for **14+ days**\n"
            "• Have **zero** active warns\n"
            "• Be **active** in the server regularly\n"
            "• Friendly, calm, and fair to all members\n"
            "• Good understanding of the server rules\n"
            "• Minimum age: **13+**"
        ),
        inline=True,
    )

    main_embed.add_field(
        name="\u200b",
        value="\u200b",
        inline=False,
    )

    main_embed.add_field(
        name="📝  How to Apply",
        value=(
            "1️⃣  Go to <#{}> and click **🎟️ Create a Ticket**\n"
            "2️⃣  In your ticket, state the role you're applying for\n"
            "3️⃣  Answer honestly — staff will ask you a few questions\n"
            "4️⃣  Wait for a decision. **Do not DM staff asking for an update** ⚠️"
        ).format(
            next(
                (c.id for c in category.text_channels if "create-ticket" in c.name),
                0,
            )
        ),
        inline=False,
    )

    main_embed.add_field(
        name="⚠️  Important Notes",
        value=(
            "• **Asking for a role directly will disqualify you**\n"
            "• We accept/deny applications at our own pace — be patient\n"
            "• Decisions are final unless you appeal after 30 days\n"
            "• Being Staff means responsibility, not power 💜"
        ),
        inline=False,
    )

    main_embed.set_footer(
        text="Good luck! We look forward to reviewing your application ✨"
    )

    await ch.send(embed=main_embed)
    print("✅ Posted staff application embed")
    await client.close()

client.run(TOKEN)
