"""
Model settings router.

GET  /api/settings/models  – get current model settings (API keys masked)
PUT  /api/settings/models  – save model settings and invalidate client cache
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_or_init_model_settings
from models.model_catalog import ModelCatalogEntry
from models.user import User
from services import audit_log
from services.anthropic_client import invalidate_ai_settings_cache
from services.auth import client_ip, get_current_user
from utils.masking import MASK, mask_secret

router = APIRouter(prefix="/api/settings", tags=["settings"])

FAMILY_ORDER = ["fable", "opus", "sonnet", "haiku"]

STATIC_OPENAI_MODELS = [
    {"group": "OpenAI", "id": "gpt-4o", "label": "GPT-4o"},
    {"group": "OpenAI", "id": "gpt-4o-mini", "label": "GPT-4o Mini (Fast)"},
]

# Used only if the catalog hasn't been populated yet (no Anthropic API key
# configured, or the very first request before the startup refresh completed).
FALLBACK_ANTHROPIC_MODELS = [
    {"group": "Anthropic", "id": "claude-fable-5", "label": "Claude Fable 5"},
    {"group": "Anthropic", "id": "claude-opus-5", "label": "Claude Opus 5"},
    {"group": "Anthropic", "id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
    {"group": "Anthropic", "id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (Fast)"},
]


class ModelSettingsIn(BaseModel):
    main_model: str | None = None
    fast_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


@router.get("/models")
async def get_model_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return current model settings; API keys are masked."""
    ms = get_or_init_model_settings(db)
    return {
        "main_model": ms.main_model,
        "fast_model": ms.fast_model,
        "anthropic_api_key": mask_secret(ms.anthropic_api_key),
        "openai_api_key": mask_secret(ms.openai_api_key),
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }


@router.get("/available-models")
async def get_available_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Model options for the settings dropdowns. Anthropic entries reflect the
    latest model per family as of the last scheduled catalog refresh (see
    services.model_catalog); OpenAI entries are static since /v1/models only
    covers Anthropic models."""
    entries = {e.family: e for e in db.query(ModelCatalogEntry).all()}
    anthropic_models = [
        {"group": "Anthropic", "id": entries[family].model_id, "label": entries[family].display_name}
        for family in FAMILY_ORDER
        if family in entries
    ]
    if not anthropic_models:
        anthropic_models = FALLBACK_ANTHROPIC_MODELS

    fetched_ats = [e.fetched_at for e in entries.values() if e.fetched_at]
    return {
        "models": anthropic_models + STATIC_OPENAI_MODELS,
        "catalog_updated_at": max(fetched_ats).isoformat() if fetched_ats else None,
    }


@router.put("/models")
async def save_model_settings(
    body: ModelSettingsIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save model settings. Empty string API keys are ignored (keep existing)."""
    ms = get_or_init_model_settings(db)

    changed: list[str] = []
    if body.main_model is not None and body.main_model != ms.main_model:
        changed.append("main_model")
        ms.main_model = body.main_model
    if body.fast_model is not None and body.fast_model != ms.fast_model:
        changed.append("fast_model")
        ms.fast_model = body.fast_model
    # Only update API keys when a real new value is sent — ignore empty strings
    # and the masked sentinel a client may echo back from GET.
    if body.anthropic_api_key and body.anthropic_api_key != MASK:
        changed.append("anthropic_api_key")
        ms.anthropic_api_key = body.anthropic_api_key
    if body.openai_api_key and body.openai_api_key != MASK:
        changed.append("openai_api_key")
        ms.openai_api_key = body.openai_api_key

    ms.updated_at = datetime.utcnow()

    if changed:
        audit_log.record(db, user=current_user, action="settings.model_settings.update",
                          resource_type="model_settings", detail=f"updated: {', '.join(changed)}",
                          ip_address=client_ip(request))

    db.commit()
    db.refresh(ms)

    invalidate_ai_settings_cache()

    return {
        "main_model": ms.main_model,
        "fast_model": ms.fast_model,
        "anthropic_api_key": mask_secret(ms.anthropic_api_key),
        "openai_api_key": mask_secret(ms.openai_api_key),
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }
