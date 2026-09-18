"""1GC(ian) dashboard — Discord bot cogs."""
from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import COLL, get_db, next_case_number
from automod_engine import check_message, is_exempt
from ai_service import ticket_ai_reply, summarize_ticket

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.guilds = True
INTENTS.reactions = True

BOT_STARTED_AT: Optional[datetime] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    m, s = divmod(seconds, 60); h, m = divmod(m, 60); d, h = divmod(h, 24)
    p = []
    if d: p.append(f"{d}d")
    if h: p.append(f"{h}h")
    if m: p.append(f"{m}m")
    if s and not d and not h: p.append(f"{s}s")
    return " ".join(p)


def _render_vars(text: str, **vars) -> str:
    for k, v in vars.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def _hex_to_int(hexcolor) -> int:
    if isinstance(hexcolor, int):
        return hexcolor
    try:
        return int(str(hexcolor).lstrip("#"), 16)
    except Exception:
        return 0x00F0FF


def _build_embed(cfg: dict, **vars) -> Optional[discord.Embed]:
    if not cfg or cfg.get("enabled") is False:
        # allow embed to be built even if enabled=False for previews when caller wants
        pass
    e = discord.Embed(
        title=_render_vars(cfg.get("title", "") or "", **vars),
        description=_render_vars(cfg.get("description", "") or "", **vars),
        color=_hex_to_int(cfg.get("color") or "#00F0FF"),
    )
    if cfg.get("thumbnail"): e.set_thumbnail(url=cfg["thumbnail"])
    if cfg.get("image"): e.set_image(url=cfg["image"])
    if cfg.get("author_name"):
        e.set_author(name=cfg["author_name"], icon_url=cfg.get("author_icon") or None)
    if cfg.get("footer_text"):
        e.set_footer(text=_render_vars(cfg["footer_text"], **vars), icon_url=cfg.get("footer_icon") or None)
    for f in cfg.get("fields") or []:
        e.add_field(name=_render_vars(f.get("name", "\u200b"), **vars),
                    value=_render_vars(f.get("value", "\u200b"), **vars),
                    inline=bool(f.get("inline")))
    return e


# ============================== Sync + cache ==============================
class SyncCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _cache_guild(self, g: discord.Guild):
        db = get_db()
        await db[COLL["guilds"]].update_one(
            {"_id": str(g.id)},
            {"$set": {"_id": str(g.id), "id": str(g.id), "name": g.name,
                       "icon": (str(g.icon.url) if g.icon else None),
                       "member_count": g.member_count or 0,
                       "owner_id": str(g.owner_id) if g.owner_id else None}},
            upsert=True)
        await db[COLL["channels"]].delete_many({"guild_id": str(g.id)})
        chs = [{"guild_id": str(g.id), "id": str(c.id), "name": c.name, "type": c.type.name} for c in g.channels]
        if chs:
            await db[COLL["channels"]].insert_many(chs)
        await db[COLL["roles"]].delete_many({"guild_id": str(g.id)})
        rs = [{"guild_id": str(g.id), "id": str(r.id), "name": r.name,
                "color": r.color.value, "position": r.position, "managed": r.managed}
                for r in g.roles if r.name != "@everyone"]
        if rs:
            await db[COLL["roles"]].insert_many(rs)

    @commands.Cog.listener()
    async def on_ready(self):
        global BOT_STARTED_AT
        BOT_STARTED_AT = datetime.now(timezone.utc)
        for g in self.bot.guilds:
            await self._cache_guild(g)
        try:
            synced = await self.bot.tree.sync()
            print(f"[1GC dashboard] Synced {len(synced)} slash commands as {self.bot.user}")
        except Exception as e:
            print(f"[1GC dashboard] Slash sync failed: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, ch): await self._cache_guild(ch.guild)
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, ch): await self._cache_guild(ch.guild)
    @commands.Cog.listener()
    async def on_guild_role_create(self, r): await self._cache_guild(r.guild)
    @commands.Cog.listener()
    async def on_guild_role_delete(self, r): await self._cache_guild(r.guild)
    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after): await self._cache_guild(after.guild)


