"""Iteration 3: tests for POST /api/guilds/{gid}/ai/apply + static code inspection
for Discord bot ticket AI (Toggle AI button, on_message opener auto-reply, taitoggle handler)."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
GID = "1520987269471670303"
API = f"{BASE_URL}/api/guilds/{GID}"

BACKEND_DIR = Path("/app/backend")


# ---------- health / bot ready ----------
def test_health_and_bot_ready():
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert j.get("bot_ready") is True, f"bot not ready: {j}"


# ---------- ai/apply: welcome ----------
def test_ai_apply_welcome_persists():
    payload = {"section": "welcome", "config": {"text": "hello NEW WELCOME"}}
    r = requests.post(f"{API}/ai/apply", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "hello NEW WELCOME" in (body.get("text") or "")
    # verify via GET
    g = requests.get(f"{API}/welcome", timeout=10).json()
    assert "hello NEW WELCOME" in (g.get("text") or "")


# ---------- ai/apply: moderation partial merge preserves escalation ----------
def test_ai_apply_moderation_merges_partial():
    before = requests.get(f"{API}/moderation", timeout=10).json()
    esc_before = before.get("escalation") or []
    assert isinstance(esc_before, list) and len(esc_before) > 0, \
        f"default escalation missing: {before}"

    payload = {"section": "moderation",
               "config": {"warn_dm_template": "AI test WARN DM"}}
    r = requests.post(f"{API}/ai/apply", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after.get("warn_dm_template") == "AI test WARN DM"
    # escalation array must survive
    esc_after = after.get("escalation") or []
    assert esc_after == esc_before, (
        f"escalation was mutated by partial merge:\nBEFORE={esc_before}\nAFTER={esc_after}"
    )

    # GET verify persistence
    g = requests.get(f"{API}/moderation", timeout=10).json()
    assert g.get("warn_dm_template") == "AI test WARN DM"
    assert (g.get("escalation") or []) == esc_before


# ---------- ai/apply: verification ----------
def test_ai_apply_verification_persists():
    r = requests.post(f"{API}/ai/apply",
                      json={"section": "verification",
                            "config": {"button_label": "AI Test Verify"}},
                      timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("button_label") == "AI Test Verify"
    g = requests.get(f"{API}/verification", timeout=10).json()
    assert g.get("button_label") == "AI Test Verify"


# ---------- ai/apply: raid ----------
def test_ai_apply_raid_persists():
    r = requests.post(f"{API}/ai/apply",
                      json={"section": "raid",
                            "config": {"join_burst_threshold": 20}},
                      timeout=15)
    assert r.status_code == 200, r.text
    assert int(r.json().get("join_burst_threshold")) == 20
    g = requests.get(f"{API}/raid", timeout=10).json()
    assert int(g.get("join_burst_threshold")) == 20


# ---------- ai/apply: starboard ----------
def test_ai_apply_starboard_persists():
    r = requests.post(f"{API}/ai/apply",
                      json={"section": "starboard", "config": {"threshold": 9}},
                      timeout=15)
    assert r.status_code == 200, r.text
    assert int(r.json().get("threshold")) == 9
    g = requests.get(f"{API}/starboard", timeout=10).json()
    assert int(g.get("threshold")) == 9


# ---------- ai/apply: automod creates new rule ----------
def test_ai_apply_automod_creates_rule_and_cleanup():
    unique = f"AI Rule {int(time.time())}"
    payload = {"section": "automod",
               "config": {"kind": "custom_regex",
                          "name": unique,
                          "regex": "test-pattern",
                          "actions": {"delete": True}}}
    r = requests.post(f"{API}/ai/apply", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created.get("name") == unique
    rule_id = created.get("id")
    assert rule_id

    # list must include it
    rules = requests.get(f"{API}/automod", timeout=10).json()
    assert any(rr.get("id") == rule_id for rr in rules)

    # cleanup
    d = requests.delete(f"{API}/automod/{rule_id}", timeout=10)
    assert d.status_code == 200


# ---------- ai/apply: unsupported sections must 400 ----------
@pytest.mark.parametrize("section", ["tickets", "announcement"])
def test_ai_apply_unsupported_returns_400(section):
    r = requests.post(f"{API}/ai/apply",
                      json={"section": section, "config": {"foo": "bar"}},
                      timeout=15)
    assert r.status_code == 400, r.text
    body = r.json()
    msg = (body.get("detail") or "").lower()
    assert "not supported" in msg or section in msg


# ---------- ai/setup still works ----------
def test_ai_setup_still_returns_text_and_config():
    r = requests.post(f"{API}/ai/setup",
                      json={"section": "welcome",
                            "prompt": "make a welcome message that greets new members"},
                      timeout=45)
    assert r.status_code == 200, r.text
    j = r.json()
    assert isinstance(j.get("text"), str) and len(j["text"]) > 0
    # config can be dict or None; when present it should be a dict
    if j.get("config") is not None:
        assert isinstance(j["config"], dict)


# ==========================================================================
# STATIC CODE INSPECTION — Discord bot changes cannot be exercised live.
# ==========================================================================

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_static_ticketcontrolview_has_toggle_ai_button():
    src = _read(BACKEND_DIR / "discord_bot.py")
    # find TicketControlView block
    m = re.search(r"class TicketControlView.*?(?=\nclass |\Z)", src, re.S)
    assert m, "TicketControlView class not found"
    block = m.group(0)
    # must contain a taitoggle button with 'Toggle AI' label; must NOT use 'task:' prefix
    assert "taitoggle" in block, "TicketControlView missing 'taitoggle' custom_id"
    assert "Toggle AI" in block, "TicketControlView missing 'Toggle AI' label"
    assert re.search(r"custom_id\s*=\s*f?\"1gc:task:", block) is None, \
        "Found forbidden 'task:' custom_id in TicketControlView"


def test_static_tickets_on_message_auto_reply_for_opener():
    src = _read(BACKEND_DIR / "discord_bot.py")
    # locate TicketsCog.on_message
    m = re.search(r"class TicketsCog.*?async def on_interaction", src, re.S)
    assert m, "TicketsCog block not found"
    block = m.group(0)
    # transcript append still there
    assert "$push" in block and "transcript" in block, "transcript append missing"
    # auto-reply requires ai_active + opener match + panel.ai_support_enabled + ticket_ai_reply call
    assert "ai_active" in block
    assert "opener_id" in block
    assert "ai_support_enabled" in block
    assert "ticket_ai_reply" in block


def test_static_tickets_on_interaction_taitoggle_flips_ai_active():
    src = _read(BACKEND_DIR / "discord_bot.py")
    # scope to TicketsCog only
    tcog = re.search(r"class TicketsCog.*?(?=\nclass |\Z)", src, re.S)
    assert tcog, "TicketsCog not found"
    m = re.search(r"async def on_interaction.*", tcog.group(0), re.S)
    assert m, "TicketsCog.on_interaction not found"
    block = m.group(0)
    assert "taitoggle" in block, "taitoggle branch missing"
    # must update ai_active in db
    assert "ai_active" in block
    assert re.search(r"\$set.*ai_active", block, re.S), \
        "Expected $set ai_active update in taitoggle handler"


def test_static_server_has_deep_merge_and_all_apply_sections():
    src = _read(BACKEND_DIR / "server.py")
    assert "def _deep_merge(" in src, "_deep_merge helper missing"
    # ai_apply endpoint
    assert "ai/apply" in src or 'ai_apply' in src, "ai_apply endpoint missing"
    # supported sections
    for section in ("welcome", "goodbye", "moderation", "verification",
                    "raid", "starboard", "automod"):
        assert f'section == "{section}"' in src, f"branch for {section} missing"
    # 400 fallback text
    assert "not supported" in src
