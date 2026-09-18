"""1GC(ian) dashboard — FastAPI backend + Discord bot supervisor."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from db import COLL, get_db  # noqa: E402
from ai_service import ticket_ai_reply, summarize_ticket, ai_setup_suggest  # noqa: E402
from default_templates import (  # noqa: E402
    DEFAULT_WELCOME, DEFAULT_GOODBYE, DEFAULT_MOD, DEFAULT_AUTOMOD_DM,
    DEFAULT_TICKET_PANEL, DEFAULT_VERIFICATION, DEFAULT_RAID, DEFAULT_AUTO_ROLES,
    DEFAULT_STARBOARD, DEFAULT_ANNOUNCEMENT, DEFAULT_CUSTOM_COMMAND,
    DEFAULT_AUTOMATION, AUTOMATION_TRIGGERS, AUTOMATION_CONDITIONS, AUTOMATION_ACTIONS,
    DEFAULT_GIVEAWAY,
)
import discord_bot  # noqa: E402

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="1GC(ian) dashboard API")
api = APIRouter(prefix="/api")

STARTED_AT = time.time()
_BOT = None
BOT_TASK: Optional[asyncio.Task] = None


# ============================== Guilds =================================
@api.get("/health")
async def health():
    return {"ok": True, "uptime": int(time.time() - STARTED_AT),
            "bot_ready": discord_bot.BOT_STARTED_AT is not None}


@api.get("/guilds")
async def list_guilds() -> List[dict]:
    db = get_db()
    return await db[COLL["guilds"]].find({}, {"_id": 0}).to_list(200)


@api.get("/guilds/{gid}")
async def get_guild(gid: str):
    db = get_db()
    g = await db[COLL["guilds"]].find_one({"id": gid}, {"_id": 0})
    if not g:
        raise HTTPException(404, "Guild not found")
    return g


@api.get("/guilds/{gid}/channels")
async def get_channels(gid: str):
    return await get_db()[COLL["channels"]].find({"guild_id": gid}, {"_id": 0}).to_list(500)


@api.get("/guilds/{gid}/roles")
async def get_roles(gid: str):
    return await get_db()[COLL["roles"]].find({"guild_id": gid}, {"_id": 0}).sort("position", -1).to_list(500)


@api.get("/guilds/{gid}/stats")
async def guild_stats(gid: str):
    db = get_db()
    g = await db[COLL["guilds"]].find_one({"id": gid}, {"_id": 0}) or {}
    tickets_open = await db[COLL["tickets"]].count_documents({"guild_id": gid, "status": {"$in": ["open", "claimed"]}})
    tickets_closed = await db[COLL["tickets"]].count_documents({"guild_id": gid, "status": "closed"})
    mod_actions = await db[COLL["cases"]].count_documents({"guild_id": gid})
    automod_events = await db[COLL["events"]].count_documents({"guild_id": gid, "type": "automod"})
    giveaways_active = await db[COLL["giveaways"]].count_documents({"guild_id": gid, "status": "active"})
    recent_events = await db[COLL["events"]].find({"guild_id": gid}, {"_id": 0}).sort("created_at", -1).to_list(15)
    return {
        "member_count": g.get("member_count", 0),
        "mod_actions": mod_actions,
        "automod_actions": automod_events,
        "tickets_open": tickets_open,
        "tickets_closed": tickets_closed,
        "giveaways_active": giveaways_active,
        "bot_uptime_seconds": int(time.time() - STARTED_AT),
        "recent_events": recent_events,
    }


# ============================== Moderation =============================
@api.get("/guilds/{gid}/cases")
async def list_cases(gid: str, limit: int = 100):
    return await get_db()[COLL["cases"]].find({"guild_id": gid}, {"_id": 0}).sort("case_number", -1).to_list(limit)


class ModSettingsIn(BaseModel):
    escalation: Optional[List[Dict[str, Any]]] = None
    dm_on_warn: Optional[bool] = None
    dm_on_timeout: Optional[bool] = None
    dm_on_kick: Optional[bool] = None
    dm_on_ban: Optional[bool] = None
    warn_dm_template: Optional[str] = None
    timeout_dm_template: Optional[str] = None
    kick_dm_template: Optional[str] = None
    ban_dm_template: Optional[str] = None
    log_channel_id: Optional[str] = None


@api.get("/guilds/{gid}/moderation")
async def get_mod_settings(gid: str):
    d = await get_db()[COLL["mod_settings"]].find_one({"guild_id": gid}, {"_id": 0})
    merged = {"guild_id": gid, **DEFAULT_MOD}
    if d:
        merged.update({k: v for k, v in d.items() if v is not None})
    return merged


@api.put("/guilds/{gid}/moderation")
async def put_mod_settings(gid: str, body: ModSettingsIn):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updates["guild_id"] = gid
    await get_db()[COLL["mod_settings"]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await get_mod_settings(gid)


# ============================== AutoMod ================================
DEFAULT_AUTOMOD_RULES = [
    {"kind": "profanity", "name": "Profanity Filter", "enabled": True, "sensitivity": 5, "threshold": 0,
     "actions": {"delete": True, "warn": True, "dm": True, "timeout_seconds": 0, "log": True}},
    {"kind": "slurs", "name": "Hate Speech & Slurs", "enabled": True, "sensitivity": 8, "threshold": 0,
     "actions": {"delete": True, "warn": True, "dm": True, "timeout_seconds": 600, "log": True, "alert_staff": True}},
    {"kind": "insults", "name": "Toxic Insults & Shortforms", "enabled": True, "threshold": 0,
     "actions": {"delete": True, "warn": True, "dm": True, "log": True}},
    {"kind": "invites", "name": "Discord Invites", "enabled": True, "threshold": 0,
     "actions": {"delete": True, "warn": True, "dm": True, "log": True}},
    {"kind": "mass_mentions", "name": "Mass Mentions", "enabled": True, "threshold": 5,
     "actions": {"delete": True, "timeout_seconds": 300, "log": True}},
    {"kind": "scam_links", "name": "Scam Detector", "enabled": True,
     "actions": {"delete": True, "warn": True, "log": True}},
]


async def _seed_automod(gid: str):
    db = get_db()
    for tpl in DEFAULT_AUTOMOD_RULES:
        if not await db[COLL["automod_rules"]].find_one({"guild_id": gid, "kind": tpl["kind"]}):
            doc = {"id": str(uuid.uuid4()), "guild_id": gid, "words": [], "regex": None,
                   "exempt_user_ids": [], "exempt_role_ids": [], "exempt_channel_ids": [],
                   "allowed_domains": [], "dm_template": DEFAULT_AUTOMOD_DM,
                   "created_at": datetime.now(timezone.utc).isoformat(), **tpl}
            await db[COLL["automod_rules"]].insert_one(doc)


@api.get("/guilds/{gid}/automod")
async def get_automod(gid: str):
    db = get_db()
    # Always run per-kind seeder — it's idempotent and adds any newly-shipped defaults
    await _seed_automod(gid)
    return await db[COLL["automod_rules"]].find({"guild_id": gid}, {"_id": 0}).to_list(200)


class AutomodRuleIn(BaseModel):
    kind: str
    name: str
    enabled: bool = True
    sensitivity: int = 5
    threshold: int = 5
    words: List[str] = []
    regex: Optional[str] = None
    actions: Dict[str, Any] = {}
    exempt_user_ids: List[str] = []
    exempt_role_ids: List[str] = []
    exempt_channel_ids: List[str] = []
    allowed_domains: List[str] = []
    dm_template: str = DEFAULT_AUTOMOD_DM


@api.post("/guilds/{gid}/automod")
async def create_automod(gid: str, body: AutomodRuleIn):
    db = get_db()
    doc = body.model_dump()
    doc.update({"id": str(uuid.uuid4()), "guild_id": gid,
                "created_at": datetime.now(timezone.utc).isoformat()})
    await db[COLL["automod_rules"]].insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/guilds/{gid}/automod/{rule_id}")
async def update_automod(gid: str, rule_id: str, body: AutomodRuleIn):
    db = get_db()
    await db[COLL["automod_rules"]].update_one({"guild_id": gid, "id": rule_id},
                                                {"$set": body.model_dump()})
    r = await db[COLL["automod_rules"]].find_one({"id": rule_id}, {"_id": 0})
    if not r:
        raise HTTPException(404, "Not found")
    return r


@api.delete("/guilds/{gid}/automod/{rule_id}")
async def delete_automod(gid: str, rule_id: str):
    await get_db()[COLL["automod_rules"]].delete_one({"guild_id": gid, "id": rule_id})
    return {"ok": True}


# ============================== Welcome / Goodbye ======================
class WGSettingsIn(BaseModel):
    enabled: bool = False
    channel_id: Optional[str] = None
    text: str = ""
    embed: Dict[str, Any] = {}
    dm_enabled: bool = False
    dm_text: str = ""


async def _get_wg(gid: str, kind: str):
    d = await get_db()[COLL[kind]].find_one({"guild_id": gid}, {"_id": 0})
    defaults = DEFAULT_WELCOME if kind == "welcome" else DEFAULT_GOODBYE
    merged = {"guild_id": gid, **defaults}
    if d:
        # only overlay non-empty values so defaults survive partial saves
        for k, v in d.items():
            if v in (None, "", [], {}):
                continue
            if k == "embed" and isinstance(v, dict):
                merged_embed = {**defaults["embed"], **{ek: ev for ek, ev in v.items() if ev not in (None, "", [], {})}}
                merged["embed"] = merged_embed
            else:
                merged[k] = v
    return merged


async def _put_wg(gid: str, kind: str, body: WGSettingsIn):
    updates = body.model_dump(); updates["guild_id"] = gid
    await get_db()[COLL[kind]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await _get_wg(gid, kind)


@api.get("/guilds/{gid}/welcome")
async def gw(gid: str): return await _get_wg(gid, "welcome")
@api.put("/guilds/{gid}/welcome")
async def pw(gid: str, body: WGSettingsIn): return await _put_wg(gid, "welcome", body)
@api.get("/guilds/{gid}/goodbye")
async def gg(gid: str): return await _get_wg(gid, "goodbye")
@api.put("/guilds/{gid}/goodbye")
async def pg(gid: str, body: WGSettingsIn): return await _put_wg(gid, "goodbye", body)


# ============================== Tickets ================================
class TicketPanelIn(BaseModel):
    name: str = DEFAULT_TICKET_PANEL["name"]
    channel_id: Optional[str] = None
    button_label: str = DEFAULT_TICKET_PANEL["button_label"]
    button_style: str = DEFAULT_TICKET_PANEL["button_style"]
    category_id: Optional[str] = None
    support_role_ids: List[str] = []
    opening_message: str = DEFAULT_TICKET_PANEL["opening_message"]
    closing_message: str = DEFAULT_TICKET_PANEL["closing_message"]
    ai_support_enabled: bool = False
    ai_knowledge_base: str = DEFAULT_TICKET_PANEL["ai_knowledge_base"]
    log_channel_id: Optional[str] = None
    form_enabled: bool = False
    form_questions: List[Dict[str, Any]] = []
    embed: Dict[str, Any] = {}


@api.get("/guilds/{gid}/ticket-panels")
async def list_panels(gid: str):
    return await get_db()[COLL["ticket_panels"]].find({"guild_id": gid}, {"_id": 0}).to_list(50)


@api.get("/guilds/{gid}/ticket-panels/defaults")
async def ticket_defaults(gid: str):
    """Return an untouched default panel scaffold for the 'new panel' modal."""
    return {
        **DEFAULT_TICKET_PANEL,
        "form_questions": DEFAULT_TICKET_PANEL["form_questions_default"],
    }


@api.post("/guilds/{gid}/ticket-panels")
async def create_panel(gid: str, body: TicketPanelIn):
    db = get_db()
    doc = body.model_dump()
    doc.update({"id": str(uuid.uuid4()), "guild_id": gid,
                "created_at": datetime.now(timezone.utc).isoformat(), "message_id": None})
    for q in doc.get("form_questions", []):
        q.setdefault("id", str(uuid.uuid4()))
    await db[COLL["ticket_panels"]].insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/guilds/{gid}/ticket-panels/{panel_id}")
async def update_panel(gid: str, panel_id: str, body: TicketPanelIn):
    db = get_db()
    updates = body.model_dump()
    for q in updates.get("form_questions", []):
        q.setdefault("id", str(uuid.uuid4()))
    await db[COLL["ticket_panels"]].update_one({"guild_id": gid, "id": panel_id}, {"$set": updates})
    p = await db[COLL["ticket_panels"]].find_one({"id": panel_id}, {"_id": 0})
    if not p:
        raise HTTPException(404, "Not found")
    return p


@api.delete("/guilds/{gid}/ticket-panels/{panel_id}")
async def delete_panel(gid: str, panel_id: str):
    await get_db()[COLL["ticket_panels"]].delete_one({"guild_id": gid, "id": panel_id})
    return {"ok": True}


@api.post("/guilds/{gid}/ticket-panels/{panel_id}/deploy")
async def deploy_panel(gid: str, panel_id: str):
    import discord
    db = get_db()
    panel = await db[COLL["ticket_panels"]].find_one({"guild_id": gid, "id": panel_id}, {"_id": 0})
    if not panel:
        raise HTTPException(404, "Panel not found")
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    guild = _BOT.get_guild(int(gid))
    if not guild:
        raise HTTPException(404, "Bot not in guild")
    if not panel.get("channel_id"):
        raise HTTPException(400, "Panel has no channel")
    ch = guild.get_channel(int(panel["channel_id"]))
    if not ch:
        raise HTTPException(404, "Channel not found")
    from discord_bot import _build_embed, TicketPanelView
    embed = _build_embed(panel.get("embed") or {}, server=guild.name) if (panel.get("embed") or {}).get("enabled", True) else None
    if not embed:
        embed = discord.Embed(title=panel["name"], description="Click below to open a ticket.", color=0x00F0FF)
    view = TicketPanelView(panel)
    msg = await ch.send(embed=embed, view=view)
    await db[COLL["ticket_panels"]].update_one({"id": panel_id}, {"$set": {"message_id": str(msg.id)}})
    return {"ok": True, "message_id": str(msg.id)}


@api.get("/guilds/{gid}/tickets")
async def list_tickets(gid: str):
    return await get_db()[COLL["tickets"]].find({"guild_id": gid}, {"_id": 0}).sort("created_at", -1).to_list(100)


@api.get("/guilds/{gid}/tickets/{ticket_id}/transcript", response_class=PlainTextResponse)
async def get_transcript(gid: str, ticket_id: str):
    t = await get_db()[COLL["tickets"]].find_one({"guild_id": gid, "id": ticket_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Not found")
    lines = t.get("transcript") or []
    txt = "\n".join(f"[{l['ts']}] {l['author']}: {l['content']}" for l in lines)
    return txt or "No transcript available."


# ============================== AI helpers =============================
class AiPreviewIn(BaseModel):
    knowledge_base: str = ""
    question: str


@api.post("/guilds/{gid}/ai/preview")
async def ai_preview(gid: str, body: AiPreviewIn):
    reply = await ticket_ai_reply(
        session_id=f"preview-{gid}-{int(time.time())}",
        knowledge_base=body.knowledge_base, history=[], user_text=body.question,
    )
    return {"reply": reply}


class AiSetupIn(BaseModel):
    section: str  # welcome | goodbye | automod | tickets | verification | raid | announcement | any
    prompt: str


@api.post("/guilds/{gid}/ai/setup")
async def ai_setup(gid: str, body: AiSetupIn):
    """AI helper that suggests configuration changes for a section based on natural language."""
    reply = await ai_setup_suggest(
        session_id=f"setup-{gid}-{body.section}-{int(time.time())}",
        section=body.section, prompt=body.prompt,
    )
    return reply  # {"text": "...", "config": {...} | None}


class AiApplyIn(BaseModel):
    section: str
    config: Dict[str, Any]


def _deep_merge(dst: dict, src: dict) -> dict:
    """Merge src into dst; nested dicts merge recursively, scalars overwrite."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k] = _deep_merge(dict(dst[k]), v)
        else:
            dst[k] = v
    return dst