# ============================== Moderation ==============================
class ModerationCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _log_case(self, guild, action, target, moderator, reason, duration=None):
        db = get_db()
        n = await next_case_number(str(guild.id))
        case = {"id": f"case-{guild.id}-{n}", "guild_id": str(guild.id), "case_number": n,
                "action": action, "target_id": str(target.id), "target_tag": str(target),
                "moderator_id": str(moderator.id), "moderator_tag": str(moderator),
                "reason": reason or "No reason provided", "duration_seconds": duration,
                "created_at": _now_iso(), "active": True}
        await db[COLL["cases"]].insert_one(case)
        s = await db[COLL["mod_settings"]].find_one({"guild_id": str(guild.id)})
        if s and s.get("log_channel_id"):
            ch = guild.get_channel(int(s["log_channel_id"]))
            if ch:
                e = discord.Embed(title=f"Case #{n} — {action.upper()}", color=0x00F0FF,
                                    timestamp=datetime.now(timezone.utc))
                e.add_field(name="User", value=f"{target} ({target.id})", inline=False)
                e.add_field(name="Moderator", value=str(moderator), inline=True)
                if duration: e.add_field(name="Duration", value=_fmt_duration(duration), inline=True)
                e.add_field(name="Reason", value=reason or "—", inline=False)
                try: await ch.send(embed=e)
                except Exception: pass
        return n

    async def _dm_user(self, guild, user, action, reason, duration_seconds=None, case_id=0, moderator="Staff"):
        db = get_db()
        s = await db[COLL["mod_settings"]].find_one({"guild_id": str(guild.id)}) or {}
        m = {"warn": ("dm_on_warn", "warn_dm_template"),
             "timeout": ("dm_on_timeout", "timeout_dm_template"),
             "kick": ("dm_on_kick", "kick_dm_template"),
             "ban": ("dm_on_ban", "ban_dm_template")}
        p = m.get(action)
        if not p or not s.get(p[0], True): return
        tpl = s.get(p[1]) or ""
        text = _render_vars(tpl, user=user.mention if hasattr(user, "mention") else str(user),
                             username=str(user), server=guild.name, reason=reason or "—",
                             duration=_fmt_duration(duration_seconds or 0), case_id=case_id, moderator=moderator)
        try: await user.send(text)
        except Exception: pass

    async def _maybe_escalate(self, guild, member):
        db = get_db()
        s = await db[COLL["mod_settings"]].find_one({"guild_id": str(guild.id)}) or {}
        esc = s.get("escalation") or []
        if not esc: return
        n = await db[COLL["cases"]].count_documents({"guild_id": str(guild.id), "target_id": str(member.id), "action": "warn"})
        rule = None
        for r in sorted(esc, key=lambda x: x["warnings"]):
            if n >= r["warnings"]: rule = r
        if not rule: return
        action, dur = rule["action"], rule.get("duration_seconds")
        try:
            if action == "timeout" and dur:
                await member.timeout(datetime.now(timezone.utc) + timedelta(seconds=dur), reason=f"Auto-escalation @ {n} warns")
                await self._log_case(guild, "timeout", member, self.bot.user, f"Auto-escalation ({n} warnings)", dur)
                await self._dm_user(guild, member, "timeout", "Automatic escalation", dur)
            elif action == "kick":
                await self._log_case(guild, "kick", member, self.bot.user, f"Auto-escalation ({n} warnings)")
                await self._dm_user(guild, member, "kick", "Automatic escalation")
                await member.kick(reason="Auto-escalation")
            elif action == "ban":
                await self._log_case(guild, "ban", member, self.bot.user, f"Auto-escalation ({n} warnings)")
                await self._dm_user(guild, member, "ban", "Automatic escalation")
                await member.ban(reason="Auto-escalation")
        except discord.Forbidden:
            pass

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.default_permissions(moderate_members=True)
    async def warn(self, itx: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if member.top_role >= itx.user.top_role and itx.user.id != itx.guild.owner_id:
            return await itx.response.send_message("Permission hierarchy blocks this action.", ephemeral=True)
        n = await self._log_case(itx.guild, "warn", member, itx.user, reason)
        await self._dm_user(itx.guild, member, "warn", reason, case_id=n, moderator=str(itx.user))
        await itx.response.send_message(f"Warned {member.mention} — case #{n}.", ephemeral=True)
        await self._maybe_escalate(itx.guild, member)

    @app_commands.command(name="warnings", description="List a member's warnings")
    async def warnings(self, itx: discord.Interaction, member: discord.Member):
        db = get_db()
        cases = await db[COLL["cases"]].find(
            {"guild_id": str(itx.guild.id), "target_id": str(member.id), "action": "warn"},
            {"_id": 0}).sort("case_number", -1).to_list(25)
        if not cases:
            return await itx.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
        e = discord.Embed(title=f"Warnings — {member}", color=0xF59E0B)
        for c in cases[:10]:
            e.add_field(name=f"Case #{c['case_number']}", value=f"{c.get('reason','—')}\nby <@{c['moderator_id']}>", inline=False)
        await itx.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="timeout", description="Timeout a member (minutes)")
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(self, itx, member: discord.Member, minutes: int, reason: str = "No reason provided"):
        if minutes <= 0 or minutes > 40320:
            return await itx.response.send_message("Minutes must be 1..40320.", ephemeral=True)
        try:
            await member.timeout(datetime.now(timezone.utc) + timedelta(minutes=minutes), reason=reason)
        except discord.Forbidden:
            return await itx.response.send_message("I lack permissions.", ephemeral=True)
        n = await self._log_case(itx.guild, "timeout", member, itx.user, reason, minutes * 60)
        await self._dm_user(itx.guild, member, "timeout", reason, minutes * 60, n, str(itx.user))
        await itx.response.send_message(f"Timed out {member.mention} for {minutes}m — case #{n}.", ephemeral=True)

    @app_commands.command(name="untimeout", description="Remove a timeout")
    @app_commands.default_permissions(moderate_members=True)
    async def untimeout(self, itx, member: discord.Member):
        try: await member.timeout(None)
        except discord.Forbidden: return await itx.response.send_message("I lack permissions.", ephemeral=True)
        await self._log_case(itx.guild, "untimeout", member, itx.user, "Timeout removed")
        await itx.response.send_message(f"Removed timeout from {member.mention}.", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, itx, member: discord.Member, reason: str = "No reason provided"):
        n = await self._log_case(itx.guild, "kick", member, itx.user, reason)
        await self._dm_user(itx.guild, member, "kick", reason, case_id=n, moderator=str(itx.user))
        try: await member.kick(reason=reason)
        except discord.Forbidden: return await itx.response.send_message("I lack permissions.", ephemeral=True)
        await itx.response.send_message(f"Kicked {member} — case #{n}.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, itx, member: discord.Member, reason: str = "No reason provided", delete_message_days: int = 0):
        n = await self._log_case(itx.guild, "ban", member, itx.user, reason)
        await self._dm_user(itx.guild, member, "ban", reason, case_id=n, moderator=str(itx.user))
        try: await itx.guild.ban(member, reason=reason, delete_message_days=min(max(delete_message_days, 0), 7))
        except discord.Forbidden: return await itx.response.send_message("I lack permissions.", ephemeral=True)
        await itx.response.send_message(f"Banned {member} — case #{n}.", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, itx, user_id: str, reason: str = "No reason provided"):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await itx.guild.unban(user, reason=reason)
        except Exception as e:
            return await itx.response.send_message(f"Unban failed: {e}", ephemeral=True)
        await self._log_case(itx.guild, "unban", user, itx.user, reason)
        await itx.response.send_message(f"Unbanned {user}.", ephemeral=True)

    @app_commands.command(name="clear", description="Bulk delete recent messages")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, itx, count: int):
        if count < 1 or count > 100:
            return await itx.response.send_message("count must be 1..100", ephemeral=True)
        await itx.response.defer(ephemeral=True)
        deleted = await itx.channel.purge(limit=count)
        await itx.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

    @app_commands.command(name="slowmode", description="Set slowmode (seconds, 0 to disable)")
    @app_commands.default_permissions(manage_channels=True)
    async def slowmode(self, itx, seconds: int):
        await itx.channel.edit(slowmode_delay=max(0, min(seconds, 21600)))
        await itx.response.send_message(f"Slowmode set to {seconds}s.", ephemeral=True)

    @app_commands.command(name="lock", description="Lock the current channel")
    @app_commands.default_permissions(manage_channels=True)
    async def lock(self, itx):
        o = itx.channel.overwrites_for(itx.guild.default_role); o.send_messages = False
        await itx.channel.set_permissions(itx.guild.default_role, overwrite=o)
        await itx.response.send_message("Channel locked.", ephemeral=True)

    @app_commands.command(name="unlock", description="Unlock the current channel")
    @app_commands.default_permissions(manage_channels=True)
    async def unlock(self, itx):
        o = itx.channel.overwrites_for(itx.guild.default_role); o.send_messages = None
        await itx.channel.set_permissions(itx.guild.default_role, overwrite=o)
        await itx.response.send_message("Channel unlocked.", ephemeral=True)

    @app_commands.command(name="case", description="View a case by number")
    async def case(self, itx, case_number: int):
        c = await get_db()[COLL["cases"]].find_one({"guild_id": str(itx.guild.id), "case_number": case_number}, {"_id": 0})
        if not c: return await itx.response.send_message("Not found.", ephemeral=True)
        e = discord.Embed(title=f"Case #{case_number} — {c['action'].upper()}", color=0x00F0FF)
        e.add_field(name="User", value=f"<@{c['target_id']}>", inline=True)
        e.add_field(name="Moderator", value=f"<@{c['moderator_id']}>", inline=True)
        e.add_field(name="Reason", value=c.get("reason", "—"), inline=False)
        if c.get("duration_seconds"): e.add_field(name="Duration", value=_fmt_duration(c["duration_seconds"]))
        e.timestamp = datetime.fromisoformat(c["created_at"])
        await itx.response.send_message(embed=e, ephemeral=True)


