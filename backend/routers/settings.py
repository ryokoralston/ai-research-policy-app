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
from models.user import User
from services import audit_log
from services.anthropic_client import invalidate_ai_settings_cache
from services.auth import client_ip, get_current_user
from utils.masking import MASK, mask_secret

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
