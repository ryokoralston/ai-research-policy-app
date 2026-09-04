"""
Model settings router.

GET  /api/settings/models  – get current model settings (API keys masked)
PUT  /api/settings/models  – save model settings and invalidate client cache
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_or_init_model_settings
from models.model_catalog import ModelCatalogEntry
from models.user import User
from services import audit_log
from services.anthropic_client import invalidate_ai_settings_cache
from services.auth import client_ip, require_admin
from services.model_catalog import FALLBACK_ANTHROPIC_MODELS, allowed_model_ids
from utils.masking import MASK, mask_secret

router = APIRouter(prefix="/api/settings", tags=["settings"])

FAMILY_ORDER = ["fable", "opus", "sonnet", "haiku"]


class ModelSettingsIn(BaseModel):
    main_model: str | None = None
    fast_model: str | None = None
    anthropic_api_key: str | None = None


@router.get("/models")
async def get_model_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return current model settings; API keys are masked."""
    ms = get_or_init_model_settings(db)
    return {
        "main_model": ms.main_model,
        "fast_model": ms.fast_model,
        "anthropic_api_key": mask_secret(ms.anthropic_api_key),
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }


@router.get("/available-models")
async def get_available_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Model options for the settings dropdowns. Entries reflect the latest
    model per family as of the last scheduled catalog refresh (see
    services.model_catalog)."""
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
        "models": anthropic_models,
        "catalog_updated_at": max(fetched_ats).isoformat() if fetched_ats else None,
    }


@router.put("/models")
async def save_model_settings(
    body: ModelSettingsIn,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save model settings. Empty string API keys are ignored (keep existing).

    Admin-only: `model_settings` is a single global row, so a member saving here
    would swap the API key and model choice for every user of the deployment.
    """
    ms = get_or_init_model_settings(db)

    # The settings form always echoes the currently-stored main_model /
    # fast_model back in the PUT body (see frontend/src/app/settings/page.tsx),
    # even when the admin is only rotating the API key. Only validate against
    # the allowlist when the value is actually changing — re-saving the
    # stored default (which may predate the allowlist, e.g. the model_settings
    # table default "claude-opus-4-6") must not 400 a routine save. A new
    # value that isn't allowed (e.g. a stale "gpt-4o") still 400s either way.
    main_model_changing = body.main_model is not None and body.main_model != ms.main_model
    fast_model_changing = body.fast_model is not None and body.fast_model != ms.fast_model
    if main_model_changing or fast_model_changing:
        allowed = allowed_model_ids(db)
        if main_model_changing and body.main_model not in allowed:
            raise HTTPException(status_code=400, detail="Unknown model id.")
        if fast_model_changing and body.fast_model not in allowed:
            raise HTTPException(status_code=400, detail="Unknown model id.")

    changed: list[str] = []
    if main_model_changing:
        changed.append("main_model")
        ms.main_model = body.main_model
    if fast_model_changing:
        changed.append("fast_model")
        ms.fast_model = body.fast_model
    # Only update API keys when a real new value is sent — ignore empty strings
    # and the masked sentinel a client may echo back from GET.
    if body.anthropic_api_key and body.anthropic_api_key != MASK:
        changed.append("anthropic_api_key")
        ms.anthropic_api_key = body.anthropic_api_key

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
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }
