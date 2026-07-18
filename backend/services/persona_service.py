"""
Persona merge/compose service for the Multi-Persona Policy Debate feature.

Merges the 10 hardcoded built-in personas (templates/personas.py's PERSONAS)
with admin-managed CustomPersona rows (models/custom_persona.py) into one
uniformly-shaped dict, so callers — routers/debate.py, services/debate_service.py,
routers/personas.py, and eventually the frontend's persona picker — never need
to special-case built-in vs custom personas.

Custom personas are organization-specific (e.g. "our VP of Engineering"),
shared across all users, admin-created. Unlike the 10 built-ins (explicitly
fictional per the debate page's disclaimer), a CustomPersona is meant to model
a real internal stakeholder's actual priorities and decision style for
internal decision-support use — see _build_custom_persona_system's docstring.
"""
import re

from sqlalchemy.orm import Session

from models.custom_persona import CustomPersona
from templates.personas import PERSONAS

# ── Built-in persona colors ──────────────────────────────────────────────
# Ported from frontend/src/app/debate/page.tsx's PERSONA_LIST (the source of
# truth for these 10 pairs) so this Python merge function can attach
# color/text_color uniformly to every persona, built-in or custom, without
# the frontend needing its own separate copy of PERSONA_LIST anymore.
BUILTIN_COLORS: dict[str, tuple[str, str]] = {
    "safety_researcher": ("bg-violet-600", "text-violet-100"),
    "tech_ceo": ("bg-blue-600", "text-blue-100"),
    "military": ("bg-slate-600", "text-slate-100"),
    "civil_rights": ("bg-rose-600", "text-rose-100"),
    "intl_relations": ("bg-teal-600", "text-teal-100"),
    "economist": ("bg-amber-600", "text-amber-100"),
    "ethicist": ("bg-emerald-600", "text-emerald-100"),
    "regulator": ("bg-orange-600", "text-orange-100"),
    "global_south": ("bg-cyan-600", "text-cyan-100"),
    "accelerationist": ("bg-red-600", "text-red-100"),
}
_DEFAULT_BUILTIN_COLOR = ("bg-slate-600", "text-slate-100")

# ── Custom persona color palette ─────────────────────────────────────────
# A distinct set of Tailwind bg-*-600/text-*-100 pairs from the 10 built-in
# ones above, so custom (organization-specific) personas read as visually
# distinct at a glance in the UI. Design decision: colors are assigned once
# at creation time by cycling through this list in creation order (see
# assign_custom_color below) — not hashed from the name — so two personas
# created back-to-back always get visibly different colors, and each
# persona's color stays stable across edits (name/title changes don't
# reshuffle it, since it's stored on the row, not recomputed).
CUSTOM_PALETTE: list[tuple[str, str]] = [
    ("bg-indigo-600", "text-indigo-100"),
    ("bg-pink-600", "text-pink-100"),
    ("bg-lime-600", "text-lime-100"),
    ("bg-sky-600", "text-sky-100"),
    ("bg-fuchsia-600", "text-fuchsia-100"),
    ("bg-yellow-600", "text-yellow-100"),
]
_CUSTOM_TEXT_BY_COLOR: dict[str, str] = dict(CUSTOM_PALETTE)
_DEFAULT_CUSTOM_TEXT_COLOR = "text-slate-100"

_BIO_MAX_LEN = 140


def assign_custom_color(existing_count: int) -> str:
    """Return the bg-* Tailwind class for the next custom persona, cycling
    through CUSTOM_PALETTE by creation order. `existing_count` is the number
    of custom_personas rows that already exist at creation time."""
    color, _ = CUSTOM_PALETTE[existing_count % len(CUSTOM_PALETTE)]
    return color


def text_color_for(color: str) -> str:
    """Return the paired text-* Tailwind class for a stored `color` (bg-*
    class), falling back to a sane default for any color not found in
    CUSTOM_PALETTE (defensive — every color a CustomPersona row can carry
    was itself assigned by assign_custom_color, so this should always hit)."""
    return _CUSTOM_TEXT_BY_COLOR.get(color, _DEFAULT_CUSTOM_TEXT_COLOR)