# ============================== AutoMod ================================
class AutoModCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        db = get_db()
        rules = await db[COLL["automod_rules"]].find({"guild_id": str(message.guild.id), "enabled": True}, {"_id": 0}).to_list(100)
        if not rules: return
        role_ids = [str(r.id) for r in message.author.roles] if isinstance(message.author, discord.Member) else []
        for rule in rules:
            if is_exempt(rule, str(message.author.id), role_ids, str(message.channel.id)): continue
            m = check_message(message.content or "", rule)
            if m:
                await self._enforce(message, rule, m); break

    async def _enforce(self, message, rule, match):
        a = rule.get("actions") or {}
        reason = f"AutoMod: {match.reason}"
        if a.get("delete", True):
            try: await message.delete()
            except Exception: pass
        mod_cog: ModerationCog = self.bot.get_cog("ModerationCog")
        if a.get("warn") and mod_cog and isinstance(message.author, discord.Member):
            await mod_cog._log_case(message.guild, "warn", message.author, self.bot.user, reason)
        if a.get("dm"):
            try:
                text = _render_vars(rule.get("dm_template", ""), server=message.guild.name,
                                    rule=rule.get("name", rule.get("kind")), reason=match.reason,
                                    user=message.author.mention, username=str(message.author),
                                    channel=f"#{message.channel.name}")
                await message.author.send(text)
            except Exception: pass
        secs = int(a.get("timeout_seconds") or 0)
        if secs and isinstance(message.author, discord.Member):
            try:
                await message.author.timeout(datetime.now(timezone.utc) + timedelta(seconds=secs), reason=reason)
                if mod_cog:
                    await mod_cog._log_case(message.guild, "timeout", message.author, self.bot.user, reason, secs)
            except Exception: pass
        if a.get("kick") and isinstance(message.author, discord.Member):
            try: await message.author.kick(reason=reason)
            except Exception: pass
        if a.get("ban") and isinstance(message.author, discord.Member):
            try: await message.author.ban(reason=reason)
            except Exception: pass
        db = get_db()
        await db[COLL["events"]].insert_one({
            "guild_id": str(message.guild.id), "type": "automod",
            "rule_kind": rule["kind"], "rule_name": rule.get("name"),
            "user_id": str(message.author.id), "user_tag": str(message.author),
            "content_snippet": (message.content or "")[:300],
            "matched": match.matched, "reason": match.reason,
            "created_at": _now_iso()})
        logs = await db[COLL["logging"]].find_one({"guild_id": str(message.guild.id)}) or {}
        cat = (logs.get("categories") or {}).get("automod") or {}
        if cat.get("enabled") and cat.get("channel_id"):
            ch = message.guild.get_channel(int(cat["channel_id"]))
            if ch:
                e = discord.Embed(title=f"AutoMod — {rule.get('name', rule['kind'])}", color=0xEF4444)
                e.add_field(name="User", value=message.author.mention)
                e.add_field(name="Channel", value=message.channel.mention)
                e.add_field(name="Reason", value=match.reason, inline=False)
                if message.content:
                    e.add_field(name="Message", value=message.content[:1000], inline=False)
                try: await ch.send(embed=e)
                except Exception: pass


# ============================== Welcome/Goodbye ========================
class WelcomeGoodbyeCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _send(self, member: discord.Member, kind: str):
        db = get_db()
        cfg = await db[COLL[kind]].find_one({"guild_id": str(member.guild.id)}) or {}
        if not cfg.get("enabled"): return
        vars = dict(user=member.mention, username=str(member),
                    server=member.guild.name, member_count=member.guild.member_count)
        text = _render_vars(cfg.get("text") or "", **vars)
        embed_cfg = cfg.get("embed") or {}
        embed = _build_embed(embed_cfg, **vars) if embed_cfg.get("enabled", True) else None
        if cfg.get("channel_id"):
            ch = member.guild.get_channel(int(cfg["channel_id"]))
            if ch:
                try: await ch.send(content=text or None, embed=embed)
                except Exception: pass
        if cfg.get("dm_enabled") and kind == "welcome":
            try: await member.send(_render_vars(cfg.get("dm_text") or "", **vars))
            except Exception: pass


# ============================== Auto Roles + Verification ==============
class VerificationView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Verify", style=discord.ButtonStyle.success, custom_id=f"1gc:verify:{guild_id}"))


class AutoRolesCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = get_db()
        cfg = await db[COLL["autoroles"]].find_one({"guild_id": str(member.guild.id)}) or {}
        if not cfg.get("enabled"): return
        if cfg.get("only_after_verification"): return  # applied on verify
        role_ids = cfg.get("bot_role_ids") if member.bot else cfg.get("role_ids")
        await asyncio.sleep(int(cfg.get("delay_seconds") or 0))
        for rid in role_ids or []:
            role = member.guild.get_role(int(rid))
            if role:
                try: await member.add_roles(role, reason="Auto role")
                except Exception: pass

    @commands.Cog.listener()
    async def on_interaction(self, itx: discord.Interaction):
        if itx.type != discord.InteractionType.component: return
        cid = (itx.data or {}).get("custom_id") or ""
        if not cid.startswith("1gc:verify:"): return
        gid = cid.split(":", 2)[2]
        db = get_db()
        vcfg = await db[COLL["verification"]].find_one({"guild_id": gid}) or {}
        if not vcfg.get("enabled"):
            return await itx.response.send_message("Verification is not active.", ephemeral=True)
        # min account age
        min_days = int(vcfg.get("min_account_age_days") or 0)
        if min_days and itx.user.created_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(days=min_days):
            await itx.response.send_message(_render_vars(vcfg.get("fail_message") or "Failed.", user=itx.user.mention, server=itx.guild.name), ephemeral=True)
            if vcfg.get("kick_on_fail"):
                try: await itx.user.kick(reason="Verification failed: account too new")
                except Exception: pass
            return
        role_id = vcfg.get("verified_role_id")
        if role_id:
            role = itx.guild.get_role(int(role_id))
            if role:
                try: await itx.user.add_roles(role, reason="Verification")
                except Exception: pass
        # apply post-verification auto roles
        ar = await db[COLL["autoroles"]].find_one({"guild_id": gid}) or {}
        if ar.get("enabled") and ar.get("only_after_verification"):
            for rid in ar.get("role_ids", []):
                role = itx.guild.get_role(int(rid))
                if role:
                    try: await itx.user.add_roles(role, reason="Post-verify auto role")
                    except Exception: pass
        await itx.response.send_message(_render_vars(vcfg.get("success_message") or "Verified.", user=itx.user.mention, server=itx.guild.name), ephemeral=True)
        if vcfg.get("log_channel_id"):
            ch = itx.guild.get_channel(int(vcfg["log_channel_id"]))
            if ch:
                try: await ch.send(f"✅ {itx.user.mention} verified.")
                except Exception: pass


# ============================== Raid Protection ========================
class RaidCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.joins: dict[int, list[datetime]] = {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await get_db()[COLL["raid"]].find_one({"guild_id": str(member.guild.id)}) or {}
        if not cfg.get("enabled", True): return
        now = datetime.now(timezone.utc)
        window = int(cfg.get("join_burst_window_seconds") or 10)
        thr = int(cfg.get("join_burst_threshold") or 8)
        buf = self.joins.setdefault(member.guild.id, [])
        buf.append(now)
        cutoff = now - timedelta(seconds=window)
        buf[:] = [t for t in buf if t >= cutoff]
        # act on new account
        new_days = int(cfg.get("new_account_days") or 0)
        is_new = new_days and member.created_at.replace(tzinfo=timezone.utc) > now - timedelta(days=new_days)
        if len(buf) >= thr:
            await self._respond(member.guild, cfg, len(buf), window, thr)
            buf.clear()
        elif is_new and cfg.get("action") == "kick_new":
            try: await member.kick(reason="Raid: new account during raid window")
            except Exception: pass

    async def _respond(self, guild: discord.Guild, cfg: dict, count: int, window: int, threshold: int):
        action = cfg.get("action", "verify")
        if cfg.get("alert_channel_id"):
            ch = guild.get_channel(int(cfg["alert_channel_id"]))
            if ch:
                msg = _render_vars(cfg.get("alert_message") or "", count=count, window=window,
                                    threshold=threshold, action=action)
                try: await ch.send(msg)
                except Exception: pass
        if action == "lockdown":
            for ch in guild.text_channels[:50]:
                try:
                    o = ch.overwrites_for(guild.default_role); o.send_messages = False
                    await ch.set_permissions(guild.default_role, overwrite=o)
                except Exception: pass


# ============================== Ticket System ==============================
class TicketButton(discord.ui.Button):
    def __init__(self, panel_id: str, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"1gc:ticket:{panel_id}", emoji="📩")


class TicketPanelView(discord.ui.View):
    def __init__(self, panel: dict):
        super().__init__(timeout=None)
        sm = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
              "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger}
        self.add_item(TicketButton(panel["id"], panel.get("button_label", "Open Ticket"),
                                    sm.get(panel.get("button_style", "primary"), discord.ButtonStyle.primary)))


class TicketFormModal(discord.ui.Modal):
    def __init__(self, panel: dict):
        super().__init__(title=(panel.get("name") or "Support")[:45])
        self.panel = panel
        self.answers: dict[str, str] = {}
        for q in (panel.get("form_questions") or [])[:5]:
            self.add_item(discord.ui.TextInput(
                label=q["label"][:45], placeholder=(q.get("placeholder") or "")[:100],
                required=bool(q.get("required", True)),
                style=discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short,
                custom_id=q.get("id", q["label"][:20]), max_length=1000))

    async def on_submit(self, itx: discord.Interaction):
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                self.answers[child.label] = child.value
        await _open_ticket(itx, self.panel, self.answers)


class CloseReasonModal(discord.ui.Modal, title="Close ticket"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, panel_id: str):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, itx: discord.Interaction):
        await _close_ticket(itx, self.panel_id, self.reason.value or "No reason provided")


class AddUserModal(discord.ui.Modal, title="Add user to ticket"):
    uid = discord.ui.TextInput(label="User ID", required=True, max_length=25)

    async def on_submit(self, itx: discord.Interaction):
        try:
            member = itx.guild.get_member(int(str(self.uid.value).strip())) or await itx.guild.fetch_member(int(str(self.uid.value).strip()))
            await itx.channel.set_permissions(member, view_channel=True, send_messages=True)
            await itx.response.send_message(f"Added {member.mention} to this ticket.")
        except Exception as e:
            await itx.response.send_message(f"Failed: {e}", ephemeral=True)


