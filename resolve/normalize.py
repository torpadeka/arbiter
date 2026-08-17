"""Surface-form normalization for entity resolution.

Everything here is deterministic and cheap. The expensive, ambiguous cases are
routed to scoring (features.py) and, only in the narrow uncertain band, to an
LLM adjudicator.
"""

from __future__ import annotations

import re
import unicodedata

# Deliberately small and conservative. A wrong nickname pair silently fuses two
# real people, which is the worst failure this system can have.
NICKNAMES: dict[str, str] = {
    "sam": "samuel", "sammy": "samuel", "soham": "samuel",
    "mike": "michael", "mikey": "michael",
    "bob": "robert", "rob": "robert", "bobby": "robert",
    "will": "william", "bill": "william", "billy": "william",
    "tom": "thomas", "tommy": "thomas",
    "chris": "christopher", "kate": "katherine", "katie": "katherine",
    "dave": "david", "danny": "daniel", "nick": "nicholas",
    "steve": "stephen", "jim": "james", "jimmy": "james",
}

TITLES = {"mr", "mrs", "ms", "dr", "prof", "sir"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def is_email(text: str) -> bool:
    return bool(EMAIL_RE.fullmatch((text or "").strip()))


def email_local(text: str) -> str:
    return (text or "").split("@", 1)[0].lower()


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def norm(text: str) -> str:
    """Casefold, de-accent, drop punctuation and handle sigils."""
    text = strip_accents(str(text or "")).lower().strip()
    text = text.lstrip("@")
    text = re.sub(r"[._\-]+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    """Normalized name tokens with titles removed and nicknames canonicalized."""
    out = []
    for tok in norm(text).split():
        if tok in TITLES:
            continue
        out.append(NICKNAMES.get(tok, tok))
    return out


def surface_tokens(text: str) -> list[str]:
    """Tokens for any surface form, including emails and handles.

    `sam.ratnaparkhi@northwind.com` -> ['samuel', 'ratnaparkhi']
    `soham-r`                       -> ['samuel', 'r']
    """
    raw = email_local(text) if is_email(text) else text
    return tokens(raw)


def is_initial(tok: str) -> bool:
    return len(tok) == 1


def is_handle(surface: str) -> bool:
    """A machine-style identifier (`soham-r`, `@wei`) rather than a written name.

    Handles routinely abbreviate a surname to a letter; written names do not,
    so the two forms get different matching leniency.
    """
    text = str(surface or "").strip()
    return bool(text) and " " not in text and not is_email(text)


def last_token(toks: list[str]) -> str:
    return toks[-1] if toks else ""


def first_token(toks: list[str]) -> str:
    return toks[0] if toks else ""


def blocking_keys(surface: str, email: str = "") -> set[str]:
    """Cheap keys that put plausible matches in the same bucket.

    Recall matters more than precision here — scoring filters afterwards, but a
    pair that never shares a bucket can never be compared at all.
    """
    keys: set[str] = set()
    toks = surface_tokens(surface)
    if email:
        keys.add(f"email:{email.lower()}")
        for tok in surface_tokens(email):
            if len(tok) > 2:
                keys.add(f"tok:{tok}")
    if toks:
        keys.add(f"full:{' '.join(toks)}")
        last = last_token(toks)
        if len(last) > 2:
            keys.add(f"last:{last}")
            keys.add(f"lastpre:{last[:4]}")           # nandakumar / nandakuma typos
            keys.add(f"li:{first_token(toks)[:1]}{last}")  # S. Ratnaparkhi <-> Sam Ratnaparkhi
        for tok in toks:
            if len(tok) > 2:
                keys.add(f"tok:{tok}")
    return keys