def derive_key(name: str) -> str:
    """Slugify `name` into a persona key, e.g. "Jane Q. Doe" -> "jane_q_doe":
    lowercase, runs of non-alphanumeric characters collapsed to a single
    underscore, leading/trailing underscores trimmed."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def validate_new_key(db: Session, key: str) -> None:
    """Raise ValueError if `key` is empty or collides with a built-in
    PERSONAS key or an existing custom_personas row. Called by the admin
    create endpoint after deriving the key from the submitted name."""
    if not key:
        raise ValueError("Could not derive a valid persona key from that name.")
    if key in PERSONAS:
        raise ValueError(f"'{key}' collides with a built-in persona key.")
    if db.query(CustomPersona).filter(CustomPersona.key == key).first():
        raise ValueError(f"'{key}' is already in use by a custom persona.")


def _summarize_priorities(priorities: str) -> str:
    """Build a short 1-sentence bio for a custom persona from its
    `priorities` field (there's no hand-written bio for these, unlike the 10
    built-ins). Prefers the first sentence if it fits the length budget,
    else truncates cleanly at a word boundary with an ellipsis."""
    text = " ".join(priorities.split())  # collapse whitespace/newlines
    if not text:
        return ""
    first_sentence_end = text.find(". ")
    if 0 < first_sentence_end <= _BIO_MAX_LEN:
        return text[: first_sentence_end + 1]
    if len(text) <= _BIO_MAX_LEN:
        return text if text.endswith((".", "!", "?")) else text + "."
    truncated = text[:_BIO_MAX_LEN].rsplit(" ", 1)[0]
    return truncated.rstrip(".,;: ") + "…"


def _build_custom_persona_system(persona: CustomPersona) -> str:
    """Compose a system prompt for a custom persona in the same voice/shape
    as the 10 hardcoded personas in templates/personas.py: every one of
    those ends on the identical sentence "You never start your response by
    introducing yourself." after a "You are {name}, {title}. [character
    framing]. You speak with [style]." shape. This weaves in `priorities`
    and `style` as flowing prose (not raw field labels) and matches that
    same closing sentence.

    Deliberately does NOT carry the "entirely fictional" framing the 10
    hardcoded personas' surrounding UI disclaims them with — modeling a real
    internal stakeholder's actual tendencies is the whole point of a custom
    persona, not a coincidence to disclaim away.

    Pure function — no I/O, no DB writes — directly unit-testable.
    """
    name = persona.name.strip()
    title = persona.title.strip()
    priorities = persona.priorities.strip()
    style = persona.style.strip()

    return (
        f"You are {name}, {title}, speaking in this policy debate as a stand-in "
        f"for a real stakeholder whose actual priorities and decision style you "
        f"are modeling. You evaluate every proposal through what you genuinely "
        f"care about: {priorities} "
        f"That comes through in how you communicate: {style} "
        f"You never start your response by introducing yourself."
    )


def get_all_personas(db: Session) -> dict[str, dict]:
    """Return every selectable persona — built-in and custom — keyed by
    persona key, each shaped identically:
    {key, name, title, initials, system, bio, color, text_color, is_custom}

    Built-ins come from templates.personas.PERSONAS (bio ported from that
    dict's own `bio` field; color/text_color from BUILTIN_COLORS above).
    Custom personas come from the custom_personas table: their system prompt
    is composed on the fly by _build_custom_persona_system, and their bio is
    synthesized from `priorities` via _summarize_priorities.
    """
    merged: dict[str, dict] = {}

    for key, p in PERSONAS.items():
        color, text_color = BUILTIN_COLORS.get(key, _DEFAULT_BUILTIN_COLOR)
        merged[key] = {
            "key": key,
            "name": p["name"],
            "title": p["title"],
            "initials": p["initials"],
            "system": p["system"],
            "bio": p.get("bio", ""),
            "color": color,
            "text_color": text_color,
            "is_custom": False,
        }

    for row in db.query(CustomPersona).order_by(CustomPersona.created_at).all():
        text_color = text_color_for(row.color)
        merged[row.key] = {
            "key": row.key,
            "name": row.name,
            "title": row.title,
            "initials": row.initials,
            "system": _build_custom_persona_system(row),
            "bio": _summarize_priorities(row.priorities),
            "color": row.color,
            "text_color": text_color,
            "is_custom": True,
        }

    return merged
