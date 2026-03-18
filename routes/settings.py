"""Settings routes — 1:1 port of server/routes/settings.js."""
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from database.db import api_keys_db, credentials_db
from middleware.auth import authenticate_token

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ===============================
# API Keys Management
# ===============================

class CreateApiKeyBody(BaseModel):
    keyName: str


class ToggleBody(BaseModel):
    isActive: bool


@router.get("/api-keys")
async def get_api_keys(request: Request, _=Depends(authenticate_token)):
    try:
        keys = api_keys_db.get_api_keys(request.state.user["id"])
        sanitized = [{**k, "api_key": k["api_key"][:10] + "..."} for k in keys]
        return {"apiKeys": sanitized}
    except Exception as e:
        print(f"Error fetching API keys: {e}")
        raise HTTPException(500, "Failed to fetch API keys")


@router.post("/api-keys")
async def create_api_key(body: CreateApiKeyBody, request: Request, _=Depends(authenticate_token)):
    try:
        if not body.keyName or not body.keyName.strip():
            raise HTTPException(400, "Key name is required")
        result = api_keys_db.create_api_key(request.state.user["id"], body.keyName.strip())
        return {"success": True, "apiKey": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating API key: {e}")
        raise HTTPException(500, "Failed to create API key")


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: int, request: Request, _=Depends(authenticate_token)):
    try:
        success = api_keys_db.delete_api_key(request.state.user["id"], key_id)
        if success:
            return {"success": True}
        raise HTTPException(404, "API key not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting API key: {e}")
        raise HTTPException(500, "Failed to delete API key")


@router.patch("/api-keys/{key_id}/toggle")
async def toggle_api_key(key_id: int, body: ToggleBody, request: Request, _=Depends(authenticate_token)):
    try:
        success = api_keys_db.toggle_api_key(request.state.user["id"], key_id, body.isActive)
        if success:
            return {"success": True}
        raise HTTPException(404, "API key not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error toggling API key: {e}")
        raise HTTPException(500, "Failed to toggle API key")


# ===============================
# Generic Credentials Management
# ===============================

class CreateCredentialBody(BaseModel):
    credentialName: str
    credentialType: str
    credentialValue: str
    description: Optional[str] = None


@router.get("/credentials")
async def get_credentials(request: Request, type: Optional[str] = None, _=Depends(authenticate_token)):
    try:
        creds = credentials_db.get_credentials(request.state.user["id"], type)
        return {"credentials": creds}
    except Exception as e:
        print(f"Error fetching credentials: {e}")
        raise HTTPException(500, "Failed to fetch credentials")


@router.post("/credentials")
async def create_credential(body: CreateCredentialBody, request: Request, _=Depends(authenticate_token)):
    try:
        for field, name in [
            (body.credentialName, "Credential name"),
            (body.credentialType, "Credential type"),
            (body.credentialValue, "Credential value"),
        ]:
            if not field or not field.strip():
                raise HTTPException(400, f"{name} is required")

        result = credentials_db.create_credential(
            request.state.user["id"],
            body.credentialName.strip(),
            body.credentialType.strip(),
            body.credentialValue.strip(),
            body.description.strip() if body.description else None,
        )
        return {"success": True, "credential": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating credential: {e}")
        raise HTTPException(500, "Failed to create credential")


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: int, request: Request, _=Depends(authenticate_token)):
    try:
        success = credentials_db.delete_credential(request.state.user["id"], credential_id)
        if success:
            return {"success": True}
        raise HTTPException(404, "Credential not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting credential: {e}")
        raise HTTPException(500, "Failed to delete credential")


@router.patch("/credentials/{credential_id}/toggle")
async def toggle_credential(credential_id: int, body: ToggleBody, request: Request, _=Depends(authenticate_token)):
    try:
        success = credentials_db.toggle_credential(request.state.user["id"], credential_id, body.isActive)
        if success:
            return {"success": True}
        raise HTTPException(404, "Credential not found")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error toggling credential: {e}")
        raise HTTPException(500, "Failed to toggle credential")
