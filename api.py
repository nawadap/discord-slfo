import json
import time
from typing import Optional, List
import html

import discord
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from config import (
    GUILD_ID,
    LINKED_ROLE_ID,
    VIP_ROLE_ID,
    BETA_ROLE_ID,
    ROBLOX_API_KEY,
    ADMIN_LOG_CHANNEL_ID,
    ANNOUNCE_CHANNEL_ID,
)

from db import (
    init_db,
    get_code,
    delete_code,
    store_link,
    get_link_by_discord,
    get_link_by_roblox_user_id,
    save_player_profile,
    get_pending_admin_actions,
    mark_admin_action_done,
    set_admin_action_result,
    list_links,
    list_profiles,
)

app = FastAPI(title="SLFO API")

DISCORD_BOT: Optional[discord.Client] = None


def set_discord_bot(bot: discord.Client):
    """Call this once from main.py after you create the discord bot."""
    global DISCORD_BOT
    DISCORD_BOT = bot


@app.on_event("startup")
async def _startup():
    await init_db()
    print("[API] DB init ok")


def _check_key(x_api_key: str):
    if x_api_key != ROBLOX_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _apply_roles(discord_id: int, *, linked: bool, vip: bool, beta: bool):
    """Apply linked/vip/beta roles for a given discord user if they are in the guild."""
    if DISCORD_BOT is None:
        return

    guild = DISCORD_BOT.get_guild(int(GUILD_ID))
    if guild is None:
        return

    try:
        member = guild.get_member(int(discord_id))
        if member is None:
            member = await guild.fetch_member(int(discord_id))
    except Exception:
        # user not in server (or cannot fetch) -> keep linked in DB; roles will apply on rejoin
        return

    def role_obj(role_id: int):
        return guild.get_role(int(role_id))

    to_add = []
    to_remove = []

    linked_role = role_obj(LINKED_ROLE_ID)
    vip_role = role_obj(VIP_ROLE_ID)
    beta_role = role_obj(BETA_ROLE_ID)

    # LINKED
    if linked and linked_role and linked_role not in member.roles:
        to_add.append(linked_role)

    # VIP
    if vip and vip_role and vip_role not in member.roles:
        to_add.append(vip_role)
    if (not vip) and vip_role and vip_role in member.roles:
        to_remove.append(vip_role)

    # BETA
    if beta and beta_role and beta_role not in member.roles:
        to_add.append(beta_role)
    if (not beta) and beta_role and beta_role in member.roles:
        to_remove.append(beta_role)

    if to_add:
        await member.add_roles(*to_add, reason="SLFO role sync")
    if to_remove:
        await member.remove_roles(*to_remove, reason="SLFO role sync")


# =========================
# ===== Link Confirm ======
# =========================

class LinkConfirmBody(BaseModel):
    code: str
    roblox_user_id: int
    roblox_username: str


@app.post("/link/confirm")
async def link_confirm(body: LinkConfirmBody, x_api_key: str = Header(default="")):
    _check_key(x_api_key)

    code = body.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    row = await get_code(code)
    if not row:
        return {"ok": False, "error": "invalid_code"}

    discord_id, created_at = row
    _ = int(time.time())  # kept if you want to add TTL logic later

    # Prevent re-link (discord already linked)
    existing_discord = await get_link_by_discord(int(discord_id))
    if existing_discord:
        await delete_code(code)
        return {"ok": False, "error": "already_linked_discord"}

    # Prevent roblox already linked to someone else
    existing_roblox = await get_link_by_roblox_user_id(int(body.roblox_user_id))
    if existing_roblox:
        await delete_code(code)
        return {"ok": False, "error": "already_linked_roblox"}

    await store_link(int(discord_id), int(body.roblox_user_id), body.roblox_username)
    await delete_code(code)

    # 🔔 Discord announce: linked
    if DISCORD_BOT is not None:
        ch = DISCORD_BOT.get_channel(int(ANNOUNCE_CHANNEL_ID))
        if ch is not None:
            embed = discord.Embed(title="🔗 Account Linked", color=0x1ABC9C)
            embed.add_field(name="Discord", value=f"<@{discord_id}> (`{discord_id}`)", inline=False)
            embed.add_field(
                name="Roblox",
                value=f"**{body.roblox_username}** (`{body.roblox_user_id}`)",
                inline=False
            )
            embed.set_footer(text="SLFO — Link System")
            try:
                await ch.send(embed=embed)
            except Exception as e:
                print("[API] announce send failed:", e)

    # ✅ Give LINKED role on link (VIP/BETA will be handled by /profile/update sync)
    if DISCORD_BOT is not None:
        guild = DISCORD_BOT.get_guild(int(GUILD_ID))
        if guild is not None:
            try:
                member = guild.get_member(int(discord_id)) or await guild.fetch_member(int(discord_id))
                role = guild.get_role(int(LINKED_ROLE_ID))
                if role is not None and member is not None:
                    await member.add_roles(role, reason="SLFO link confirmed (Roblox)")
            except Exception as e:
                print("[API] Role add failed:", e)

    return {"ok": True}


