"""Default blocklists used by AutoMod.

Kept separate so admins can extend or replace them via the dashboard.
The lists below are seeded as SERVER MODERATION DEFAULTS — admins have
full control to add, remove, or disable individual entries from the UI.
"""

# --- General profanity (delete + warn defaults) ---
PROFANITY = [
    "fuck", "fucker", "fucking", "fuckin", "fucked", "fuk", "fuc", "fck",
    "motherfucker", "mf", "mfer", "mofo",
    "shit", "shitty", "shithead", "shitface", "bullshit", "shite", "sh1t",
    "bitch", "bitches", "b1tch", "biatch",
    "asshole", "arsehole", "ahole", "ass",
    "bastard", "bstrd",
    "damn", "goddamn", "gd",
    "crap", "piss", "pissed",
    "prick", "wanker", "twat",
    "dickhead", "dick", "d1ck", "dik",
    "cock", "c0ck", "cocksucker",
    "pussy", "p*ssy", "pu55y",
    "cunt", "c*nt", "kunt",
    "cum", "jizz", "jiz",
    "whore", "hoe", "hoes", "slut", "slutty", "thot",
    "screw you", "fuk u", "fuck you", "stfu",
    "bollocks", "bugger", "arse",
    "wtf", "ffs", "omfg",
]

# --- Strong profanity (delete + warn + short timeout) ---
STRONG_PROFANITY = [
    "motherfucking", "motherfucker", "fuckface", "fucktard",
    "cocksucker", "assfuck", "shithead", "dumbfuck",
    "jackass", "dumbass", "smartass",
]

# --- Hateful slurs / stricter action (delete + warn + longer timeout) ---
# NOTE: These are seeded so admins get real protection out of the box.
# The dashboard exposes this list — admins can add or remove entries any time.
SLURS = [
    # Racial (N-word variants and obfuscations)
    "nigger", "n1gger", "nigga", "n1gga", "nga", "ngr", "nibba", "nibbas",
    "niglet", "negro",
    # Anti-Asian
    "chink", "chinky", "gook", "jap", "jappo",
    # Anti-Latino
    "spic", "wetback", "beaner",
    # Anti-Middle-Eastern / Anti-Muslim
    "sandnigger", "raghead", "towelhead", "camel jockey",
    # Anti-Semitic
    "kike", "yid",
    # Anti-Black additional
    "coon", "porchmonkey", "jigaboo",
    # Anti-LGBTQ (homophobic)
    "faggot", "faggit", "fag", "f4g", "f4ggot", "fgt",
    "dyke", "d1ke",
    # Trans slurs
    "tranny", "trannie",
    # Ableist slurs
    "retard", "retarded", "r3tard", "tard", "sped",
    # Misc identity-based
    "gyp", "gypped",
]

# --- Insults / toxic shortforms ---
INSULTS = [
    "idiot", "moron", "stupid", "dumbass", "loser", "trash",
    "kys", "kms", "kill yourself", "kill urself",
    "gtfo", "stfu", "die",
    "noob", "nub",
    "simp", "incel",
    "clown", "l bozo",
]

# --- Obfuscation reversal map (used to catch f.u.c.k / f u c k etc) ---
OBFUSCATION_MAP = {
    "a": "a4@",
    "b": "b8",
    "e": "e3",
    "i": "i1!|",
    "o": "o0",
    "s": "s5$",
    "t": "t7+",
    "l": "l1",
    "g": "g9",
}

# Kept for backwards compatibility with older import
SLURS_PLACEHOLDER = SLURS