@api.post("/guilds/{gid}/ai/apply")
async def ai_apply(gid: str, body: AiApplyIn):
    """Apply an AI-suggested config directly to the target section by merging with the
    current settings and calling the section's own PUT logic. Returns the new saved value.
    """
    section = body.section
    cfg = body.config or {}
    if section == "welcome":
        current = await _get_wg(gid, "welcome")
        merged = _deep_merge(current, cfg)
        return await _put_wg(gid, "welcome", WGSettingsIn(**{k: merged[k] for k in ("enabled", "channel_id", "text", "embed", "dm_enabled", "dm_text") if k in merged}))
    if section == "goodbye":
        current = await _get_wg(gid, "goodbye")
        merged = _deep_merge(current, cfg)
        return await _put_wg(gid, "goodbye", WGSettingsIn(**{k: merged[k] for k in ("enabled", "channel_id", "text", "embed", "dm_enabled", "dm_text") if k in merged}))
    if section == "moderation":
        current = await get_mod_settings(gid)
        merged = _deep_merge(current, cfg)
        allowed = ("escalation", "dm_on_warn", "dm_on_timeout", "dm_on_kick", "dm_on_ban",
                    "warn_dm_template", "timeout_dm_template", "kick_dm_template",
                    "ban_dm_template", "log_channel_id")
        return await put_mod_settings(gid, ModSettingsIn(**{k: merged[k] for k in allowed if k in merged}))
    if section == "verification":
        current = await get_verify(gid)
        merged = _deep_merge(current, cfg)
        allowed = ("enabled", "channel_id", "verified_role_id", "button_label",
                    "min_account_age_days", "kick_on_fail", "log_channel_id",
                    "embed", "success_message", "fail_message")
        return await put_verify(gid, VerificationIn(**{k: merged[k] for k in allowed if k in merged}))
    if section == "raid":
        current = await get_raid(gid)
        merged = _deep_merge(current, cfg)
        allowed = ("enabled", "join_burst_threshold", "join_burst_window_seconds",
                    "new_account_days", "action", "alert_channel_id",
                    "auto_lockdown_seconds", "alert_message")
        return await put_raid(gid, RaidIn(**{k: merged[k] for k in allowed if k in merged}))
    if section == "starboard":
        current = await get_star(gid)
        merged = _deep_merge(current, cfg)
        allowed = ("enabled", "channel_id", "emoji", "threshold",
                    "ignored_channel_ids", "ignored_role_ids", "ignore_bots", "self_star")
        return await put_star(gid, StarboardIn(**{k: merged[k] for k in allowed if k in merged}))
    if section == "automod":
        # Create a new rule from the AI suggestion (safer than editing an existing one).
        rule = AutomodRuleIn(
            kind=str(cfg.get("kind") or "custom_regex"),
            name=str(cfg.get("name") or "AI Rule"),
            enabled=bool(cfg.get("enabled", True)),
            sensitivity=int(cfg.get("sensitivity") or 5),
            threshold=int(cfg.get("threshold") or 0),
            words=list(cfg.get("words") or []),
            regex=cfg.get("regex"),
            actions=dict(cfg.get("actions") or {"delete": True, "warn": True, "log": True}),
            dm_template=str(cfg.get("dm_template") or DEFAULT_AUTOMOD_DM),
        )
        return await create_automod(gid, rule)
    raise HTTPException(400, f"Applying to section '{section}' is not supported")


