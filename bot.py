# bot.py
import secrets
import string

import discord
from discord import app_commands
from discord.ext import commands

from config import DISCORD_TOKEN, GUILD_ID
from db import init_db, store_code, get_link_by_discord
from bot_api import bridge

def make_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    # exemple: AB12CD34
    return "".join(secrets.choice(alphabet) for _ in range(length))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await init_db()
    bridge.set_bot(bot)

    # Sync slash commands
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
    except Exception as e:
        print("Slash command sync error:", e)

    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.tree.command(name="link", description="Génère un code pour lier ton compte Roblox")
async def link_cmd(interaction: discord.Interaction):
    code = make_code()
    await store_code(code, interaction.user.id)

    await interaction.response.send_message(
        f"🔗 **Link Roblox**\n"
        f"Dans Roblox, tape :\n"
        f"```text\n:link {code}\n```\n"
        f"⏳ Le code expire dans ~10 minutes.",
        ephemeral=True
    )

@bot.tree.command(name="whoami", description="Voir ton compte Roblox lié")
async def whoami_cmd(interaction: discord.Interaction):
    row = await get_link_by_discord(interaction.user.id)
    if not row:
        await interaction.response.send_message(
            "Tu n'es pas encore lié. Utilise `/link` puis `:link CODE` dans Roblox.",
            ephemeral=True
        )
        return

    _, roblox_user_id, roblox_username, linked_at = row
    await interaction.response.send_message(
        f"✅ Lié à Roblox: **{roblox_username or 'Unknown'}** (`{roblox_user_id}`)",
        ephemeral=True
    )

bot.run(DISCORD_TOKEN)
