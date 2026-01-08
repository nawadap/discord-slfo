# main.py
import asyncio
import uvicorn
import discord
from discord.ext import commands

from config import DISCORD_TOKEN, API_HOST, API_PORT, OFFICIAL_GUILD_ID, DEV_GUILD_ID
from db import init_db, get_guild_settings, get_link_by_discord
from bot_api import bridge
from api import app, set_discord_bot
from bot_commands import setup_commands

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
set_discord_bot(bot)
@bot.event
async def on_ready():
    await init_db()
    bridge.set_bot(bot)

    # setup slash commands
    setup_commands(bot.tree)

    try:
        if DEV_GUILD_ID and int(DEV_GUILD_ID) != 0:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"[BOT] Slash commands synced to guild {DEV_GUILD_ID}")
        else:
            await bot.tree.sync()
            print("[BOT] Slash commands synced globally (peut prendre du temps)")
    except Exception as e:
        print("[BOT] Slash sync error:", e)

    print(f"[BOT] Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_member_join(member: discord.Member):
    # Si le membre est linked en DB, on lui remet le rôle linked du serveur où il rejoint (si configuré)
    try:
        link = await get_link_by_discord(member.id)
        if not link:
            return

        settings = await get_guild_settings(int(member.guild.id))
        if not settings:
            return

        linked_role_id = settings.get("linked_role_id")
        if not linked_role_id:
            return

        role = member.guild.get_role(int(linked_role_id))
        if role is None:
            return

        if role in member.roles:
            return

        await member.add_roles(role, reason="SLFO linked user rejoined")
        print(f"[BOT] Re-applied linked role to {member} ({member.id}) in guild {member.guild.id}")

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
