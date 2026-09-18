"""MongoDB helpers for AETHERIS."""
from __future__ import annotations

import os
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None
_db = None


def get_db():
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        _db = _client[os.environ["DB_NAME"]]
    return _db


COLL = {
    "guilds": "aetheris_guilds",
    "channels": "aetheris_channels",
    "roles": "aetheris_roles",
    "cases": "aetheris_cases",
    "mod_settings": "aetheris_mod_settings",
    "automod_rules": "aetheris_automod_rules",
    "welcome": "aetheris_welcome",
    "goodbye": "aetheris_goodbye",
    "ticket_panels": "aetheris_ticket_panels",
    "tickets": "aetheris_tickets",
    "logging": "aetheris_logging",
    "events": "aetheris_events",  # log of what bot has done
    "counters": "aetheris_counters",
    "verification": "aetheris_verification",
    "raid": "aetheris_raid",
    "autoroles": "aetheris_autoroles",
    "starboard": "aetheris_starboard",
    "starboard_posts": "aetheris_starboard_posts",
    "announcements": "aetheris_announcements",
    "custom_commands": "aetheris_custom_commands",
    "automations": "aetheris_automations",
    "giveaways": "aetheris_giveaways",
    "giveaway_entries": "aetheris_giveaway_entries",
}


async def next_case_number(guild_id: str) -> int:
    db = get_db()
    res = await db[COLL["counters"]].find_one_and_update(
        {"_id": f"cases:{guild_id}"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=True,
    )
    return int(res["value"])
