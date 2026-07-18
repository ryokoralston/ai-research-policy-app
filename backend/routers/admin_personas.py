"""
Custom persona management — admin-only (see main.py: this router carries
Depends(require_admin) at include_router time). Custom personas are shared
across all users (any authenticated user can select and use them in a
debate via routers/personas.py + routers/debate.py) — only creating,
editing, and deleting them is restricted to admins.

GET    /api/admin/personas/       – list custom personas with full editable fields
POST   /api/admin/personas/       – create a custom persona
PUT    /api/admin/personas/{key}  – update a custom persona
DELETE /api/admin/personas/{key}  – delete a custom persona

The GET here is distinct from routers/personas.py's GET /api/personas/ (open
to any user, merged with built-ins, no `priorities`/`style` in its response
shape) — this one is admin-only and includes the raw `priorities`/`style`
fields so the admin edit form can be pre-filled without asking the admin to
retype them from scratch.

PUT/DELETE 404 on any `key` not present in custom_personas — which, since
built-in PERSONAS keys were never rows in this table, also naturally
protects the 10 built-in personas from being "edited" or "deleted" via this
router. `key` itself is immutable after creation (it's what
DebateArgument.persona_key and Debate.personas reference for past debates,
so changing it on rename would silently break history) — PUT only updates
name/title/initials/priorities/style.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.custom_persona import CustomPersona
from models.user import User
from services.auth import require_admin
from services.persona_service import assign_custom_color, derive_key, text_color_for, validate_new_key

router = APIRouter(prefix="/api/admin/personas", tags=["admin-personas"])


class PersonaCreateIn(BaseModel):
    name: str
    title: str
    initials: str
    priorities: str
    style: str


class PersonaUpdateIn(BaseModel):
    name: str
    title: str
    initials: str
    priorities: str
    style: str


def _serialize(p: CustomPersona) -> dict[str, Any]:
    return {
        "key": p.key,
        "name": p.name,
        "title": p.title,
        "initials": p.initials,
        "color": p.color,
        "text_color": text_color_for(p.color),
        "priorities": p.priorities,
        "style": p.style,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "is_custom": True,
    }


def _validate_fields(name: str, title: str, initials: str, priorities: str, style: str) -> None:
    if not name.strip() or not title.strip() or not priorities.strip() or not style.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="name, title, priorities, and style are all required.",
        )
    if not (1 <= len(initials.strip()) <= 3):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="initials must be 1-3 characters (e.g. 'JD').",
        )


@router.get("/")
async def list_custom_personas(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    personas = db.query(CustomPersona).order_by(CustomPersona.created_at).all()
    return [_serialize(p) for p in personas]


@router.post("/")
async def create_persona(
    body: PersonaCreateIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _validate_fields(body.name, body.title, body.initials, body.priorities, body.style)

    key = derive_key(body.name)
    try:
        validate_new_key(db, key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    existing_count = db.query(CustomPersona).count()
    color = assign_custom_color(existing_count)

    persona = CustomPersona(
        key=key,
        name=body.name.strip(),
        title=body.title.strip(),
        initials=body.initials.strip().upper(),
        color=color,
        priorities=body.priorities.strip(),
        style=body.style.strip(),
        created_by=current_user.id,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return _serialize(persona)


@router.put("/{key}")
async def update_persona(
    key: str,
    body: PersonaUpdateIn,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    persona = db.query(CustomPersona).filter(CustomPersona.key == key).first()
    if not persona:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom persona not found")

    _validate_fields(body.name, body.title, body.initials, body.priorities, body.style)

    persona.name = body.name.strip()
    persona.title = body.title.strip()
    persona.initials = body.initials.strip().upper()
    persona.priorities = body.priorities.strip()
    persona.style = body.style.strip()
    persona.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(persona)
    return _serialize(persona)


@router.delete("/{key}")
async def delete_persona(
    key: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    persona = db.query(CustomPersona).filter(CustomPersona.key == key).first()
    if not persona:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom persona not found")

    db.delete(persona)
    db.commit()
    return {"deleted": key}
