"""Default message prompts and templates used everywhere in the dashboard.
Every section ships with a sensible default so admins don't need to type from scratch.
"""

DEFAULT_WELCOME_IMAGE = "https://images.unsplash.com/photo-1542751371-adc38448a05e?crop=entropy&cs=srgb&fm=jpg&w=1200&q=70"
DEFAULT_WELCOME_THUMBNAIL = "https://images.unsplash.com/photo-1612287230202-1ff1d85d1bdf?crop=entropy&cs=srgb&fm=jpg&w=400&q=70"
DEFAULT_GOODBYE_IMAGE = "https://images.unsplash.com/photo-1672872476232-da16b45c9001?crop=entropy&cs=srgb&fm=jpg&w=1200&q=70"

DEFAULT_WELCOME = {
    "enabled": False,
    "channel_id": None,
    "text": "Welcome {user} to **{server}** — you are member #{member_count}. Read the rules and enjoy your stay.",
    "embed": {
        "enabled": True,
        "title": "Welcome to {server}",
        "description": "Hey {user}, we are glad you are here.\n\nGrab your roles, say hi in general chat, and check the announcements channel for what is going on this week.",
        "color": "#00F0FF",
        "thumbnail": DEFAULT_WELCOME_THUMBNAIL,
        "image": DEFAULT_WELCOME_IMAGE,
        "author_name": "",
        "author_icon": "",
        "footer_text": "Member #{member_count}",
        "footer_icon": "",
        "fields": [
            {"name": "New here?", "value": "Introduce yourself in #introductions.", "inline": True},
            {"name": "Need help?", "value": "Open a ticket in #support.", "inline": True},
        ],
    },
    "dm_enabled": False,
    "dm_text": "Welcome to **{server}**, {username}! If you need help, open a ticket in the support channel.",
}

DEFAULT_GOODBYE = {
    "enabled": False,
    "channel_id": None,
    "text": "**{username}** has left the server. We are now at {member_count} members.",
    "embed": {
        "enabled": True,
        "title": "Farewell, {username}",
        "description": "Thanks for being part of **{server}**. The door is always open if you decide to return.",
        "color": "#F59E0B",
        "thumbnail": "",
        "image": DEFAULT_GOODBYE_IMAGE,
        "author_name": "",
        "author_icon": "",
        "footer_text": "Members remaining: {member_count}",
        "footer_icon": "",
        "fields": [],
    },
    "dm_enabled": False,
    "dm_text": "",
}

DEFAULT_MOD = {
    "escalation": [
        {"warnings": 3, "action": "timeout", "duration_seconds": 600},
        {"warnings": 5, "action": "kick"},
        {"warnings": 7, "action": "ban"},
    ],
    "dm_on_warn": True, "dm_on_timeout": True, "dm_on_kick": True, "dm_on_ban": True,
    "warn_dm_template": "Hey {username}, you were warned in **{server}**.\nReason: {reason}\nCase: #{case_id}\n\nPlease review the server rules to avoid further action.",
    "timeout_dm_template": "You have been timed out in **{server}** for {duration}.\nReason: {reason}\nCase: #{case_id}",
    "kick_dm_template": "You were kicked from **{server}**.\nReason: {reason}\nYou can rejoin, but repeated violations will lead to a ban.",
    "ban_dm_template": "You have been banned from **{server}**.\nReason: {reason}\nIf you believe this was in error, contact staff outside the server.",
    "log_channel_id": None,
}

DEFAULT_AUTOMOD_DM = "Your message in **{server}** was removed by AutoMod.\nRule: {rule}\nReason: {reason}\n\nPlease review the server rules."

DEFAULT_TICKET_PANEL = {
    "name": "Support",
    "button_label": "Open Ticket",
    "button_style": "primary",
    "opening_message": "Hello {user}, thank you for opening a ticket in **{server}**.\n\nA staff member will be with you shortly. In the meantime, please describe your issue in detail.",
    "closing_message": "This ticket has been closed. A transcript has been saved.",
    "ai_knowledge_base": "You are a Discord server support assistant. If a user asks about:\n- Rules: point them to the #rules channel.\n- Roles: tell them to use the #roles channel to self-assign.\n- Bugs: ask for screenshots and steps to reproduce.\n- Bans/warnings: tell them a human staff member will follow up.\nBe polite, brief, and helpful.",
    "embed": {
        "enabled": True,
        "title": "Server Support",
        "description": "Need help? Click the button below to open a private ticket with staff.\n\nOur team responds within 24 hours.",
        "color": "#00F0FF",
        "thumbnail": "",
        "image": "",
        "author_name": "",
        "author_icon": "",
        "footer_text": "You will get a private channel visible only to you and staff.",
        "footer_icon": "",
        "fields": [
            {"name": "Response time", "value": "Usually under 24 hours", "inline": True},
            {"name": "Ticket types", "value": "General · Bug · Report", "inline": True},
        ],
    },
    "form_questions_default": [
        {"label": "Subject", "placeholder": "Short summary of your issue", "required": True, "style": "short"},
        {"label": "Describe your issue", "placeholder": "Please include as much detail as possible", "required": True, "style": "paragraph"},
    ],
}

