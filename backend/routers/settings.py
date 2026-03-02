"""
Model settings router.

GET  /api/settings/models  – get current model settings (API keys masked)
PUT  /api/settings/models  – save model settings and invalidate client cache
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_or_init_model_settings
from services.anthropic_client import invalidate_ai_settings_cache

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ModelSettingsIn(BaseModel):
    main_model: str | None = None
    fast_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


def _mask(value: str) -> str:
    return "***" if value else ""


@router.get("/models")
async def get_model_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return current model settings; API keys are masked."""
    ms = get_or_init_model_settings(db)
    return {
        "main_model": ms.main_model,
        "fast_model": ms.fast_model,
        "anthropic_api_key": _mask(ms.anthropic_api_key),
        "openai_api_key": _mask(ms.openai_api_key),
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }


@router.put("/models")
async def save_model_settings(
    body: ModelSettingsIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save model settings. Empty string API keys are ignored (keep existing)."""
    ms = get_or_init_model_settings(db)

    if body.main_model is not None:
        ms.main_model = body.main_model
    if body.fast_model is not None:
        ms.fast_model = body.fast_model
    # Only update API keys when a non-empty value is sent
    if body.anthropic_api_key:
        ms.anthropic_api_key = body.anthropic_api_key
    if body.openai_api_key:
        ms.openai_api_key = body.openai_api_key

    ms.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ms)

    invalidate_ai_settings_cache()

    return {
        "main_model": ms.main_model,
        "fast_model": ms.fast_model,
        "anthropic_api_key": _mask(ms.anthropic_api_key),
        "openai_api_key": _mask(ms.openai_api_key),
        "updated_at": ms.updated_at.isoformat() if ms.updated_at else None,
    }
