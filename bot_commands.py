import secrets
import string
import time
import discord
from discord import app_commands
import os
import httpx
from config import (
    OFFICIAL_GUILD_ID,
    DEV_GUILD_ID,
    LINKED_ROLE_ID,
    CODE_TTL_SECONDS,
    ADMIN_LOG_CHANNEL_ID,
    ADMIN_ROLE_ID,
)
from db import (
    store_code,
    get_link_by_discord,
    delete_link,
    delete_unused_codes_for_user,
    get_link_by_roblox_username,
    get_link_by_roblox_user_id,
    get_profile_by_roblox_user_id,
    enqueue_admin_action,
)

EMBED_COLOR = 0x0B2E1A  # SLFO dark forest green


# ==================== Utils ====================

def make_code(length=8):
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))


def chunk_lines(lines, size):
    return [lines[i:i + size] for i in range(0, len(lines), size)]


def build_sword_lines(swords: dict):
    items = sorted(((k, int(v)) for k, v in swords.items() if int(v) > 0), key=lambda x: (-x[1], x[0]))
    lines = [f"{name} × {qty}" for name, qty in items]
    return lines, sum(qty for _, qty in items), len(items)


def _safe_amount(x: int) -> int:
    return max(0, int(x))

def is_official_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        # Doit être dans un serveur
        if not interaction.guild or not interaction.guild_id:
            raise app_commands.CheckFailure("Guild only.")

        # Doit être sur le serveur officiel
        if int(interaction.guild_id) != int(OFFICIAL_GUILD_ID):
            raise app_commands.CheckFailure("Not in official guild.")

        # Vérif rôle admin
        try:
            member = interaction.user
            # interaction.user est souvent un Member, mais on sécurise
            if not isinstance(member, discord.Member):
                member = await interaction.guild.fetch_member(interaction.user.id)

            return any(int(r.id) == int(ADMIN_ROLE_ID) for r in member.roles)
        except Exception:
            return False

    return app_commands.check(predicate)

async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "❌",
            ephemeral=True
        )

# ==================== View ====================

class SwordInventoryView(discord.ui.View):
    def __init__(self, owner_id: int, base_embed: discord.Embed, pages: list[str]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.pages = pages
        self.index = 0

        base = base_embed.to_dict()
        self.title = base.get("title", "Profile — SLFO")
        self.color = base_embed.color

        self.static_fields = []
        for f in base.get("fields", []):
            name = f.get("name", "")
            if name.startswith("⚔️ Swords"):
                continue
            self.static_fields.append((name, f.get("value", ""), f.get("inline", False)))

        self._refresh_buttons()

    def _refresh_buttons(self):
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    def _make_embed(self):
        e = discord.Embed(title=self.title, color=self.color)
        for name, value, inline in self.static_fields:
            e.add_field(name=name, value=value, inline=inline)

        e.add_field(
            name=f"⚔️ Swords (page {self.index+1}/{len(self.pages)})",
            value=self.pages[self.index],
            inline=False
        )

        e.set_footer(text="SLFO — Profile")
        return e

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the command author can use these buttons.",
                ephemeral=True
            )
            return False
        return True

    async def _update(self, interaction):
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        self.index = max(0, self.index - 1)
        await self._update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._update(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)


# ==================== Commands ====================

