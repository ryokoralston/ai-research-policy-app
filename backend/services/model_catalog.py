"""
Keeps the "latest model per family" catalog in sync with Anthropic's /v1/models
endpoint, so the AI Model Settings dropdown doesn't need a manual edit every
time Anthropic ships a new Opus/Sonnet/Haiku/Fable release.

Family membership is decided by a substring match on the model id (naming
convention); which model is "latest" within a family comes from the API's own
created_at, not from parsing version numbers out of the id — version strings
like "4-8" vs "5" vs "4-6" aren't reliably comparable, but release dates are.
"""
import logging
from datetime import datetime
from typing import Any

import anthropic

from database import SessionLocal
from models.model_catalog import ModelCatalogEntry
from services.anthropic_client import _load_ai_settings

logger = logging.getLogger(__name__)

FAMILY_SUBSTRINGS = {
    "fable": "fable",
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
}

# Mythos is invitation-only (Project Glasswing) — never auto-populate it, since
# most API keys can't actually use it even when it's listed.
EXCLUDED_SUBSTRING = "mythos"


def _family_of(model_id: str) -> str | None:
    if EXCLUDED_SUBSTRING in model_id:
        return None
    for family, needle in FAMILY_SUBSTRINGS.items():
        if needle in model_id:
            return family
    return None


def refresh_model_catalog() -> None:
    """Fetch /v1/models, keep the newest model per family by created_at, upsert into DB."""
    ai_settings = _load_ai_settings()
    key = ai_settings.get("anthropic_api_key")
    if not key:
        logger.info("Model catalog refresh skipped: no Anthropic API key configured.")
        return

    client = anthropic.Anthropic(api_key=key)
    latest: dict[str, Any] = {}
    try:
        for model in client.models.list():
            family = _family_of(model.id)
            if family is None:
                continue
            current = latest.get(family)
            if current is None or model.created_at > current.created_at:
                latest[family] = model
    except Exception:
        logger.exception("Model catalog refresh failed")
        return

    if not latest:
        logger.warning("Model catalog refresh found no matching Opus/Sonnet/Haiku/Fable models")
        return

    with SessionLocal() as db:
        for family, model in latest.items():
            entry = db.get(ModelCatalogEntry, family)
            if entry is None:
                entry = ModelCatalogEntry(family=family)
                db.add(entry)
            entry.model_id = model.id
            entry.display_name = model.display_name
            entry.released_at = model.created_at
            entry.fetched_at = datetime.utcnow()
        db.commit()

    logger.info("Model catalog refreshed: %s", {f: m.id for f, m in latest.items()})