# ============================== Logging ================================
LOGGING_EVENTS = [
    "message_delete", "message_edit", "member_join", "member_leave",
    "member_ban", "member_unban", "member_timeout", "member_warn",
    "role_add", "role_remove", "role_create", "role_delete",
    "channel_create", "channel_delete", "channel_update", "server_update",
    "voice_join", "voice_leave", "automod", "tickets", "verification",
]


class LoggingIn(BaseModel):
    categories: Dict[str, Dict[str, Any]]


@api.get("/guilds/{gid}/logging")
async def get_logging(gid: str):
    d = await get_db()[COLL["logging"]].find_one({"guild_id": gid}, {"_id": 0})
    if not d:
        d = {"guild_id": gid, "categories": {
            k: {"enabled": False, "channel_id": None, "ignored_channel_ids": [],
                "ignored_role_ids": [], "ignored_user_ids": []} for k in LOGGING_EVENTS}}
    return d


@api.put("/guilds/{gid}/logging")
async def put_logging(gid: str, body: LoggingIn):
    await get_db()[COLL["logging"]].update_one(
        {"guild_id": gid}, {"$set": {"guild_id": gid, "categories": body.categories}}, upsert=True)
    return await get_logging(gid)


# ============================== Verification ===========================
class VerificationIn(BaseModel):
    enabled: bool = False
    channel_id: Optional[str] = None
    verified_role_id: Optional[str] = None
    button_label: str = "Verify"
    min_account_age_days: int = 0
    kick_on_fail: bool = False
    log_channel_id: Optional[str] = None
    embed: Dict[str, Any] = {}
    success_message: str = DEFAULT_VERIFICATION["success_message"]
    fail_message: str = DEFAULT_VERIFICATION["fail_message"]


