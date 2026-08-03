"""
Post the ticket panel (blue Create a Ticket + red Report Someone buttons)
to the #create-ticket channel. Run once after bot.py is live.
"""
import asyncio, os, discord
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN          = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID       = 1517657288444346398

intents        = discord.Intents.default()
client         = discord.Client(intents=intents)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="🎟️  Create a Ticket",
            style=discord.ButtonStyle.primary,
            custom_id="ticket_create",
        ))
        self.add_item(discord.ui.Button(
            label="🚨  Report Someone",
            style=discord.ButtonStyle.danger,
            custom_id="ticket_report",
        ))

@client.event
async def on_ready():
    print(f"✅ Connected as {client.user}")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Guild not found"); await client.close(); return

    # Find #create-ticket
    ch = discord.utils.find(
        lambda c: "create-ticket" in c.name and isinstance(c, discord.TextChannel),
        guild.channels,
    )
    if not ch:
        print("❌ #create-ticket not found"); await client.close(); return

    # Delete old bot messages
    try:
        async for msg in ch.history(limit=30):
            if msg.author == client.user:
                await msg.delete()
    except Exception as e:
        print(f"⚠️  Could not clean old messages: {e}")

    embed = discord.Embed(
        title="🎟️  Support & Reports",
        description=(
            "Need help or want to report someone? Click a button below to open a **private ticket**.\n\n"
            "Your ticket will only be visible to **you and staff**. 🔒\n"
            "A staff member will claim and respond to your ticket as soon as possible. 💜\n\u200b"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="🎟️  Create a Ticket",
        value="General help, questions, appeals, or concerns.",
        inline=True,
    )
    embed.add_field(
        name="🚨  Report Someone",
        value="Report a member for breaking the server rules.",
        inline=True,
    )
    embed.set_footer(text="Tickets are private — staff will be with you shortly ✨")

    await ch.send(embed=embed, view=TicketPanelView())
    print(f"✅ Ticket panel posted in #{ch.name}")
    await client.close()

client.run(TOKEN)
