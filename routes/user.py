"""User routes — 1:1 port of server/routes/user.js."""
import re

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel

from database.db import user_db, user_settings_db
from middleware.auth import authenticate_token

router = APIRouter(prefix="/api/user", tags=["user"])


class GitConfigBody(BaseModel):
    gitName: str
    gitEmail: str


class PreferencesBody(BaseModel):
    settings: dict[str, str | None]


@router.get("/git-config")
async def get_git_config(request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]
        git_config = user_db.get_git_config(user_id)

        return {
            "success": True,
            "gitName": git_config.get("git_name") if git_config else None,
            "gitEmail": git_config.get("git_email") if git_config else None,
        }
    except Exception as e:
        print(f"Error getting git config: {e}")
        raise HTTPException(500, "Failed to get git configuration")


@router.post("/git-config")
async def update_git_config(body: GitConfigBody, request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]

        if not body.gitName or not body.gitEmail:
            raise HTTPException(400, "Git name and email are required")

        email_re = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
        if not email_re.match(body.gitEmail):
            raise HTTPException(400, "Invalid email format")

        user_db.update_git_config(user_id, body.gitName, body.gitEmail)

        return {"success": True, "gitName": body.gitName, "gitEmail": body.gitEmail}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating git config: {e}")
        raise HTTPException(500, "Failed to update git configuration")


@router.post("/complete-onboarding")
async def complete_onboarding(request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]
        user_db.complete_onboarding(user_id)
        return {"success": True, "message": "Onboarding completed successfully"}
    except Exception as e:
        print(f"Error completing onboarding: {e}")
        raise HTTPException(500, "Failed to complete onboarding")


@router.get("/onboarding-status")
async def onboarding_status(request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]
        has_completed = user_db.has_completed_onboarding(user_id)
        return {"success": True, "hasCompletedOnboarding": has_completed}
    except Exception as e:
        print(f"Error checking onboarding status: {e}")
        raise HTTPException(500, "Failed to check onboarding status")


@router.get("/preferences")
async def get_preferences(request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]
        return {"success": True, "settings": user_settings_db.get_settings(user_id)}
    except Exception as e:
        print(f"Error getting user preferences: {e}")
        raise HTTPException(500, "Failed to get user preferences")


@router.put("/preferences")
async def update_preferences(body: PreferencesBody, request: Request, _=Depends(authenticate_token)):
    try:
        user_id = request.state.user["id"]
        user_settings_db.set_settings(user_id, body.settings or {})
        return {"success": True}
    except Exception as e:
        print(f"Error updating user preferences: {e}")
        raise HTTPException(500, "Failed to update user preferences")