def setup_commands(tree: app_commands.CommandTree):

    # ---------- LINK ----------

    @tree.command(name="link", description="Link your Discord account with Roblox")
    async def link_cmd(interaction: discord.Interaction):
        existing = await get_link_by_discord(interaction.user.id)
        if existing:
            _, rid, rname, _ = existing
            await interaction.response.send_message(
                f"✅ Already linked with **{rname}** (`{rid}`)\nUse `/unlink` to remove the link.",
                ephemeral=True
            )
            return

        await delete_unused_codes_for_user(interaction.user.id)
        code = make_code()
        await store_code(code, interaction.user.id)

        await interaction.response.send_message(
            f"🕯️ **Link your soul**\nIn Roblox chat type:\n```text\n:link {code}\n```"
            f"\nCode expires in ~{CODE_TTL_SECONDS//60} minutes.",
            ephemeral=True
        )

    @tree.command(name="unlink", description="Unlink your Roblox account")
    async def unlink_cmd(interaction: discord.Interaction):
    
        await interaction.response.defer(ephemeral=True)
    
        # 🔎 get link BEFORE deletion (for logging)
        link_before = await get_link_by_discord(interaction.user.id)
    
        removed = await delete_link(interaction.user.id)
        if not removed:
            await interaction.followup.send("❌ Not linked.", ephemeral=True)
            return
            
        # 🧹 remove role (sur le serveur officiel uniquement)
        try:
            official_guild = interaction.client.get_guild(int(OFFICIAL_GUILD_ID))
            if official_guild:
                role = official_guild.get_role(int(LINKED_ROLE_ID))
                if role:
                    try:
                        member = await official_guild.fetch_member(interaction.user.id)
                        if role in member.roles:
                            await member.remove_roles(role, reason="SLFO unlink")
                    except discord.NotFound:
                        pass
        except Exception as e:
            print("[BOT] Role remove failed:", e)
            
        # 🧾 admin log
        try:
            ch = interaction.client.get_channel(int(ADMIN_LOG_CHANNEL_ID))
            if ch:
                embed = discord.Embed(
                    title="🔓 Account Unlinked",
                    color=0xE67E22
                )
    
                embed.add_field(
                    name="Discord",
                    value=f"<@{interaction.user.id}> (`{interaction.user.id}`)",
                    inline=False
                )
    
                if link_before:
                    _, rid, rname, _ = link_before
                    embed.add_field(
                        name="Roblox",
                        value=f"**{rname}** (`{rid}`)",
                        inline=False
                    )
    
                embed.set_footer(text="SLFO — Link System")
                await ch.send(embed=embed)
        except Exception as e:
            print("[BOT] Unlink log failed:", e)
    
        await interaction.followup.send("🧹 Link removed successfully.", ephemeral=True)

    # ---------- PROFILE ----------

    @tree.command(name="profile", description="Show a player profile")
    @app_commands.describe(pseudo="Optional Roblox username")
    async def profile_cmd(interaction, pseudo: str = None):

        if pseudo:
            link = await (get_link_by_roblox_user_id(int(pseudo)) if pseudo.isdigit() else get_link_by_roblox_username(pseudo))
        else:
            link = await get_link_by_discord(interaction.user.id)

        if not link:
            await interaction.response.send_message("❌ Player not linked.", ephemeral=False)
            return

        discord_id, roblox_id, roblox_name, _ = link
        profile = await get_profile_by_roblox_user_id(roblox_id)

        if not profile:
            await interaction.response.send_message("⚠️ Profile not synced yet.", ephemeral=False)
            return

        now = int(time.time())
        updated_ago = max(0, now - int(profile["updated_at"]))

        light = int(profile["points"])
        bank = int(profile["bank"])
        total = light + bank
        kills = int(profile["kills"])
        tickets = int(profile["tickets"])
        robux = int(profile["robux_donated"])

        sword_lines, _, _ = build_sword_lines(profile["swords"])
        pages = ["```text\nNone\n```"] if not sword_lines else [
            "```text\n" + "\n".join(p) + "\n```" for p in chunk_lines(sword_lines, 15)
        ]

        embed = discord.Embed(title="Profile — SLFO", color=EMBED_COLOR)
        embed.add_field(name="Last updated", value=f"{updated_ago}s ago", inline=False)
        embed.add_field(name="Identity", value=f"**Roblox:** {roblox_name} (`{roblox_id}`)\n**Discord:** <@{discord_id}>", inline=False)
        embed.add_field(name="✨ Light", value=light)
        embed.add_field(name="🏦 Vault", value=bank)
        embed.add_field(name="🌕 Total", value=total)
        embed.add_field(name="🗡️ Kills", value=kills)
        embed.add_field(name="🎟️ Tickets", value=tickets)
        embed.add_field(name="💚 Robux Donated", value=f"{robux} R$")
        embed.add_field(name=f"⚔️ Swords (page 1/{len(pages)})", value=pages[0], inline=False)

        view = SwordInventoryView(interaction.user.id, embed, pages)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

    # ---------- ADMIN COMMANDS ----------

    @tree.command(name="vault_add", description="(Admin) Add Light to a player's Vault")
    @is_official_admin()
    async def vault_add_cmd(interaction, pseudo: str, amount: int):
        amount = _safe_amount(amount)
        link = await (get_link_by_roblox_user_id(int(pseudo)) if pseudo.isdigit() else get_link_by_roblox_username(pseudo))
        if not link:
            await interaction.response.send_message("❌ Not linked", ephemeral=True)
            return

        _, roblox_id, name, _ = link
        action_id = await enqueue_admin_action(roblox_id, "BANK_ADD", amount)
        await interaction.response.send_message(f"✅ BANK_ADD {amount} queued for {name} (#{action_id})", ephemeral=True)

    @tree.command(name="vault_remove", description="(Admin) Remove Light from a player's Vault")
    @is_official_admin()
    async def vault_remove_cmd(interaction, pseudo: str, amount: int):
        amount = _safe_amount(amount)
        link = await (get_link_by_roblox_user_id(int(pseudo)) if pseudo.isdigit() else get_link_by_roblox_username(pseudo))
        if not link:
            await interaction.response.send_message("❌ Not linked", ephemeral=True)
            return

        _, roblox_id, name, _ = link
        action_id = await enqueue_admin_action(roblox_id, "BANK_REMOVE", amount)
        await interaction.response.send_message(f"✅ BANK_REMOVE {amount} queued for {name} (#{action_id})", ephemeral=True)

    @tree.command(name="hand_remove", description="(Admin) Remove Light from a player's hand")
    @is_official_admin()
    async def hand_remove_cmd(interaction, pseudo: str, amount: int):
        amount = _safe_amount(amount)
        link = await (get_link_by_roblox_user_id(int(pseudo)) if pseudo.isdigit() else get_link_by_roblox_username(pseudo))
        if not link:
            await interaction.response.send_message("❌ Not linked", ephemeral=True)
            return

        _, roblox_id, name, _ = link
        action_id = await enqueue_admin_action(roblox_id, "HAND_REMOVE", amount)
        await interaction.response.send_message(f"✅ HAND_REMOVE {amount} queued for {name} (#{action_id})", ephemeral=True)

    @tree.command(name="admin_announce", description="(Admin) Global announcement in all Roblox servers")
    @is_official_admin()
    @app_commands.describe(message="Message to broadcast to all Roblox servers")
    async def admin_announce_cmd(interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
    
        base_url = os.environ["FASTAPI_BASE_URL"]  # ex: https://api.tondomaine.com
        token = os.environ["INTERNAL_ADMIN_TOKEN"]
    
        body = {
            "sender_name": interaction.user.display_name,
            "message": message[:300],  # limite simple (tu peux ajuster)
        }
    
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{base_url}/admin/announce",
                    json=body,
                    headers={"x-admin-token": token},
                )
            if r.status_code >= 300:
                await interaction.followup.send(f"❌ API error: {r.status_code} {r.text[:400]}", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"❌ Request failed: {e}", ephemeral=True)
            return
    
        await interaction.followup.send("✅ Announcement sent to all Roblox servers.", ephemeral=True)