# =========================
# ===== Profile Update =====
# =========================

class ProfileUpdateBody(BaseModel):
    roblox_user_id: int
    roblox_username: str
    points: int = 0
    bank: int = 0
    tickets: int = 0
    kills: int = 0
    robux_donated: int = 0
    swords: dict = Field(default_factory=dict)
    vip: bool = False
    beta: bool = False


@app.post("/profile/update")
async def profile_update(body: ProfileUpdateBody, x_api_key: str = Header(default="")):
    _check_key(x_api_key)

    payload = {
        "roblox_user_id": int(body.roblox_user_id),
        "roblox_username": str(body.roblox_username),
        "points": int(body.points),
        "bank": int(body.bank),
        "tickets": int(body.tickets),
        "kills": int(body.kills),
        "robux_donated": int(body.robux_donated),
        "swords": body.swords or {},
        "vip": bool(body.vip),
        "beta": bool(body.beta),
    }

    await save_player_profile(int(body.roblox_user_id), json.dumps(payload, ensure_ascii=False))

    # ✅ If linked -> sync roles (LINKED + VIP + BETA)
    link = await get_link_by_roblox_user_id(int(body.roblox_user_id))
    if link:
        discord_id, _, _, _ = link
        await _apply_roles(
            int(discord_id),
            linked=True,
            vip=bool(body.vip),
            beta=bool(body.beta),
        )

    return {"ok": True}


# =========================
# ===== Admin queue ========
# =========================

@app.get("/admin/actions/pull")
async def admin_pull(limit: int = 50, x_api_key: str = Header(default="")):
    _check_key(x_api_key)

    rows = await get_pending_admin_actions()

    actions = []
    for r in rows[: int(limit)]:
        actions.append({
            "id": r[0],
            "roblox_user_id": r[1],
            "action": r[2],
            "amount": r[3],
            "queued_at": r[4],
        })

    return {"ok": True, "actions": actions}


class AdminAckBody(BaseModel):
    ids: List[int]


@app.post("/admin/actions/ack")
async def admin_ack(body: AdminAckBody, x_api_key: str = Header(default="")):
    _check_key(x_api_key)

    for action_id in body.ids:
        await mark_admin_action_done(int(action_id))

    return {"ok": True}


# =========================
# ===== Admin report ======
# =========================

class AdminActionReportBody(BaseModel):
    action_id: int
    success: bool
    result_text: str = ""
    roblox_user_id: int
    roblox_username: str
    action: str
    amount: int


@app.post("/admin/actions/report")
async def admin_report(body: AdminActionReportBody, x_api_key: str = Header(default="")):
    _check_key(x_api_key)

    await set_admin_action_result(
        action_id=int(body.action_id),
        success=bool(body.success),
        result_text=str(body.result_text or ""),
    )

    # Discord embed (green/red)
    if DISCORD_BOT is not None:
        ch = DISCORD_BOT.get_channel(int(ADMIN_LOG_CHANNEL_ID))
        if ch is not None:
            color = 0x2ECC71 if body.success else 0xE74C3C
            title = "✅ Admin Action Applied" if body.success else "❌ Admin Action Failed"

            embed = discord.Embed(title=title, color=color)
            embed.add_field(
                name="Player",
                value=f"**{body.roblox_username}** (`{body.roblox_user_id}`)",
                inline=False
            )
            embed.add_field(name="Action", value=f"`{body.action}`", inline=True)
            embed.add_field(name="Amount", value=str(int(body.amount)), inline=True)

            if body.result_text:
                embed.add_field(name="Info", value=body.result_text[:900], inline=False)

            embed.set_footer(text=f"ActionId: {body.action_id}")
            try:
                await ch.send(embed=embed)
            except Exception as e:
                print("[API] admin log send failed:", e)

    return {"ok": True}