DEFAULT_VERIFICATION = {
    "enabled": False,
    "channel_id": None,
    "verified_role_id": None,
    "button_label": "Verify",
    "min_account_age_days": 0,
    "kick_on_fail": False,
    "log_channel_id": None,
    "embed": {
        "enabled": True,
        "title": "Verification required",
        "description": "Click the button below to gain access to the rest of the server.\n\nBy verifying, you agree to follow the server rules.",
        "color": "#10B981",
        "thumbnail": "",
        "image": "",
        "author_name": "",
        "author_icon": "",
        "footer_text": "This helps us keep the server safe from bots and raiders.",
        "footer_icon": "",
        "fields": [],
    },
    "success_message": "You are now verified. Welcome to the server, {user}.",
    "fail_message": "Your account does not meet our security requirements. Contact staff for assistance.",
}

DEFAULT_RAID = {
    "enabled": True,
    "join_burst_threshold": 8,
    "join_burst_window_seconds": 10,
    "new_account_days": 7,
    "action": "verify",  # verify | lockdown | kick_new | ban_new
    "alert_channel_id": None,
    "auto_lockdown_seconds": 600,
    "alert_message": "**RAID ALERT** — {count} accounts joined in the last {window}s (raid threshold: {threshold}). Auto-response: {action}.",
}

DEFAULT_AUTO_ROLES = {
    "enabled": False,
    "role_ids": [],
    "bot_role_ids": [],
    "delay_seconds": 0,
    "only_after_verification": False,
}

DEFAULT_STARBOARD = {
    "enabled": False,
    "channel_id": None,
    "emoji": "⭐",
    "threshold": 5,
    "ignored_channel_ids": [],
    "ignored_role_ids": [],
    "ignore_bots": True,
    "self_star": False,
}

DEFAULT_ANNOUNCEMENT = {
    "channel_id": None,
    "mention": "",  # e.g. "@everyone" or role mention
    "text": "",
    "embed": {
        "enabled": True,
        "title": "Announcement",
        "description": "Write your announcement here. You can use markdown, mentions, and variables.",
        "color": "#00F0FF",
        "thumbnail": "",
        "image": "",
        "author_name": "",
        "author_icon": "",
        "footer_text": "",
        "footer_icon": "",
        "fields": [],
    },
    "scheduled_at": None,  # ISO string or None to send immediately
}

DEFAULT_CUSTOM_COMMAND = {
    "name": "example",
    "description": "An example custom command",
    "response_text": "Hello {user}, this is a custom response.",
    "response_embed": None,  # optional embed
    "required_role_ids": [],
    "channel_ids": [],  # restrict to channels (empty = all)
    "cooldown_seconds": 0,
    "delete_trigger": False,
    "enabled": True,
}

DEFAULT_AUTOMATION = {
    "name": "New Automation",
    "enabled": True,
    "trigger": {"type": "member_join", "params": {}},
    "conditions": [],  # [{type: "has_role", value: "role_id"}]
    "actions": [{"type": "send_message", "params": {"channel_id": "", "text": "Welcome!"}}],
}

AUTOMATION_TRIGGERS = [
    "member_join", "member_leave", "message_sent", "message_deleted",
    "role_added", "role_removed", "voice_join", "voice_leave",
    "ticket_created", "ticket_closed", "giveaway_ended",
    "automod_action", "moderation_action", "scheduled",
]

AUTOMATION_CONDITIONS = [
    "has_role", "not_has_role", "in_channel", "account_age_days",
    "membership_age_days", "message_contains", "message_matches_regex",
    "user_has_permission",
]

AUTOMATION_ACTIONS = [
    "send_message", "send_dm", "add_role", "remove_role",
    "warn", "timeout", "kick", "ban", "delete_message",
    "lock_channel", "unlock_channel", "create_ticket", "log",
]

DEFAULT_GIVEAWAY = {
    "prize": "Steam Key",
    "winners": 1,
    "duration_minutes": 60,
    "required_role_ids": [],
    "blocked_role_ids": [],
    "min_account_age_days": 0,
    "min_membership_days": 0,
    "bonus_entry_role_ids": [],  # each role adds +1 entry
    "channel_id": None,
    "announcement_text": "🎉 **GIVEAWAY** 🎉\n\nClick the button below to enter for a chance to win **{prize}**.\n\nWinners: {winners}\nEnds: <t:{ends_ts}:R>",
    "winner_dm_enabled": True,
    "winner_dm_text": "Congratulations {user}, you won **{prize}** in **{server}**! A staff member will contact you shortly.",
}
