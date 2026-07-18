"""
Persona listing for the Multi-Persona Policy Debate feature — open to any
authenticated user (see main.py: this router carries Depends(get_current_user)
at include_router time), since any logged-in user can select and use built-in
or custom personas in a debate. Creating/editing/deleting custom personas is
a separate, admin-only concern — see routers/admin_personas.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from services.persona_service import get_all_personas

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("/")
def list_personas(db: Session = Depends(get_db)) -> list[dict]:
    """Return every selectable persona (10 built-in + any custom), as a
    JSON list — each entry shaped:
        {key, name, title, initials, system, bio, color, text_color, is_custom}
    Order: built-ins first (PERSONAS dict order), then custom personas in
    creation order (see services.persona_service.get_all_personas)."""
    return list(get_all_personas(db).values())