class RenameModal(discord.ui.Modal, title="Rename ticket"):
    name = discord.ui.TextInput(label="New name", required=True, max_length=90)

    async def on_submit(self, itx: discord.Interaction):
        try:
            await itx.channel.edit(name=str(self.name.value)[:90])
            await itx.response.send_message(f"Renamed to `{self.name.value}`.")
        except Exception as e:
            await itx.response.send_message(f"Failed: {e}", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self, panel_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Claim", style=discord.ButtonStyle.secondary, custom_id=f"1gc:tclaim:{panel_id}", emoji="🙋"))
        self.add_item(discord.ui.Button(label="Add User", style=discord.ButtonStyle.secondary, custom_id=f"1gc:tadd:{panel_id}", emoji="➕"))
        self.add_item(discord.ui.Button(label="Rename", style=discord.ButtonStyle.secondary, custom_id=f"1gc:trename:{panel_id}", emoji="✏️"))
        self.add_item(discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, custom_id=f"1gc:tclose:{panel_id}", emoji="🔒"))


class ClosedTicketView(discord.ui.View):
    def __init__(self, panel_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Reopen", style=discord.ButtonStyle.success, custom_id=f"1gc:treopen:{panel_id}", emoji="🔓"))
        self.add_item(discord.ui.Button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id=f"1gc:tscript:{panel_id}", emoji="📄"))
        self.add_item(discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger, custom_id=f"1gc:tdel:{panel_id}", emoji="🗑️"))


async def _open_ticket(itx: discord.Interaction, panel: dict, answers: dict):
    guild = itx.guild
    db = get_db()
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        itx.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
    }
    for rid in (panel.get("support_role_ids") or []):
        role = guild.get_role(int(rid))
        if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
    category = guild.get_channel(int(panel["category_id"])) if panel.get("category_id") else None
    ch_name = f"ticket-{itx.user.name}"[:90]
    try:
        channel = await guild.create_text_channel(
            ch_name, category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites, reason=f"Ticket by {itx.user}")
    except discord.Forbidden:
        return await itx.response.send_message("I lack permission to create ticket channels.", ephemeral=True)

    ticket_doc = {"id": f"t-{channel.id}", "guild_id": str(guild.id), "panel_id": panel["id"],
                  "channel_id": str(channel.id), "opener_id": str(itx.user.id), "opener_tag": str(itx.user),
                  "status": "open", "form_answers": answers or {}, "ai_messages": [],
                  "ai_active": bool(panel.get("ai_support_enabled")),
                  "transcript": [], "created_at": _now_iso()}
    await db[COLL["tickets"]].insert_one(ticket_doc)

    e = discord.Embed(
        title=f"Ticket — {panel.get('name', 'Support')}",
        description=_render_vars(panel.get("opening_message") or "", user=itx.user.mention, server=guild.name),
        color=0x00F0FF)
    if answers:
        for k, v in answers.items():
            e.add_field(name=k, value=v[:1000] or "—", inline=False)
    e.set_footer(text=f"Ticket ID: {channel.id}")
    support_mentions = " ".join(f"<@&{r}>" for r in (panel.get("support_role_ids") or []))
    view = TicketControlView(panel["id"])
    await channel.send(content=f"{itx.user.mention} {support_mentions}".strip(), embed=e, view=view)
    await itx.response.send_message(f"Ticket opened: {channel.mention}", ephemeral=True)


async def _close_ticket(itx: discord.Interaction, panel_id: str, reason: str):
    db = get_db()
    t = await db[COLL["tickets"]].find_one({"channel_id": str(itx.channel.id)})
    if not t:
        return await itx.response.send_message("Not a ticket.", ephemeral=True)
    # capture transcript
    lines = []
    try:
        async for m in itx.channel.history(limit=500, oldest_first=True):
            lines.append({"ts": m.created_at.isoformat(), "author": f"{m.author}",
                            "content": (m.content or "").replace("\n", "  ")})
    except Exception: pass
    await db[COLL["tickets"]].update_one(
        {"channel_id": str(itx.channel.id)},
        {"$set": {"status": "closed", "closed_at": _now_iso(), "close_reason": reason,
                    "closed_by": str(itx.user.id), "transcript": lines}})
    panel = await db[COLL["ticket_panels"]].find_one({"id": t["panel_id"]}) or {}
    # Build transcript text + file once — reused for archive channel and opener DM
    txt = "\n".join(f"[{l['ts']}] {l['author']}: {l['content']}" for l in lines) or "(empty)"
    import io
    def _new_file():
        return discord.File(fp=io.BytesIO(txt.encode()),
                             filename=f"transcript-{itx.channel.name}-{itx.channel.id}.txt")
    # Archive transcript to log channel as an embed + attachment
    if panel.get("log_channel_id"):
        ch = itx.guild.get_channel(int(panel["log_channel_id"]))
        if ch:
            e = discord.Embed(title="Ticket closed — transcript archived", color=0xEF4444,
                                timestamp=datetime.now(timezone.utc))
            e.add_field(name="Ticket", value=f"#{itx.channel.name}")
            e.add_field(name="Panel", value=panel.get("name", "—"))
            e.add_field(name="Opened by", value=f"<@{t['opener_id']}>")
            e.add_field(name="Closed by", value=itx.user.mention)
            e.add_field(name="Reason", value=reason, inline=False)
            e.set_footer(text=f"Messages captured: {len(lines)}")
            try: await ch.send(embed=e, file=_new_file())
            except Exception:
                try: await ch.send(embed=e)  # attachment may fail if too large
                except Exception: pass
    # DM the transcript to the ticket opener
    try:
        opener = itx.guild.get_member(int(t["opener_id"]))
        if not opener:
            try:
                opener = await itx.client.fetch_user(int(t["opener_id"]))
            except Exception:
                opener = None
        if opener:
            dm_embed = discord.Embed(
                title=f"Your ticket in {itx.guild.name} was closed",
                description=f"Panel: **{panel.get('name', 'Support')}**\nReason: {reason}",
                color=0xEF4444, timestamp=datetime.now(timezone.utc))
            dm_embed.set_footer(text=f"Messages captured: {len(lines)}")
            await opener.send(embed=dm_embed, file=_new_file())
    except Exception as e:
        print(f"[ticket close] DM opener failed: {e}")
    await itx.response.send_message(
        embed=discord.Embed(title="Ticket closed", description=reason, color=0xEF4444),
        view=ClosedTicketView(panel_id))
    try:
        overwrite = itx.channel.overwrites_for(itx.guild.get_member(int(t["opener_id"])))
        overwrite.send_messages = False
        await itx.channel.set_permissions(itx.guild.get_member(int(t["opener_id"])), overwrite=overwrite)
    except Exception: pass


class TicketsCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        db = get_db()
        t = await db[COLL["tickets"]].find_one(
            {"channel_id": str(message.channel.id), "status": {"$in": ["open", "claimed"]}},
            {"_id": 0})
        if not t:
            return
        # Append to transcript
        await db[COLL["tickets"]].update_one(
            {"channel_id": str(message.channel.id)},
            {"$push": {"transcript": {"ts": message.created_at.isoformat(),
                                        "author": str(message.author),
                                        "content": (message.content or "")[:2000]}}})

    @commands.Cog.listener()
    async def on_interaction(self, itx: discord.Interaction):
        if itx.type != discord.InteractionType.component:
            return
        cid = (itx.data or {}).get("custom_id") or ""
        if not cid.startswith("1gc:"):
            return
        parts = cid.split(":")
        if len(parts) < 3 or parts[1] not in {
            "ticket", "tclaim", "tclose", "tadd", "trename",
            "treopen", "tscript", "tdel"
        }:
            return
        action = parts[1]; panel_id = parts[2]
        db = get_db()
        if action == "ticket":
            panel = await db[COLL["ticket_panels"]].find_one({"id": panel_id}, {"_id": 0})
            if not panel:
                return await itx.response.send_message("Panel not found.", ephemeral=True)
            if panel.get("form_enabled") and (panel.get("form_questions") or []):
                await itx.response.send_modal(TicketFormModal(panel))
            else:
                await _open_ticket(itx, panel, {})
        elif action == "tclaim":
            await db[COLL["tickets"]].update_one(
                {"channel_id": str(itx.channel.id)},
                {"$set": {"claimed_by": str(itx.user.id), "status": "claimed"}})
            await itx.response.send_message(f"🙋 Ticket claimed by {itx.user.mention}.")
        elif action == "tclose":
            await itx.response.send_modal(CloseReasonModal(panel_id))
        elif action == "tadd":
            await itx.response.send_modal(AddUserModal())
        elif action == "trename":
            await itx.response.send_modal(RenameModal())
        elif action == "treopen":
            await db[COLL["tickets"]].update_one(
                {"channel_id": str(itx.channel.id)},
                {"$set": {"status": "open", "closed_at": None}})
            try:
                t = await db[COLL["tickets"]].find_one({"channel_id": str(itx.channel.id)})
                member = itx.guild.get_member(int(t["opener_id"]))
                if member:
                    o = itx.channel.overwrites_for(member); o.send_messages = True
                    await itx.channel.set_permissions(member, overwrite=o)
            except Exception: pass
            await itx.response.send_message("🔓 Ticket reopened.", view=TicketControlView(panel_id))
        elif action == "tscript":
            t = await db[COLL["tickets"]].find_one({"channel_id": str(itx.channel.id)}, {"_id": 0})
            if not t or not t.get("transcript"):
                return await itx.response.send_message("No transcript.", ephemeral=True)
            txt = "\n".join(f"[{l['ts']}] {l['author']}: {l['content']}" for l in t["transcript"])
            import io
            f = discord.File(fp=io.BytesIO(txt.encode()), filename=f"transcript-{itx.channel.name}.txt")
            await itx.response.send_message(file=f)
        elif action == "tdel":
            await itx.response.send_message("Deleting in 3 seconds…")
            await asyncio.sleep(3)
            try: await itx.channel.delete(reason="Ticket deleted")
            except Exception: pass


# ============================== Starboard ==============================
class StarboardCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id: return
        db = get_db()
        cfg = await db[COLL["starboard"]].find_one({"guild_id": str(payload.guild_id)}) or {}
        if not cfg.get("enabled") or not cfg.get("channel_id"): return
        if str(payload.emoji) != cfg.get("emoji", "⭐"): return
        if str(payload.channel_id) in (cfg.get("ignored_channel_ids") or []): return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        ch = guild.get_channel(payload.channel_id)
        if not ch: return
        try: msg = await ch.fetch_message(payload.message_id)
        except Exception: return
        if msg.author.bot and cfg.get("ignore_bots", True): return
        thr = int(cfg.get("threshold") or 5)
        r = next((r for r in msg.reactions if str(r.emoji) == cfg.get("emoji", "⭐")), None)
        if not r or r.count < thr: return
        # dedupe
        existing = await db[COLL["starboard_posts"]].find_one({"guild_id": str(payload.guild_id), "message_id": str(msg.id)})
        star_ch = guild.get_channel(int(cfg["channel_id"]))
        if not star_ch: return
        e = discord.Embed(description=msg.content or "", color=0xF59E0B, timestamp=msg.created_at)
        e.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
        e.add_field(name="Source", value=f"[Jump to message]({msg.jump_url})")
        if msg.attachments:
            e.set_image(url=msg.attachments[0].url)
        if existing:
            try:
                post = await star_ch.fetch_message(int(existing["post_id"]))
                await post.edit(content=f"{cfg.get('emoji','⭐')} **{r.count}** — in {ch.mention}", embed=e)
            except Exception: pass
        else:
            post = await star_ch.send(content=f"{cfg.get('emoji','⭐')} **{r.count}** — in {ch.mention}", embed=e)
            await db[COLL["starboard_posts"]].insert_one({
                "guild_id": str(payload.guild_id), "message_id": str(msg.id),
                "post_id": str(post.id), "created_at": _now_iso()})


# ============================== Giveaways ==============================
class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Enter", style=discord.ButtonStyle.success,
                                          custom_id=f"1gc:gwenter:{giveaway_id}", emoji="🎉"))


class GiveawayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.ticker.start()

    def cog_unload(self):
        self.ticker.cancel()

    @tasks.loop(seconds=30)
    async def ticker(self):
        try:
            db = get_db()
            now = datetime.now(timezone.utc)
            async for gv in db[COLL["giveaways"]].find({"status": "active"}):
                ends = datetime.fromisoformat(gv["ends_at"])
                if ends <= now:
                    await end_giveaway(self.bot, gv["guild_id"], gv["id"])
        except Exception: pass

    @ticker.before_loop
    async def _before(self): await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_interaction(self, itx: discord.Interaction):
        if itx.type != discord.InteractionType.component: return
        cid = (itx.data or {}).get("custom_id") or ""
        if not cid.startswith("1gc:gwenter:"): return
        gid = cid.split(":", 2)[2]
        db = get_db()
        gv = await db[COLL["giveaways"]].find_one({"id": gid}) or {}
        if gv.get("status") != "active":
            return await itx.response.send_message("This giveaway has ended.", ephemeral=True)
        # check requirements
        member: discord.Member = itx.user  # type: ignore
        req_roles = set(gv.get("required_role_ids") or [])
        blk_roles = set(gv.get("blocked_role_ids") or [])
        role_ids = {str(r.id) for r in member.roles}
        if req_roles and not (req_roles & role_ids):
            return await itx.response.send_message("You don't have the required role.", ephemeral=True)
        if blk_roles & role_ids:
            return await itx.response.send_message("Your role is blocked from this giveaway.", ephemeral=True)
        min_acc = int(gv.get("min_account_age_days") or 0)
        if min_acc and member.created_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(days=min_acc):
            return await itx.response.send_message("Your account is too new.", ephemeral=True)
        min_mem = int(gv.get("min_membership_days") or 0)
        if min_mem and member.joined_at and member.joined_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(days=min_mem):
            return await itx.response.send_message("You have not been in the server long enough.", ephemeral=True)
        # bonus entries: 1 base + 1 per matching bonus role
        entries = 1 + sum(1 for r in (gv.get("bonus_entry_role_ids") or []) if r in role_ids)
        existing = await db[COLL["giveaway_entries"]].find_one({"giveaway_id": gid, "user_id": str(member.id)})
        if existing:
            return await itx.response.send_message(f"Already entered ({existing.get('entries',1)} entries).", ephemeral=True)
        await db[COLL["giveaway_entries"]].insert_one({
            "giveaway_id": gid, "user_id": str(member.id), "entries": entries,
            "created_at": _now_iso()})
        await itx.response.send_message(f"🎉 Entered! ({entries} entries)", ephemeral=True)


async def start_giveaway(bot, guild_id: str, data: dict):
    guild = bot.get_guild(int(guild_id))
    if not guild: raise ValueError("Bot not in guild")
    ch = guild.get_channel(int(data["channel_id"]))
    if not ch: raise ValueError("Channel not found")
    gid = f"gw-{int(datetime.now(timezone.utc).timestamp())}-{random.randint(100,999)}"
    ends = datetime.now(timezone.utc) + timedelta(minutes=int(data.get("duration_minutes") or 60))
    text = _render_vars(data.get("announcement_text") or "", prize=data["prize"],
                          winners=data["winners"], ends_ts=int(ends.timestamp()))
    embed = discord.Embed(title=f"🎉 Giveaway: {data['prize']}", description=text, color=0xF59E0B, timestamp=ends)
    embed.set_footer(text=f"Winners: {data['winners']} · Ends")
    view = GiveawayView(gid)
    msg = await ch.send(embed=embed, view=view)
    doc = {**data, "id": gid, "guild_id": guild_id, "channel_id": str(ch.id),
            "message_id": str(msg.id), "status": "active", "created_at": _now_iso(),
            "ends_at": ends.isoformat()}
    await get_db()[COLL["giveaways"]].insert_one(doc)
    bot.add_view(view)
    doc.pop("_id", None)
    return doc


async def end_giveaway(bot, guild_id: str, giveaway_id: str):
    db = get_db()
    gv = await db[COLL["giveaways"]].find_one({"id": giveaway_id, "guild_id": guild_id})
    if not gv or gv.get("status") != "active":
        return {"ok": False, "reason": "not active"}
    entries = await db[COLL["giveaway_entries"]].find({"giveaway_id": giveaway_id}).to_list(10000)
    pool = []
    for e in entries:
        pool.extend([e["user_id"]] * int(e.get("entries") or 1))
    random.shuffle(pool)
    winners_needed = int(gv.get("winners") or 1)
    picked: List[str] = []
    for uid in pool:
        if uid not in picked:
            picked.append(uid)
        if len(picked) >= winners_needed: break
    guild = bot.get_guild(int(guild_id))
    ch = guild.get_channel(int(gv["channel_id"])) if guild else None
    if ch and picked:
        mentions = " ".join(f"<@{u}>" for u in picked)
        await ch.send(f"🎉 Winners of **{gv['prize']}**: {mentions}")
        if gv.get("winner_dm_enabled"):
            for uid in picked:
                m = guild.get_member(int(uid))
                if m:
                    try:
                        await m.send(_render_vars(gv.get("winner_dm_text") or "",
                                                   user=m.mention, username=str(m),
                                                   prize=gv["prize"], server=guild.name))
                    except Exception: pass
    elif ch:
        await ch.send(f"🎉 No valid entries for **{gv['prize']}**.")
    await db[COLL["giveaways"]].update_one({"id": giveaway_id},
                                             {"$set": {"status": "ended", "winners_ids": picked,
                                                        "ended_at": _now_iso()}})
    return {"ok": True, "winners": picked}


async def reroll_giveaway(bot, guild_id: str, giveaway_id: str):
    db = get_db()
    gv = await db[COLL["giveaways"]].find_one({"id": giveaway_id, "guild_id": guild_id})
    if not gv: return {"ok": False}
    entries = await db[COLL["giveaway_entries"]].find({"giveaway_id": giveaway_id}).to_list(10000)
    prev = set(gv.get("winners_ids") or [])
    pool = [e["user_id"] for e in entries if e["user_id"] not in prev]
    if not pool: return {"ok": False, "reason": "no candidates"}
    winner = random.choice(pool)
    guild = bot.get_guild(int(guild_id))
    ch = guild.get_channel(int(gv["channel_id"])) if guild else None
    if ch:
        await ch.send(f"🎉 Reroll winner for **{gv['prize']}**: <@{winner}>")
    return {"ok": True, "winner": winner}


