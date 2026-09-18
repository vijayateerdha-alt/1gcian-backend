"""AutoMod detection engine. Framework-agnostic: takes a message + rules -> matches."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from default_profanity import OBFUSCATION_MAP, PROFANITY, STRONG_PROFANITY, SLURS, INSULTS


INVITE_RE = re.compile(r"(discord\.gg|discord(app)?\.com/invite|discord\.me)/[a-zA-Z0-9-]+", re.I)
URL_RE = re.compile(r"https?://([\w\.-]+)", re.I)
MENTION_RE = re.compile(r"<@!?(\d+)>|<@&(\d+)>|@everyone|@here")
CAPS_RE = re.compile(r"[A-Z]")
EMOJI_RE = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF]|[\u2600-\u27BF]")

SCAM_KEYWORDS = [
    "free nitro", "steam gift", "airdrop", "claim your", "verify wallet",
    "connect your wallet", "hacked account", "you have been selected",
]


@dataclass
class Match:
    rule_kind: str
    reason: str
    matched: str = ""


def _normalize(text: str) -> str:
    """Reverse obfuscation: lowercase and map look-alikes back to letters."""
    text = text.lower()
    reverse = {}
    for base, variants in OBFUSCATION_MAP.items():
        for v in variants:
            reverse[v] = base
    out = []
    for ch in text:
        out.append(reverse.get(ch, ch))
    # collapse repeated chars (leet spacing) e.g. f u c k -> fuck
    return "".join(out)


def _word_hit(text: str, words: List[str]) -> Optional[str]:
    if not words:
        return None
    norm = _normalize(text)
    # remove non-letters between chars to catch "f.u.c.k"
    stripped = re.sub(r"[^a-z]+", "", norm)
    for w in words:
        wl = w.lower()
        if wl in norm or wl in stripped:
            return w
    return None


def check_message(content: str, rule: dict) -> Optional[Match]:
    """Return a Match if `content` violates `rule` (dict form)."""
    if not rule.get("enabled", True):
        return None
    kind = rule["kind"]
    words = rule.get("words") or []
    threshold = int(rule.get("threshold") or 0)

    if kind == "profanity":
        wl = words or PROFANITY
        hit = _word_hit(content, wl)
        if hit:
            return Match(kind, f"blocked word: {hit}", hit)

    elif kind == "strong_profanity":
        wl = words or STRONG_PROFANITY
        hit = _word_hit(content, wl)
        if hit:
            return Match(kind, f"strong language: {hit}", hit)

    elif kind == "slurs":
        wl = words or SLURS
        hit = _word_hit(content, wl)
        if hit:
            return Match(kind, "hateful content detected", hit)

    elif kind == "insults":
        wl = words or INSULTS
        hit = _word_hit(content, wl)
        if hit:
            return Match(kind, f"insult: {hit}", hit)

    elif kind == "invites":
        m = INVITE_RE.search(content)
        if m:
            return Match(kind, "Discord invite link", m.group(0))

    elif kind == "external_links":
        allowed = set(d.lower() for d in rule.get("allowed_domains") or [])
        for m in URL_RE.finditer(content):
            domain = m.group(1).lower()
            if not any(domain.endswith(a) for a in allowed):
                return Match(kind, f"external link: {domain}", domain)

    elif kind == "mass_mentions":
        n = len(MENTION_RE.findall(content))
        limit = threshold or 5
        if n >= limit:
            return Match(kind, f"{n} mentions (limit {limit})")

    elif kind == "excessive_caps":
        letters = [c for c in content if c.isalpha()]
        if len(letters) >= 8:
            caps = sum(1 for c in letters if c.isupper())
            pct = caps * 100 // len(letters)
            limit = threshold or 70
            if pct >= limit:
                return Match(kind, f"{pct}% caps (limit {limit}%)")

    elif kind == "excessive_emojis":
        n = len(EMOJI_RE.findall(content))
        limit = threshold or 8
        if n >= limit:
            return Match(kind, f"{n} emojis (limit {limit})")

    elif kind == "scam_links":
        lc = content.lower()
        for kw in SCAM_KEYWORDS:
            if kw in lc and URL_RE.search(content):
                return Match(kind, f"suspicious content: {kw}")

    elif kind == "obfuscation":
        # excessive symbol interleaving with letters, e.g. "b.a.d w.o.r.d"
        symbols = re.findall(r"[a-z][^a-z\s]+[a-z]", content.lower())
        if len(symbols) >= (threshold or 3):
            return Match(kind, "character obfuscation")

    elif kind == "custom_regex":
        rx = rule.get("regex")
        if rx:
            try:
                m = re.search(rx, content, re.I)
                if m:
                    return Match(kind, "matched custom pattern", m.group(0))
            except re.error:
                return None

    return None


def is_exempt(rule: dict, user_id: str, role_ids: List[str], channel_id: str) -> bool:
    if user_id in (rule.get("exempt_user_ids") or []):
        return True
    if channel_id in (rule.get("exempt_channel_ids") or []):
        return True
    exempt_roles = set(rule.get("exempt_role_ids") or [])
    if exempt_roles.intersection(role_ids):
        return True
    return False
