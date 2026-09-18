"""AI service for 1GC(ian) dashboard — ticket support + setup helper.

Uses emergentintegrations LlmChat with GPT model via Emergent Universal Key.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

MODEL_PROVIDER = "openai"
MODEL_NAME = "gpt-5.6-luna"


def _client(session_id: str, system: str) -> LlmChat:
    api_key = os.environ.get("EMERGENT_LLM_KEY", "")
    return LlmChat(api_key=api_key, session_id=session_id, system_message=system).with_model(MODEL_PROVIDER, MODEL_NAME)


async def ticket_ai_reply(session_id: str, knowledge_base: str, history: List[Dict[str, str]], user_text: str) -> str:
    if not os.environ.get("EMERGENT_LLM_KEY"):
        return "AI support is not configured for this server."
    system = (
        "You are the support assistant embedded inside a Discord ticket for the 1GC(ian) dashboard. "
        "Answer clearly and briefly (max 6 sentences). Use the KNOWLEDGE BASE when relevant. "
        "If the user's issue is complex or requires human staff (billing, moderation appeals, security incidents), "
        "politely say a human staff member will take over. Never claim to be human. "
        "Never invent policies not in the knowledge base."
    )
    if knowledge_base.strip():
        system += f"\n\nKNOWLEDGE BASE:\n{knowledge_base.strip()}"

    chat = _client(session_id, system)
    prompt = user_text
    if history:
        transcript = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history[-10:])
        prompt = f"CONVERSATION SO FAR:\n{transcript}\n\nUSER: {user_text}\nASSISTANT:"

    try:
        reply = await chat.send_message(UserMessage(text=prompt))
        return str(reply).strip() or "I'm not sure how to help with that. A staff member will follow up shortly."
    except Exception as e:
        return f"AI error: {e}"


async def summarize_ticket(session_id: str, messages: List[Dict[str, str]]) -> str:
    if not os.environ.get("EMERGENT_LLM_KEY") or not messages:
        return "No conversation to summarize."
    chat = _client(session_id, "You summarize Discord support tickets concisely (bulleted, <=8 bullets).")
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    try:
        reply = await chat.send_message(UserMessage(text=f"Summarize:\n{convo}"))
        return str(reply).strip()
    except Exception as e:
        return f"AI error: {e}"


SETUP_SCHEMAS = {
    "welcome": '{"text": "string", "embed": {"title": "string", "description": "string", "color": "#hex", "footer_text": "string"}}',
    "goodbye": '{"text": "string", "embed": {"title": "string", "description": "string", "color": "#hex"}}',
    "moderation": '{"warn_dm_template": "string", "timeout_dm_template": "string", "kick_dm_template": "string", "ban_dm_template": "string", "escalation": [{"warnings": int, "action": "warn|timeout|kick|ban", "duration_seconds": int|null}]}',
    "automod": '{"name": "string", "kind": "profanity|invites|mass_mentions|scam_links|excessive_caps|custom_regex", "threshold": int, "actions": {"delete": bool, "warn": bool, "dm": bool, "timeout_seconds": int}}',
    "tickets": '{"name": "string", "opening_message": "string", "closing_message": "string", "ai_knowledge_base": "string"}',
    "verification": '{"button_label": "string", "min_account_age_days": int, "success_message": "string", "embed": {"title": "string", "description": "string"}}',
    "raid": '{"enabled": bool, "join_burst_threshold": int, "join_burst_window_seconds": int, "action": "verify|lockdown|kick_new|ban_new"}',
    "announcement": '{"text": "string", "embed": {"title": "string", "description": "string", "color": "#hex"}}',
    "starboard": '{"emoji": "string", "threshold": int}',
    "any": '{}',
}


async def ai_setup_suggest(session_id: str, section: str, prompt: str) -> Dict[str, Any]:
    """Return an assistant reply and optionally a JSON config patch to apply."""
    if not os.environ.get("EMERGENT_LLM_KEY"):
        return {"text": "AI setup helper is not configured.", "config": None}

    schema = SETUP_SCHEMAS.get(section, "{}")
    system = (
        "You are a helpful setup assistant for the 1GC(ian) Discord dashboard. "
        f"The user is configuring the '{section}' section. "
        "Produce concrete, ready-to-apply configuration. "
        "Keep prose to 2 short sentences. Then output a single fenced JSON block that matches this shape "
        f"(fill only fields the user cares about, omit others):\n\nSCHEMA:\n{schema}\n\n"
        "Variables you can use inside text/embed strings: {user}, {username}, {server}, {member_count}, {reason}, {duration}, {case_id}, {rule}, {channel}. "
        "Colors must be hex (e.g. #00F0FF). Be concise, friendly, and useful."
    )
    chat = _client(session_id, system)
    try:
        raw = await chat.send_message(UserMessage(text=prompt))
        text = str(raw).strip()
    except Exception as e:
        return {"text": f"AI error: {e}", "config": None}

    # Extract fenced JSON
    cfg: Optional[Dict[str, Any]] = None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            cfg = json.loads(m.group(1))
        except Exception:
            cfg = None
    return {"text": text, "config": cfg}
