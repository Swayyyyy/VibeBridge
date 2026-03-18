"""Agent routes — stub port of server/routes/agent.js.

External API with API key authentication for programmatic access.
Full AI session management (Claude/Codex) comes in Phase 3.
"""
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel

from database.db import user_db, api_keys_db
from config import IS_PLATFORM

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# API key auth middleware (as FastAPI dependency)
# ---------------------------------------------------------------------------

async def validate_api_key(request: Request):
    """Authenticate via API key (or platform mode bypass)."""
    if IS_PLATFORM:
        user = user_db.get_first_user()
        if not user:
            raise HTTPException(500, "Platform mode: No user found in database")
        request.state.user = user
        return user

    auth = request.headers.get("authorization", "")
    api_key = request.headers.get("x-api-key", "")

    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:]
    elif api_key:
        token = api_key

    if not token:
        raise HTTPException(401, "API key required. Provide via Authorization: Bearer <key> or X-API-Key header.")

    result = api_keys_db.validate_api_key(token)
    if not result:
        raise HTTPException(401, "Invalid API key")

    request.state.user = {"id": result["id"], "username": result["username"]}
    return request.state.user


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class QueryBody(BaseModel):
    prompt: str
    provider: str = "claude"
    model: Optional[str] = None
    projectPath: Optional[str] = None
    sessionId: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def agent_status(_=Depends(validate_api_key)):
    return {"status": "ok", "message": "Agent API is running"}


@router.post("/query")
async def agent_query(body: QueryBody, request: Request, _=Depends(validate_api_key)):
    """Send a prompt to Claude or Codex and return the response.

    Stub — full streaming implementation comes in Phase 3.
    """
    raise HTTPException(501, "Agent query not yet implemented in Python backend. Coming in Phase 3.")
