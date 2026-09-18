"""Pydantic models for AETHERIS Discord admin platform."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ----------------------------- Guild ------------------------------
class Guild(Base):
    id: str  # discord guild id (snowflake string) is the primary id
    name: str
    icon: Optional[str] = None
    member_count: int = 0
    owner_id: Optional[str] = None
    joined_at: str = Field(default_factory=_now)


class Channel(Base):
    id: str
    name: str
    type: str  # text | voice | category | forum | announcement | ...


class Role(Base):
    id: str
    name: str
    color: int = 0
    position: int = 0
    managed: bool = False


# ---------------------------- Embed --------------------------------
class EmbedField(Base):
    name: str = ""
    value: str = ""
    inline: bool = False


class EmbedConfig(Base):
    enabled: bool = True
    title: str = ""
    description: str = ""
    color: str = "#00F0FF"  # hex
    thumbnail: str = ""
    image: str = ""
    author_name: str = ""
    author_icon: str = ""
    footer_text: str = ""
    footer_icon: str = ""
    fields: List[EmbedField] = Field(default_factory=list)


# --------------------------- Moderation -----------------------------
Action = Literal["warn", "timeout", "kick", "ban", "unban", "softban", "untimeout", "note"]


class Case(Base):
    id: str = Field(default_factory=_uuid)
    guild_id: str
    case_number: int
    action: Action
    target_id: str
    target_tag: str = ""
    moderator_id: str
    moderator_tag: str = ""
    reason: str = ""
    duration_seconds: Optional[int] = None
    created_at: str = Field(default_factory=_now)
    expires_at: Optional[str] = None
    active: bool = True


class EscalationRule(Base):
    warnings: int  # trigger at N warnings
    action: Action  # what to do
    duration_seconds: Optional[int] = None


class ModerationSettings(Base):
    guild_id: str
    escalation: List[EscalationRule] = Field(
        default_factory=lambda: [
            EscalationRule(warnings=3, action="timeout", duration_seconds=600),
            EscalationRule(warnings=5, action="kick"),
            EscalationRule(warnings=7, action="ban"),
        ]
    )
    dm_on_warn: bool = True
    dm_on_timeout: bool = True
    dm_on_kick: bool = True
    dm_on_ban: bool = True
    warn_dm_template: str = "You received a warning in **{server}**. Reason: {reason}"
    timeout_dm_template: str = "You were timed out in **{server}** for {duration}. Reason: {reason}"
    kick_dm_template: str = "You were kicked from **{server}**. Reason: {reason}"
    ban_dm_template: str = "You were banned from **{server}**. Reason: {reason}"
    log_channel_id: Optional[str] = None


# ---------------------------- AutoMod -------------------------------
AutomodRuleKind = Literal[
    "profanity",
    "strong_profanity",
    "slurs",
    "insults",
    "invites",
    "external_links",
    "spam",
    "flood",
    "repeated_messages",
    "excessive_caps",
    "excessive_emojis",
    "mass_mentions",
    "scam_links",
    "obfuscation",
    "custom_regex",
]


class AutomodAction(Base):
    delete: bool = True
    warn: bool = False
    dm: bool = False
    timeout_seconds: int = 0  # 0 = no timeout
    kick: bool = False
    ban: bool = False
    add_role_ids: List[str] = Field(default_factory=list)
    remove_role_ids: List[str] = Field(default_factory=list)
    alert_staff: bool = False
    log: bool = True


class AutomodRule(Base):
    id: str = Field(default_factory=_uuid)
    guild_id: str
    kind: AutomodRuleKind
    name: str
    enabled: bool = True
    sensitivity: int = 5  # 1-10
    threshold: int = 5  # rule-specific (caps %, mention count, etc)
    words: List[str] = Field(default_factory=list)  # custom words
    regex: Optional[str] = None
    actions: AutomodAction = Field(default_factory=AutomodAction)
    exempt_user_ids: List[str] = Field(default_factory=list)
    exempt_role_ids: List[str] = Field(default_factory=list)
    exempt_channel_ids: List[str] = Field(default_factory=list)
    allowed_domains: List[str] = Field(default_factory=list)
    dm_template: str = "Your message in **{server}** was removed by AutoMod ({rule})."
    created_at: str = Field(default_factory=_now)


# --------------------------- Welcome/Goodbye ------------------------
class WelcomeGoodbyeSettings(Base):
    guild_id: str
    kind: Literal["welcome", "goodbye"]
    enabled: bool = False
    channel_id: Optional[str] = None
    text: str = ""
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    dm_enabled: bool = False
    dm_text: str = ""


# ---------------------------- Tickets -------------------------------
class TicketFormQuestion(Base):
    id: str = Field(default_factory=_uuid)
    label: str
    placeholder: str = ""
    required: bool = True
    style: Literal["short", "paragraph"] = "short"


class TicketPanel(Base):
    id: str = Field(default_factory=_uuid)
    guild_id: str
    name: str
    channel_id: Optional[str] = None
    message_id: Optional[str] = None
    button_label: str = "Open Ticket"
    button_style: Literal["primary", "secondary", "success", "danger"] = "primary"
    category_id: Optional[str] = None  # category to open tickets in
    support_role_ids: List[str] = Field(default_factory=list)
    opening_message: str = "Thank you for opening a ticket. Staff will be with you shortly."
    closing_message: str = "This ticket has been closed."
    ai_support_enabled: bool = False
    ai_knowledge_base: str = ""
    log_channel_id: Optional[str] = None
    form_enabled: bool = False
    form_questions: List[TicketFormQuestion] = Field(default_factory=list)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    created_at: str = Field(default_factory=_now)


class Ticket(Base):
    id: str = Field(default_factory=_uuid)
    guild_id: str
    panel_id: str
    channel_id: str
    opener_id: str
    opener_tag: str = ""
    status: Literal["open", "claimed", "closed"] = "open"
    claimed_by: Optional[str] = None
    form_answers: Dict[str, str] = Field(default_factory=dict)
    ai_messages: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    closed_at: Optional[str] = None


# ---------------------------- Logging -------------------------------
LoggingEventKey = Literal[
    "message_delete",
    "message_edit",
    "member_join",
    "member_leave",
    "member_ban",
    "member_unban",
    "member_timeout",
    "member_warn",
    "role_add",
    "role_remove",
    "role_create",
    "role_delete",
    "channel_create",
    "channel_delete",
    "channel_update",
    "server_update",
    "voice_join",
    "voice_leave",
    "automod",
    "tickets",
    "verification",
]


class LoggingCategory(Base):
    enabled: bool = False
    channel_id: Optional[str] = None
    ignored_channel_ids: List[str] = Field(default_factory=list)
    ignored_role_ids: List[str] = Field(default_factory=list)
    ignored_user_ids: List[str] = Field(default_factory=list)


class LoggingSettings(Base):
    guild_id: str
    categories: Dict[str, LoggingCategory] = Field(default_factory=dict)


# ---------------------------- Analytics -----------------------------
class GuildStats(Base):
    guild_id: str
    member_count: int = 0
    joins_7d: int = 0
    leaves_7d: int = 0
    mod_actions_7d: int = 0
    automod_actions_7d: int = 0
    tickets_open: int = 0
    tickets_closed_7d: int = 0
    bot_uptime_seconds: int = 0
