"""
Give every non-managed role a completely unique color.
"""
import asyncio, os, discord
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TOKEN    = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = 1517657288444346398

intents = discord.Intents.default()
client  = discord.Client(intents=intents)

# Unique color for each role name (lowercase). All distinct, all vivid.
ROLE_COLORS: dict[str, int] = {
    "made the server":              0xFF007F,   # hot pink
    "w wwtv":                       0x00E5FF,   # electric cyan
    "w slax":                       0xFF6D00,   # deep orange
    "owner🤴":                       0xFFD700,   # gold
    "admin":                        0xFF1744,   # bright red
    "winners":                      0x76FF03,   # lime green
    "beta tester for bots":         0xAA00FF,   # deep purple
    "trustworthy":                  0x00B0FF,   # light blue
    "clown🤡":                       0xFF6F00,   # amber orange
    "unverified":                   0x90A4AE,   # blue-grey
    "verified ✓":                    0x00E676,   # neon green
    "owner":                        0xFF9100,   # orange
    "coder":                        0x40C4FF,   # sky blue
    "🛠️ moderator":                  0xE040FB,   # orchid
    "🚨 helper":                     0xFF4081,   # pink
    "bot manager":                  0x64FFDA,   # aqua mint
    "⭐ vip":                        0xFFEA00,   # vivid yellow
    "🔥 og":                         0xFF3D00,   # deep red-orange
    "smart cookie":                 0xB2FF59,   # light green
    "w coder":                      0xF50057,   # deep pink
    "bot manger":                   0x00BFA5,   # teal
    "owner for me":                 0xC6FF00,   # yellow-green
    "exposing members (tuff)":      0x7C4DFF,   # electric violet
    "kind bobby":                   0xFF6B6B,   # coral red
    "co owner🤴🤯":                   0x18FFFF,   # bright aqua
    "begger":                       0xA5D6A7,   # soft green
    "bot owner":                    0xFF80AB,   # light pink
    "anime nerd":                   0x82B1FF,   # lavender blue
    "owner bypasses":               0xFFAB40,   # golden yellow
    "beta tester (only nathan)":    0x69F0AE,   # mint green
    "new role":                     0xCFD8DC,   # light grey
    "jimmy is sad":                 0x536DFE,   # indigo blue
    "beta":                         0xFF6E40,   # deep orange-red
    "beta tester (gonzaluigi)":     0xEA80FC,   # light purple
    "beta (only rk)":               0x80D8FF,   # baby blue
}

@client.event
async def on_ready():
    print(f"✅ Connected as {client.user}")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Guild not found")
        await client.close()
        return

    # Build a pool of extra unique colors for any role not in the map
    extra_colors = [
        0xFF5252, 0xFF6D00, 0xFFD740, 0x69F0AE, 0x40C4FF,
        0xE040FB, 0xFF4081, 0x00E5FF, 0xB2FF59, 0xFFAB40,
        0xF50057, 0x651FFF, 0x00BFA5, 0x76FF03, 0xFF80AB,
    ]
    extra_idx = 0

    updated = skipped = 0

    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role.name == "@everyone" or role.managed:
            skipped += 1
            continue

        key   = role.name.lower().strip()
        color = ROLE_COLORS.get(key)

        if color is None:
            color     = extra_colors[extra_idx % len(extra_colors)]
            extra_idx += 1

        try:
            await role.edit(color=discord.Color(color), reason="Unique color pass")
            print(f"  ✅ {role.name:<42} → #{color:06X}")
            updated += 1
        except discord.Forbidden:
            print(f"  ⛔ {role.name:<42} → too high in hierarchy (move bot role up)")
        except Exception as e:
            print(f"  ⚠️  {role.name:<42} → {e}")

        await asyncio.sleep(0.4)

    print(f"\n✅ Done — {updated} updated, {skipped} skipped (managed/everyone)")
    await client.close()

client.run(TOKEN)