@api.get("/guilds/{gid}/verification")
async def get_verify(gid: str):
    d = await get_db()[COLL["verification"]].find_one({"guild_id": gid}, {"_id": 0})
    merged = {"guild_id": gid, **DEFAULT_VERIFICATION}
    if d:
        merged.update(d)
    return merged


@api.put("/guilds/{gid}/verification")
async def put_verify(gid: str, body: VerificationIn):
    updates = body.model_dump(); updates["guild_id"] = gid
    await get_db()[COLL["verification"]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await get_verify(gid)


@api.post("/guilds/{gid}/verification/deploy")
async def deploy_verify(gid: str):
    import discord
    cfg = await get_verify(gid)
    if not cfg.get("channel_id"):
        raise HTTPException(400, "No verification channel configured")
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    guild = _BOT.get_guild(int(gid))
    if not guild:
        raise HTTPException(404, "Bot not in guild")
    ch = guild.get_channel(int(cfg["channel_id"]))
    if not ch:
        raise HTTPException(404, "Channel not found")
    from discord_bot import _build_embed, VerificationView
    embed = _build_embed(cfg.get("embed") or {}, server=guild.name)
    if not embed:
        embed = discord.Embed(title="Verify", description="Click below to verify.", color=0x10B981)
    view = VerificationView(gid)
    await ch.send(embed=embed, view=view)
    return {"ok": True}


# ============================== Raid Protection ========================
class RaidIn(BaseModel):
    enabled: bool = True
    join_burst_threshold: int = 8
    join_burst_window_seconds: int = 10
    new_account_days: int = 7
    action: str = "verify"
    alert_channel_id: Optional[str] = None
    auto_lockdown_seconds: int = 600
    alert_message: str = DEFAULT_RAID["alert_message"]


@api.get("/guilds/{gid}/raid")
async def get_raid(gid: str):
    d = await get_db()[COLL["raid"]].find_one({"guild_id": gid}, {"_id": 0})
    merged = {"guild_id": gid, **DEFAULT_RAID}
    if d:
        merged.update(d)
    return merged


@api.put("/guilds/{gid}/raid")
async def put_raid(gid: str, body: RaidIn):
    updates = body.model_dump(); updates["guild_id"] = gid
    await get_db()[COLL["raid"]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await get_raid(gid)


# ============================== Auto Roles =============================
class AutoRolesIn(BaseModel):
    enabled: bool = False
    role_ids: List[str] = []
    bot_role_ids: List[str] = []
    delay_seconds: int = 0
    only_after_verification: bool = False


@api.get("/guilds/{gid}/auto-roles")
async def get_autoroles(gid: str):
    d = await get_db()[COLL["autoroles"]].find_one({"guild_id": gid}, {"_id": 0})
    merged = {"guild_id": gid, **DEFAULT_AUTO_ROLES}
    if d:
        merged.update(d)
    return merged


@api.put("/guilds/{gid}/auto-roles")
async def put_autoroles(gid: str, body: AutoRolesIn):
    updates = body.model_dump(); updates["guild_id"] = gid
    await get_db()[COLL["autoroles"]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await get_autoroles(gid)


# ============================== Starboard ==============================
class StarboardIn(BaseModel):
    enabled: bool = False
    channel_id: Optional[str] = None
    emoji: str = "⭐"
    threshold: int = 5
    ignored_channel_ids: List[str] = []
    ignored_role_ids: List[str] = []
    ignore_bots: bool = True
    self_star: bool = False


@api.get("/guilds/{gid}/starboard")
async def get_star(gid: str):
    d = await get_db()[COLL["starboard"]].find_one({"guild_id": gid}, {"_id": 0})
    merged = {"guild_id": gid, **DEFAULT_STARBOARD}
    if d:
        merged.update(d)
    return merged


@api.put("/guilds/{gid}/starboard")
async def put_star(gid: str, body: StarboardIn):
    updates = body.model_dump(); updates["guild_id"] = gid
    await get_db()[COLL["starboard"]].update_one({"guild_id": gid}, {"$set": updates}, upsert=True)
    return await get_star(gid)


# ============================== Announcements ==========================
class AnnouncementIn(BaseModel):
    channel_id: Optional[str] = None
    mention: str = ""
    text: str = ""
    embed: Dict[str, Any] = {}
    scheduled_at: Optional[str] = None


@api.get("/guilds/{gid}/announcements/defaults")
async def announcement_defaults(gid: str):
    return DEFAULT_ANNOUNCEMENT


@api.post("/guilds/{gid}/announcements/send")
async def send_announcement(gid: str, body: AnnouncementIn):
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    guild = _BOT.get_guild(int(gid))
    if not guild:
        raise HTTPException(404, "Bot not in guild")
    if not body.channel_id:
        raise HTTPException(400, "Channel required")
    ch = guild.get_channel(int(body.channel_id))
    if not ch:
        raise HTTPException(404, "Channel not found")
    from discord_bot import _build_embed
    embed = _build_embed(body.embed or {}, server=guild.name) if (body.embed or {}).get("enabled", True) else None
    content = (body.mention + "\n" + body.text).strip() or None
    await ch.send(content=content, embed=embed)
    # log to db
    await get_db()[COLL["announcements"]].insert_one({
        "id": str(uuid.uuid4()), "guild_id": gid, "channel_id": body.channel_id,
        "text": body.text, "embed": body.embed, "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@api.get("/guilds/{gid}/announcements")
async def list_announcements(gid: str):
    return await get_db()[COLL["announcements"]].find({"guild_id": gid}, {"_id": 0}).sort("sent_at", -1).to_list(50)


# ============================== Custom Commands ========================
class CustomCommandIn(BaseModel):
    name: str
    description: str = ""
    response_text: str = ""
    response_embed: Optional[Dict[str, Any]] = None
    required_role_ids: List[str] = []
    channel_ids: List[str] = []
    cooldown_seconds: int = 0
    delete_trigger: bool = False
    enabled: bool = True


@api.get("/guilds/{gid}/custom-commands")
async def list_ccs(gid: str):
    return await get_db()[COLL["custom_commands"]].find({"guild_id": gid}, {"_id": 0}).to_list(200)


@api.post("/guilds/{gid}/custom-commands")
async def create_cc(gid: str, body: CustomCommandIn):
    doc = body.model_dump()
    doc.update({"id": str(uuid.uuid4()), "guild_id": gid,
                "created_at": datetime.now(timezone.utc).isoformat()})
    await get_db()[COLL["custom_commands"]].insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/guilds/{gid}/custom-commands/{cid}")
async def update_cc(gid: str, cid: str, body: CustomCommandIn):
    await get_db()[COLL["custom_commands"]].update_one({"guild_id": gid, "id": cid}, {"$set": body.model_dump()})
    d = await get_db()[COLL["custom_commands"]].find_one({"id": cid}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    return d


@api.delete("/guilds/{gid}/custom-commands/{cid}")
async def delete_cc(gid: str, cid: str):
    await get_db()[COLL["custom_commands"]].delete_one({"guild_id": gid, "id": cid})
    return {"ok": True}


# ============================== Automations ============================
class AutomationIn(BaseModel):
    name: str
    enabled: bool = True
    trigger: Dict[str, Any]
    conditions: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []


@api.get("/guilds/{gid}/automations/schema")
async def automation_schema(gid: str):
    return {"triggers": AUTOMATION_TRIGGERS, "conditions": AUTOMATION_CONDITIONS, "actions": AUTOMATION_ACTIONS}


@api.get("/guilds/{gid}/automations")
async def list_autos(gid: str):
    return await get_db()[COLL["automations"]].find({"guild_id": gid}, {"_id": 0}).to_list(100)


@api.post("/guilds/{gid}/automations")
async def create_auto(gid: str, body: AutomationIn):
    doc = body.model_dump()
    doc.update({"id": str(uuid.uuid4()), "guild_id": gid,
                "created_at": datetime.now(timezone.utc).isoformat()})
    await get_db()[COLL["automations"]].insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/guilds/{gid}/automations/{aid}")
async def update_auto(gid: str, aid: str, body: AutomationIn):
    await get_db()[COLL["automations"]].update_one({"guild_id": gid, "id": aid}, {"$set": body.model_dump()})
    d = await get_db()[COLL["automations"]].find_one({"id": aid}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Not found")
    return d


@api.delete("/guilds/{gid}/automations/{aid}")
async def delete_auto(gid: str, aid: str):
    await get_db()[COLL["automations"]].delete_one({"guild_id": gid, "id": aid})
    return {"ok": True}


# ============================== Giveaways ==============================
class GiveawayIn(BaseModel):
    prize: str = DEFAULT_GIVEAWAY["prize"]
    winners: int = 1
    duration_minutes: int = 60
    required_role_ids: List[str] = []
    blocked_role_ids: List[str] = []
    min_account_age_days: int = 0
    min_membership_days: int = 0
    bonus_entry_role_ids: List[str] = []
    channel_id: str
    announcement_text: str = DEFAULT_GIVEAWAY["announcement_text"]
    winner_dm_enabled: bool = True
    winner_dm_text: str = DEFAULT_GIVEAWAY["winner_dm_text"]


@api.get("/guilds/{gid}/giveaways/defaults")
async def giveaway_defaults(gid: str):
    return DEFAULT_GIVEAWAY


@api.get("/guilds/{gid}/giveaways")
async def list_giveaways(gid: str):
    return await get_db()[COLL["giveaways"]].find({"guild_id": gid}, {"_id": 0}).sort("created_at", -1).to_list(100)


@api.post("/guilds/{gid}/giveaways")
async def create_giveaway(gid: str, body: GiveawayIn):
    from discord_bot import start_giveaway
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    gv = await start_giveaway(_BOT, gid, body.model_dump())
    return gv


@api.post("/guilds/{gid}/giveaways/{giveaway_id}/end")
async def end_giveaway(gid: str, giveaway_id: str):
    from discord_bot import end_giveaway as end_gw
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    result = await end_gw(_BOT, gid, giveaway_id)
    return result


@api.post("/guilds/{gid}/giveaways/{giveaway_id}/reroll")
async def reroll_giveaway(gid: str, giveaway_id: str):
    from discord_bot import reroll_giveaway as rr
    if not _BOT or not _BOT.is_ready():
        raise HTTPException(503, "Bot not ready")
    return await rr(_BOT, gid, giveaway_id)


# ============================== App wiring =============================
app.include_router(api)
app.add_middleware(
    CORSMiddleware, allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    global BOT_TASK, _BOT
    if not os.environ.get("DISCORD_TOKEN"):
        log.info("No DISCORD_TOKEN — bot disabled")
        return
    _BOT = await discord_bot.build_bot()
    # Persistent views
    try:
        db = get_db()
        async for p in db[COLL["ticket_panels"]].find({}, {"_id": 0}):
            _BOT.add_view(discord_bot.TicketPanelView(p))
            _BOT.add_view(discord_bot.TicketControlView(p["id"]))
        async for gv in db[COLL["giveaways"]].find({"status": "active"}, {"_id": 0}):
            _BOT.add_view(discord_bot.GiveawayView(gv["id"]))
        async for v in db[COLL["verification"]].find({"enabled": True}, {"_id": 0}):
            _BOT.add_view(discord_bot.VerificationView(v["guild_id"]))
    except Exception as e:
        log.warning(f"Persistent view register failed: {e}")
    BOT_TASK = asyncio.create_task(_BOT.start(os.environ["DISCORD_TOKEN"]))
    log.info("Discord bot task started")


@app.on_event("shutdown")
async def _shutdown():
    global _BOT, BOT_TASK
    try:
        if _BOT:
            await _BOT.close()
    except Exception:
        pass
    if BOT_TASK:
        BOT_TASK.cancel()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
