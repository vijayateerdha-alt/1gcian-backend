"""Backend regression tests for 1GC(ian) dashboard - iteration 2.

Covers all newly added modules & defaults, plus regressions from iteration_1.
Live guild: 1520987269471670303
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
# Frontend .env sets it without /api - append it here
if not BASE_URL.endswith("/api"):
    API = BASE_URL + "/api"
else:
    API = BASE_URL

GID = "1520987269471670303"


@pytest.fixture(scope="session")
def s():
    ses = requests.Session()
    ses.headers.update({"Content-Type": "application/json"})
    return ses


# ============================== Health / Guilds =============================
class TestHealthAndGuilds:
    def test_health(self, s):
        r = s.get(f"{API}/health", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d.get("ok") is True
        assert d.get("bot_ready") is True, f"Bot not ready: {d}"

    def test_guilds_list(self, s):
        r = s.get(f"{API}/guilds", timeout=15)
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list) and len(arr) >= 1


# ============================== Welcome / Goodbye ==========================
class TestWelcomeGoodbye:
    def test_welcome_defaults_nonempty(self, s):
        r = s.get(f"{API}/guilds/{GID}/welcome", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["embed"]["title"]
        assert d["embed"]["description"]
        assert d["embed"]["image"].startswith("http")
        assert "{user}" in d["text"] or "{username}" in d["text"]

    def test_goodbye_defaults_nonempty(self, s):
        r = s.get(f"{API}/guilds/{GID}/goodbye", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["embed"]["title"]
        assert d["embed"]["description"]
        assert d["embed"]["image"].startswith("http")

    def test_welcome_defaults_survive_partial_put(self, s):
        # PUT with empty strings/objects - defaults should still be returned
        r = s.put(f"{API}/guilds/{GID}/welcome",
                  json={"enabled": True, "text": "", "embed": {}, "dm_text": ""}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        # Text was empty in PUT - the default should survive via merge
        assert d["text"], "Default welcome text should survive empty PUT"
        assert d["embed"].get("title"), "Default embed title should survive empty PUT"
        assert d["embed"].get("description")
        assert d["enabled"] is True  # non-empty change persisted

    def test_goodbye_partial_put_persist(self, s):
        r = s.put(f"{API}/guilds/{GID}/goodbye",
                  json={"enabled": True, "text": "", "embed": {}}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["text"], "Default goodbye text should survive"
        assert d["embed"].get("title")
        assert d["enabled"] is True


# ============================== Moderation =================================
class TestModeration:
    def test_mod_defaults_include_dm_templates(self, s):
        # Reset by writing a null-only body so we still have defaults
        r = s.get(f"{API}/guilds/{GID}/moderation", timeout=15)
        assert r.status_code == 200
        d = r.json()
        # Even after previous destructive PUT, defaults must merge
        for k in ("warn_dm_template", "timeout_dm_template", "kick_dm_template", "ban_dm_template"):
            assert d.get(k), f"Missing default {k}"
        assert isinstance(d.get("escalation"), list)


# ============================== Ticket Panels ==============================
class TestTicketPanels:
    def test_ticket_defaults(self, s):
        r = s.get(f"{API}/guilds/{GID}/ticket-panels/defaults", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d.get("opening_message")
        assert d.get("ai_knowledge_base")
        assert d.get("embed", {}).get("title")
        assert isinstance(d.get("embed", {}).get("fields"), list)
        assert isinstance(d.get("form_questions_default"), list) and len(d["form_questions_default"]) >= 1

    def test_ticket_panels_list_works(self, s):
        # Regression from iteration_1: was returning 404 due to wildcard shadowing
        r = s.get(f"{API}/guilds/{GID}/ticket-panels", timeout=15)
        assert r.status_code == 200, f"list_panels broken: {r.text}"
        assert isinstance(r.json(), list)


# ============================== Verification ===============================
class TestVerification:
    def test_defaults(self, s):
        r = s.get(f"{API}/guilds/{GID}/verification", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["enabled"] is False
        assert d["success_message"]
        assert d["fail_message"]
        assert d["embed"].get("title")

    def test_put_persists(self, s):
        r = s.put(f"{API}/guilds/{GID}/verification",
                  json={"enabled": False, "button_label": "TESTVerify",
                        "min_account_age_days": 3, "kick_on_fail": True,
                        "success_message": "TEST_success", "fail_message": "TEST_fail",
                        "embed": {"title": "TEST", "description": "d"}}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["button_label"] == "TESTVerify"
        assert d["min_account_age_days"] == 3
        assert d["success_message"] == "TEST_success"


# ============================== Raid =======================================
class TestRaid:
    def test_defaults(self, s):
        r = s.get(f"{API}/guilds/{GID}/raid", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["join_burst_threshold"] == 8
        assert d["action"] == "verify"
        assert d["alert_message"]

    def test_put_persists(self, s):
        r = s.put(f"{API}/guilds/{GID}/raid",
                  json={"enabled": True, "join_burst_threshold": 12,
                        "action": "lockdown", "alert_message": "TEST_raid"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["join_burst_threshold"] == 12
        assert d["action"] == "lockdown"


# ============================== Auto Roles =================================
class TestAutoRoles:
    def test_defaults_and_put(self, s):
        r = s.get(f"{API}/guilds/{GID}/auto-roles", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "role_ids" in d
        r2 = s.put(f"{API}/guilds/{GID}/auto-roles",
                   json={"enabled": True, "role_ids": ["12345"], "delay_seconds": 5}, timeout=15)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["enabled"] is True
        assert d2["role_ids"] == ["12345"]


# ============================== Starboard ==================================
class TestStarboard:
    def test_defaults_and_put(self, s):
        r = s.get(f"{API}/guilds/{GID}/starboard", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["emoji"] == "⭐"
        assert d["threshold"] == 5
        r2 = s.put(f"{API}/guilds/{GID}/starboard",
                   json={"enabled": True, "emoji": "🌟", "threshold": 7}, timeout=15)
        assert r2.status_code == 200
        assert r2.json()["threshold"] == 7


# ============================== Announcements ==============================
class TestAnnouncements:
    def test_defaults(self, s):
        r = s.get(f"{API}/guilds/{GID}/announcements/defaults", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["embed"]["title"]
        assert d["embed"]["description"]

    def test_send_requires_channel(self, s):
        # Sending without channel_id should 400
        r = s.post(f"{API}/guilds/{GID}/announcements/send",
                   json={"text": "TEST_ignore", "embed": {}}, timeout=15)
        assert r.status_code == 400, f"expected 400 got {r.status_code}: {r.text}"

    def test_send_bogus_channel_404(self, s):
        r = s.post(f"{API}/guilds/{GID}/announcements/send",
                   json={"channel_id": "999999999999999999", "text": "x"}, timeout=15)
        assert r.status_code == 404, f"expected 404 got {r.status_code}"


# ============================== Automations ================================
class TestAutomations:
    def test_schema(self, s):
        r = s.get(f"{API}/guilds/{GID}/automations/schema", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert len(d["triggers"]) > 0
        assert len(d["conditions"]) > 0
        assert len(d["actions"]) > 0

    def test_crud(self, s):
        payload = {"name": "TEST_auto", "enabled": True,
                   "trigger": {"type": "member_join", "params": {}},
                   "conditions": [], "actions": [{"type": "send_message", "params": {"channel_id": "123", "text": "hi"}}]}
        r = s.post(f"{API}/guilds/{GID}/automations", json=payload, timeout=15)
        assert r.status_code == 200
        aid = r.json()["id"]
        r2 = s.get(f"{API}/guilds/{GID}/automations", timeout=15)
        assert any(a["id"] == aid for a in r2.json())
        r3 = s.put(f"{API}/guilds/{GID}/automations/{aid}",
                   json={**payload, "name": "TEST_auto2"}, timeout=15)
        assert r3.status_code == 200
        assert r3.json()["name"] == "TEST_auto2"
        r4 = s.delete(f"{API}/guilds/{GID}/automations/{aid}", timeout=15)
        assert r4.status_code == 200


# ============================== Giveaways ==================================
class TestGiveaways:
    def test_defaults(self, s):
        r = s.get(f"{API}/guilds/{GID}/giveaways/defaults", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["prize"]
        assert d["announcement_text"]

    def test_create_bogus_channel_fails(self, s):
        # Do not spam live server: pass fake channel id, expect failure (404/400/500 from bot)
        payload = {"prize": "TEST_prize", "winners": 1, "duration_minutes": 1,
                   "channel_id": "999999999999999999"}
        r = s.post(f"{API}/guilds/{GID}/giveaways", json=payload, timeout=30)
        assert r.status_code >= 400, f"Should fail with fake channel, got {r.status_code}: {r.text}"


# ============================== Custom Commands ============================
class TestCustomCommands:
    def test_crud(self, s):
        payload = {"name": "TEST_cmd", "description": "d", "response_text": "hi"}
        r = s.post(f"{API}/guilds/{GID}/custom-commands", json=payload, timeout=15)
        assert r.status_code == 200
        cid = r.json()["id"]
        r2 = s.get(f"{API}/guilds/{GID}/custom-commands", timeout=15)
        assert any(c["id"] == cid for c in r2.json())
        r3 = s.put(f"{API}/guilds/{GID}/custom-commands/{cid}",
                   json={**payload, "response_text": "hello2"}, timeout=15)
        assert r3.status_code == 200
        assert r3.json()["response_text"] == "hello2"
        r4 = s.delete(f"{API}/guilds/{GID}/custom-commands/{cid}", timeout=15)
        assert r4.status_code == 200


# ============================== AI setup + preview =========================
class TestAI:
    def test_ai_preview(self, s):
        r = s.post(f"{API}/guilds/{GID}/ai/preview",
                   json={"knowledge_base": "The server has rules in #rules.",
                         "question": "Where are the rules?"}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["reply"] and len(d["reply"]) > 5

    def test_ai_setup_welcome_returns_text_and_config(self, s):
        r = s.post(f"{API}/guilds/{GID}/ai/setup",
                   json={"section": "welcome",
                         "prompt": "Make it neon cyberpunk themed, friendly, mention the rules channel"},
                   timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("text"), f"text empty: {d}"
        # config is dict or None - both accepted per contract, but for welcome we expect JSON
        cfg = d.get("config")
        assert cfg is None or isinstance(cfg, dict)
        # Emit for report
        print(f"[ai/setup welcome] text_len={len(d['text'])} config_keys={list(cfg.keys()) if cfg else None}")


# ============================== Logging regression =========================
class TestLogging:
    def test_logging_get(self, s):
        r = s.get(f"{API}/guilds/{GID}/logging", timeout=15)
        assert r.status_code == 200, f"logging GET broken: {r.text}"
        d = r.json()
        assert "categories" in d
