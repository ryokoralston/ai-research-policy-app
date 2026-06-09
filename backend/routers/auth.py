"""
Auth router — login and status. These endpoints are intentionally NOT protected
by require_auth (you cannot present a token before you have one).

POST /api/auth/login   – exchange the shared password for a bearer token
GET  /api/auth/status  – whether auth is required (so the UI knows to show login)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from config import get_settings
from services.auth import auth_enabled, check_password, create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


@router.get("/status")
async def auth_status() -> dict:
    return {"auth_required": auth_enabled()}


@router.post("/login")
async def login(body: LoginIn) -> dict:
    if not auth_enabled():
        # Auth disabled — hand back an empty token so the UI flow is uniform.
        return {"token": "", "auth_required": False}

    if not check_password(body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )

    return {
        "token": create_token(),
        "auth_required": True,
        "expires_in": get_settings().session_ttl_hours * 3600,
    }
