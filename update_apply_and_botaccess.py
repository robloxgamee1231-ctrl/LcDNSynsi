"""
1. Refresh #staff-applications with updated requirements
2. Create #bot-access channel in Tickets & Support
"""
import asyncio, os, discord
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN    = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = 1517657288444346398

intents = discord.Intents.default()
client  = discord.Client(intents=intents)


async def refresh_staff_applications(guild: discord.Guild, category: discord.CategoryChannel):
    ch = discord.utils.find(
        lambda c: "application" in c.name.lower() or "apply" in c.name.lower(),
        category.text_channels,
    )
    if not ch:
        print("❌ #staff-applications not found"); return

    # Wipe old bot messages
    try:
        async for msg in ch.history(limit=20):
            if msg.author == client.user:
                await msg.delete()
    except Exception:
        pass

    ticket_ch_id = next(
        (c.id for c in category.text_channels if "create-ticket" in c.name), 0
    )

    main_embed = discord.Embed(
        title="📋  Want to Join the Staff Team?",
        description=(
            "We're always looking for dedicated, communicative, and trustworthy members "
            "to help run the server! 🌟\n\n"
            "Read the requirements below carefully, then **open a ticket** in "
            f"<#{ticket_ch_id}> to apply."
        ),
        color=0x7C4DFF,
    )

    main_embed.add_field(
        name="🛡️  Admin Requirements",
        value=(
            "• **Online for 10 days in a row** (no skipping)\n"
            "• **Actively talking** with members — not just using bots\n"
            "• Already holding the **Moderator** role\n"
            "• Zero active warns or bans\n"
            "• Trusted and well-known in the community\n"
            "• Minimum age: **13+**"
        ),
        inline=False,
    )

    main_embed.add_field(
        name="🚨  Moderator Requirements",
        value=(
            "• **Online for at least 5 days** and active in chat\n"
            "• **Talking with people** regularly — not just lurking or using bots\n"
            "• Zero active warns\n"
            "• Friendly, calm, and fair to all members\n"
            "• Good understanding of the server rules\n"
            "• Minimum age: **13+**"
        ),
        inline=False,
    )

    main_embed.add_field(
        name="📝  How to Apply",
        value=(
            f"1️⃣  Go to <#{ticket_ch_id}> and click **🎟️ Create a Ticket**\n"
            "2️⃣  State the role you're applying for (**Admin** or **Moderator**)\n"
            "3️⃣  Answer honestly — staff will ask you a few questions\n"
            "4️⃣  Be patient. **Do not DM staff asking for updates** ⚠️"
        ),
        inline=False,
    )

    main_embed.add_field(
        name="⚠️  Important Notes",
        value=(
            "• **Asking for a role directly will disqualify you instantly**\n"
            "• Activity in chat matters more than bot usage\n"
            "• Decisions are final unless you appeal after 30 days\n"
            "• Being Staff means responsibility, not power 💜"
        ),
        inline=False,
    )

    main_embed.set_footer(text="Good luck! We look forward to reviewing your application ✨")

    await ch.send(embed=main_embed)
    print(f"✅ Refreshed #staff-applications")


async def create_bot_access(guild: discord.Guild, category: discord.CategoryChannel):
    # Check if already exists
    existing = discord.utils.find(
        lambda c: "bot-access" in c.name.lower() or "bot access" in c.name.lower(),
        category.text_channels,
    )
    if existing:
        ch = existing
        print(f"ℹ️  #bot-access already exists — refreshing embed")
        try:
            async for msg in ch.history(limit=20):
                if msg.author == client.user:
                    await msg.delete()
        except Exception:
            pass
    else:
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
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                )

        ch = await guild.create_text_channel(
            name="bot-access",
            category=category,
            overwrites=overwrites,
            topic="Request access to use the server bots. Open a ticket to apply!",
            reason="Bot access info channel",
        )
        print(f"✅ Created channel: #{ch.name}")

    ticket_ch_id = next(
        (c.id for c in category.text_channels if "create-ticket" in c.name), 0
    )

    embed = discord.Embed(
        title="🤖  Bot Access",
        description=(
            "Want to use the bots in this server? You'll need to request access first.\n\n"
            "Open a ticket in <#{}> and a staff member will review your request. 💜"
        ).format(ticket_ch_id),
        color=0x00E5FF,
    )

    embed.add_field(
        name="✅  Who Can Get Bot Access?",
        value=(
            "• Members who are **verified** and in good standing\n"
            "• No active warns or bans\n"
            "• Have been in the server for at least a few days\n"
            "• Are active and communicate with the community"
        ),
        inline=False,
    )

    embed.add_field(
        name="🚫  Who Will Be Denied?",
        value=(
            "• Members with **active warns or bans**\n"
            "• New/unknown accounts with no activity\n"
            "• Anyone who has abused bots in the past\n"
            "• Members who only joined to use the bot"
        ),
        inline=False,
    )

    embed.add_field(
        name="📝  How to Request Access",
        value=(
            f"1️⃣  Go to <#{ticket_ch_id}> and click **🎟️ Create a Ticket**\n"
            "2️⃣  Say you're requesting **bot access** and which bot you want to use\n"
            "3️⃣  Staff will review and grant or deny access\n"
            "4️⃣  If approved, you'll be given the appropriate access role 🎉"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚠️  Rules",
        value=(
            "• **Do not abuse the bots** — access will be revoked immediately\n"
            "• Follow all server rules while using bots\n"
            "• Bot access can be removed at any time by staff"
        ),
        inline=False,
    )

    embed.set_footer(text="Bot access is a privilege, not a right 🤖✨")

    await ch.send(embed=embed)
    print("✅ Posted bot-access embed")


@client.event
async def on_ready():
    print(f"✅ Connected as {client.user}")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Guild not found"); await client.close(); return

    category = discord.utils.find(
        lambda c: "ticket" in c.name.lower() or "support" in c.name.lower(),
        guild.categories,
    )
    if not category:
        print("❌ Tickets & Support category not found"); await client.close(); return

    await refresh_staff_applications(guild, category)
    await create_bot_access(guild, category)

    print("\n✅ All done!")
    await client.close()

client.run(TOKEN)