# =========================
# ===== Dashboard (/) =====
# =========================

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    links = await list_links()
    profiles = await list_profiles()

    async def discord_label(discord_id: int) -> str:
        if DISCORD_BOT is None:
            return f"<span class='muted'>@unknown</span> <span class='muted'>({discord_id})</span>"
        user = DISCORD_BOT.get_user(int(discord_id))
        if user:
            # discriminator may be "0" on newer accounts, but it's fine for display
            return f"{html.escape(user.name)}#{html.escape(getattr(user, 'discriminator', '0'))} <span class='muted'>({discord_id})</span>"
        return f"<span class='muted'>@unknown</span> <span class='muted'>({discord_id})</span>"

    rows_html = []
    now = int(time.time())

    for discord_id, roblox_user_id, roblox_username, linked_at in links:
        p = profiles.get(int(roblox_user_id))
        if p:
            points = int(p.get("points", 0))
            bank = int(p.get("bank", 0))
            total = points + bank
            kills = int(p.get("kills", 0))
            tickets = int(p.get("tickets", 0))
            robux = int(p.get("robux_donated", 0))
            updated_ago = max(0, now - int(p.get("updated_at", linked_at)))

            swords = p.get("swords") or {}
            sword_items = sorted(
                [(k, int(v)) for k, v in swords.items() if int(v) > 0],
                key=lambda x: (-x[1], x[0])
            )
            sword_total = sum(q for _, q in sword_items)
            sword_distinct = len(sword_items)
            top5 = ", ".join([f"{html.escape(name)}×{qty}" for name, qty in sword_items[:5]])
            swords_text = f"{sword_distinct} types / {sword_total} total" + (f" — {top5}" if top5 else "")
        else:
            points = bank = total = kills = tickets = robux = 0
            updated_ago = None
            swords_text = "No data yet"

        disc_txt = await discord_label(int(discord_id))

        rows_html.append(f"""
        <tr>
            <td>
                <div class="small muted">Discord</div>
                <div>{disc_txt}</div>
                <div class="small muted">Mention: <code>&lt;@{discord_id}&gt;</code></div>
            </td>
            <td>
                <div class="small muted">Roblox</div>
                <div><strong>{html.escape(str(roblox_username))}</strong> <span class="muted">({roblox_user_id})</span></div>
                <div class="small muted">Linked: {time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(linked_at)))} UTC</div>
            </td>
            <td>
                <div class="grid">
                    <div class="card"><div class="k">Light</div><div class="v">{points}</div></div>
                    <div class="card"><div class="k">Vault</div><div class="v">{bank}</div></div>
                    <div class="card"><div class="k">Total</div><div class="v">{total}</div></div>
                    <div class="card"><div class="k">Kills</div><div class="v">{kills}</div></div>
                    <div class="card"><div class="k">Tickets</div><div class="v">{tickets}</div></div>
                    <div class="card"><div class="k">Robux</div><div class="v">{robux}</div></div>
                </div>
                <div class="small muted" style="margin-top:8px;">
                    Last updated: {f"{updated_ago}s ago" if updated_ago is not None else "—"}
                </div>
                <div class="small" style="margin-top:6px;">
                    <span class="muted">Swords:</span> {swords_text}
                </div>
            </td>
        </tr>
        """)

    body = "\n".join(rows_html) if rows_html else """
        <tr><td colspan="3" class="muted">No linked players yet.</td></tr>
    """

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>SLFO — Linked Players</title>
  <style>
    :root {{
      --bg: #050A0F;
      --panel: rgba(8, 40, 25, 0.35);
      --stroke: rgba(40, 120, 60, 0.35);
      --text: #dcffdc;
      --muted: rgba(220, 255, 220, 0.65);
      --card: rgba(4, 18, 10, 0.55);
    }}
    body {{
      margin: 0; padding: 24px;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
      background: radial-gradient(1200px 800px at 20% 0%, rgba(8,40,25,.45), transparent 60%),
                  radial-gradient(1000px 600px at 80% 10%, rgba(2,8,10,.65), transparent 55%),
                  var(--bg);
      color: var(--text);
    }}
    h1 {{ margin: 0 0 10px; font-size: 22px; }}
    .sub {{ margin: 0 0 18px; color: var(--muted); }}
    .wrap {{
      border: 1px solid var(--stroke);
      background: var(--panel);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,.35);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
    }}
    th, td {{
      padding: 12px 12px;
      vertical-align: top;
      border-bottom: 1px solid rgba(40, 120, 60, 0.22);
    }}
    th {{
      text-align: left;
      font-size: 12px;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .muted {{ color: var(--muted); }}
    .small {{ font-size: 12px; }}
    code {{
      background: rgba(0,0,0,.25);
      padding: 2px 6px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,.08);
      color: var(--text);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(90px, 1fr));
      gap: 8px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid rgba(40, 120, 60, 0.28);
      border-radius: 12px;
      padding: 8px 10px;
    }}
    .k {{ font-size: 11px; color: var(--muted); }}
    .v {{ font-size: 16px; font-weight: 700; }}
    @media (max-width: 1100px) {{
      .grid {{ grid-template-columns: repeat(3, minmax(90px, 1fr)); }}
    }}
    @media (max-width: 700px) {{
      body {{ padding: 12px; }}
      th:nth-child(3), td:nth-child(3) {{ display: block; }}
      .grid {{ grid-template-columns: repeat(2, minmax(90px, 1fr)); }}
    }}
  </style>
</head>
<body>
  <h1>SLFO — Linked Players</h1>
  <p class="sub">Live view of linked accounts and last synced in-game stats.</p>

  <div class="wrap">
    <table>
      <thead>
        <tr>
          <th style="width: 28%;">Discord</th>
          <th style="width: 22%;">Roblox</th>
          <th style="width: 50%;">Profile</th>
        </tr>
      </thead>
      <tbody>
        {body}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