# ============================== Logging ================================
class LoggingCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def _log(self, guild, key, embed):
        cfg = await get_db()[COLL["logging"]].find_one({"guild_id": str(guild.id)}) or {}
        cat = (cfg.get("categories") or {}).get(key) or {}
        if not cat.get("enabled") or not cat.get("channel_id"): return
        ch = guild.get_channel(int(cat["channel_id"]))
        if ch:
            try: await ch.send(embed=embed)
            except Exception: pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not message.guild or message.author.bot: return
        e = discord.Embed(title="Message Deleted", color=0xEF4444, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Author", value=str(message.author))
        e.add_field(name="Channel", value=message.channel.mention)
        e.add_field(name="Content", value=(message.content or "—")[:1000], inline=False)
        await self._log(message.guild, "message_delete", e)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not after.guild or after.author.bot or before.content == after.content: return
        e = discord.Embed(title="Message Edited", color=0xF59E0B, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Author", value=str(after.author))
        e.add_field(name="Channel", value=after.channel.mention)
        e.add_field(name="Before", value=(before.content or "—")[:500], inline=False)
        e.add_field(name="After", value=(after.content or "—")[:500], inline=False)
        await self._log(after.guild, "message_edit", e)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        e = discord.Embed(title="Member Joined", color=0x10B981, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=f"{member} ({member.id})")
        e.add_field(name="Account Age", value=f"<t:{int(member.created_at.timestamp())}:R>")
        await self._log(member.guild, "member_join", e)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        e = discord.Embed(title="Member Left", color=0xEF4444, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=f"{member} ({member.id})")
        await self._log(member.guild, "member_leave", e)


# ============================== WG dispatcher =============================
class MemberFlowCog(commands.Cog):
    """Dispatches on_member_join to Welcome + AutoRoles + Raid, on_member_remove to Goodbye."""
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        wg = self.bot.get_cog("WelcomeGoodbyeCog")
        if wg: await wg._send(member, "welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        wg = self.bot.get_cog("WelcomeGoodbyeCog")
        if wg: await wg._send(member, "goodbye")


# ============================== Utilities ==============================
class UtilCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ping", description="Bot latency")
    async def ping(self, itx: discord.Interaction):
        await itx.response.send_message(f"Pong! `{round(self.bot.latency*1000)}ms`", ephemeral=True)

    @app_commands.command(name="serverinfo", description="Info about this server")
    async def serverinfo(self, itx: discord.Interaction):
        g = itx.guild
        e = discord.Embed(title=g.name, color=0x00F0FF)
        if g.icon: e.set_thumbnail(url=g.icon.url)
        e.add_field(name="Members", value=g.member_count)
        e.add_field(name="Roles", value=len(g.roles))
        e.add_field(name="Channels", value=len(g.channels))
        e.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:R>")
        await itx.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="userinfo", description="Info about a user")
    async def userinfo(self, itx: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or itx.user
        e = discord.Embed(title=str(member), color=member.color.value or 0x00F0FF)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="ID", value=str(member.id))
        e.add_field(name="Joined", value=f"<t:{int((member.joined_at or datetime.now(timezone.utc)).timestamp())}:R>")
        e.add_field(name="Account", value=f"<t:{int(member.created_at.timestamp())}:R>")
        await itx.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="avatar", description="Show a user's avatar")
    async def avatar(self, itx: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or itx.user
        e = discord.Embed(title=f"Avatar — {member}", color=0x00F0FF)
        e.set_image(url=member.display_avatar.url)
        await itx.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="membercount", description="Show member count")
    async def membercount(self, itx: discord.Interaction):
        await itx.response.send_message(f"Members: **{itx.guild.member_count}**", ephemeral=True)

    @app_commands.command(name="uptime", description="Bot uptime")
    async def uptime(self, itx: discord.Interaction):
        if not BOT_STARTED_AT: return await itx.response.send_message("Just starting up…", ephemeral=True)
        secs = int((datetime.now(timezone.utc) - BOT_STARTED_AT).total_seconds())
        await itx.response.send_message(f"Uptime: **{_fmt_duration(secs)}**", ephemeral=True)


# ============================== Custom Commands ========================
class CustomCommandCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot: return
        if not message.content.startswith("!"): return
        name = message.content[1:].split()[0].lower()
        cc = await get_db()[COLL["custom_commands"]].find_one({"guild_id": str(message.guild.id), "name": name, "enabled": True})
        if not cc: return
        if cc.get("channel_ids") and str(message.channel.id) not in cc["channel_ids"]: return
        role_ids = {str(r.id) for r in message.author.roles}
        if cc.get("required_role_ids") and not set(cc["required_role_ids"]) & role_ids: return
        if cc.get("delete_trigger"):
            try: await message.delete()
            except Exception: pass
        vars = dict(user=message.author.mention, username=str(message.author),
                     server=message.guild.name, channel=message.channel.mention)
        txt = _render_vars(cc.get("response_text") or "", **vars)
        embed = _build_embed(cc["response_embed"], **vars) if cc.get("response_embed") else None
        try: await message.channel.send(content=txt or None, embed=embed)
        except Exception: pass


# ============================== Runner =================================
async def build_bot() -> commands.Bot:
    bot = commands.Bot(command_prefix="!", intents=INTENTS)
    for cog in [SyncCog, ModerationCog, AutoModCog, WelcomeGoodbyeCog, MemberFlowCog,
                AutoRolesCog, RaidCog, TicketsCog, StarboardCog, GiveawayCog, LoggingCog,
                CustomCommandCog, UtilCog]:
        await bot.add_cog(cog(bot))
    return bot


async def run_bot():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("[1GC dashboard] No DISCORD_TOKEN"); return
    bot = await build_bot()
    try: await bot.start(token)
    except Exception as e: print(f"[1GC dashboard] Bot error: {e}")
