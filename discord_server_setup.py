"""
discord_server_setup.py — One-shot server setup:
  1. Delete any channel/category named "cometapi" (case-insensitive)
  2. Update the rules channel with emoji-rich, colored embed
  3. Create a "🎟️ Tickets & Support" category + #create-ticket channel
"""

import asyncio
import os
import discord
from discord import CategoryChannel, TextChannel
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.environ.get("DISCORD_TOKEN", "")
TARGET_GUILD_ID = 1517657288444346398

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    guild = client.get_guild(TARGET_GUILD_ID)
    if not guild:
        print("❌ Could not find target guild")
        await client.close()
        return

    # ─── 1. LIST CHANNELS ────────────────────────────────────────────────────
    print("\n📋 Current channels:")
    for ch in guild.channels:
        kind = "CAT" if isinstance(ch, CategoryChannel) else "TXT"
        cat = f"  (under: {ch.category.name})" if hasattr(ch, "category") and ch.category else ""
        print(f"  [{kind}] {ch.name} (id={ch.id}){cat}")

    # ─── 2. DELETE "cometapi" channel/category ───────────────────────────────
    deleted = []
    for ch in list(guild.channels):
        if "cometapi" in ch.name.lower():
            try:
                await ch.delete(reason="Requested cleanup")
                deleted.append(ch.name)
                print(f"🗑️  Deleted: {ch.name}")
            except Exception as e:
                print(f"⚠️  Could not delete {ch.name}: {e}")
    if not deleted:
        print("ℹ️  No channels matching 'cometapi' found")

    # ─── 3. UPDATE RULES CHANNEL ─────────────────────────────────────────────
    rules_channel = None
    for ch in guild.text_channels:
        if "rule" in ch.name.lower():
            rules_channel = ch
            break

    if rules_channel:
        print(f"\n📝 Updating rules channel: #{rules_channel.name}")

        rules_embed = discord.Embed(
            title="📜  Server Rules",
            description=(
                "Welcome to the server! Please follow these rules to keep our community safe, "
                "fun, and respectful for everyone. 🌟"
            ),
            color=0x7B2FBE,  # purple
        )
        rules_embed.add_field(
            name="1️⃣  Be Respectful",
            value=(
                "Treat everyone with kindness and respect. "
                "No harassment, hate speech, or personal attacks. 💜"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="2️⃣  No Spam",
            value=(
                "Don't flood chats with repetitive messages, emojis, "
                "or self-promotion. Keep it clean. 🚫"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="3️⃣  Keep It Safe for All Ages",
            value=(
                "No NSFW content, graphic violence, or inappropriate material. "
                "This is a safe space for everyone. 🛡️"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="4️⃣  No Doxxing or Privacy Violations",
            value=(
                "Never share someone's personal information without their consent. "
                "Protect everyone's privacy. 🔒"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="5️⃣  Follow Discord's ToS",
            value=(
                "All members must abide by [Discord's Terms of Service](https://discord.com/terms). "
                "Violations will result in immediate removal. ⚠️"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="6️⃣  Listen to Staff",
            value=(
                "Moderators and admins have final say. "
                "If you have a concern, open a support ticket instead of arguing in public channels. 🎟️"
            ),
            inline=False,
        )
        rules_embed.add_field(
            name="7️⃣  Use Channels Correctly",
            value=(
                "Post in the right channels. Off-topic messages will be moved or deleted. 📂"
            ),
            inline=False,
        )
        rules_embed.set_footer(
            text="🌐 Breaking rules may result in a warn, mute, kick, or ban  •  Last updated by staff"
        )
        rules_embed.set_thumbnail(
            url="https://cdn.discordapp.com/emojis/1234567890.png"  # placeholder — Discord ignores invalid URLs gracefully
        )

        try:
            # Try to edit the most recent bot message; otherwise send fresh
            sent = False
            async for msg in rules_channel.history(limit=20):
                if msg.author == client.user:
                    await msg.edit(embed=rules_embed)
                    print("✅ Edited existing rules embed")
                    sent = True
                    break
            if not sent:
                # Clear old text messages from bot first (optional — skip if no perms)
                await rules_channel.send(embed=rules_embed)
                print("✅ Sent new rules embed")
        except Exception as e:
            print(f"⚠️  Could not update rules: {e}")
    else:
        print("ℹ️  No rules channel found — skipping rules update")

    # ─── 4. CREATE "Tickets & Support" CATEGORY + CHANNEL ───────────────────
    existing_ticket_cat = None
    for ch in guild.categories:
        if "ticket" in ch.name.lower() or "support" in ch.name.lower():
            existing_ticket_cat = ch
            break

    if existing_ticket_cat:
        print(f"\nℹ️  Ticket category already exists: {existing_ticket_cat.name}")
    else:
        try:
            ticket_category = await guild.create_category(
                name="🎟️ Tickets & Support",
                reason="New support section",
            )
            print(f"\n✅ Created category: {ticket_category.name}")

            # #create-ticket channel
            create_ticket_ch = await guild.create_text_channel(
                name="create-ticket",
                category=ticket_category,
                topic="Open a ticket to contact staff for support, questions, or reports.",
                reason="New support section",
            )
            print(f"✅ Created channel: #{create_ticket_ch.name}")

            # Send an info embed in the new channel
            ticket_embed = discord.Embed(
                title="🎟️  Support Tickets",
                description=(
                    "Need help? Have a question or concern?\n\n"
                    "**React with 🎟️ below** or contact a staff member to open a ticket.\n\n"
                    "Our team will assist you as soon as possible! 💜"
                ),
                color=0x00C8FF,  # cyan/blue
            )
            ticket_embed.add_field(
                name="📋  When to open a ticket",
                value=(
                    "• Report a rule violation 🚨\n"
                    "• Appeal a punishment 📝\n"
                    "• Ask staff a private question ❓\n"
                    "• Report a bug or issue 🐛"
                ),
                inline=False,
            )
            ticket_embed.set_footer(text="Please be patient — staff will respond shortly ✨")
            await create_ticket_ch.send(embed=ticket_embed)
            print("✅ Sent ticket info embed")

            # #ticket-logs channel (only visible to admins)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            # Give admins access
            for role in guild.roles:
                if role.permissions.administrator:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True)
            ticket_logs_ch = await guild.create_text_channel(
                name="ticket-logs",
                category=ticket_category,
                overwrites=overwrites,
                topic="Closed ticket transcripts (staff only)",
                reason="New support section",
            )
            print(f"✅ Created channel: #{ticket_logs_ch.name} (staff only)")

        except Exception as e:
            print(f"⚠️  Could not create ticket category: {e}")

    print("\n✅ All done!")
    await client.close()

client.run(TOKEN)
