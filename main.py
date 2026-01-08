# main.py
import asyncio
import uvicorn
import discord
from discord.ext import commands

from config import DISCORD_TOKEN, API_HOST, API_PORT, GUILD_ID
from config import LINKED_ROLE_ID
from db import get_link_by_discord
from bot_api import bridge
from api import app, set_discord_bot
from db import init_db
from bot_commands import setup_commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
set_discord_bot(bot)
@bot.event
async def on_ready():
    await init_db()
    bridge.set_bot(bot)

    # setup slash commands
    setup_commands(bot.tree)

    try:
        if GUILD_ID and int(GUILD_ID) != 0:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"[BOT] Slash commands synced to guild {GUILD_ID}")
        else:
            await bot.tree.sync()
            print("[BOT] Slash commands synced globally (peut prendre du temps)")
    except Exception as e:
        print("[BOT] Slash sync error:", e)

    print(f"[BOT] Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_member_join(member: discord.Member):
    # si le membre revient, on lui remet le rôle s'il est déjà link
    if member.guild.id != int(GUILD_ID):
        return

    link = await get_link_by_discord(member.id)
    if not link:
        return

    role = member.guild.get_role(int(LINKED_ROLE_ID))
    if role is None:
        return

    try:
        await member.add_roles(role, reason="SLFO linked user rejoined")
        print(f"[BOT] Re-applied linked role to {member} ({member.id})")
    except Exception as e:
        print("[BOT] Failed to re-apply role on join:", e)

async def start_api():
    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"[API] Starting on {API_HOST}:{API_PORT}")
    await server.serve()

async def main():
    api_task = asyncio.create_task(start_api())
    await bot.start(DISCORD_TOKEN)
    api_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
