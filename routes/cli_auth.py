"""CLI auth status routes — port of server/routes/cli-auth.js."""
import json
import time
import base64
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/cli", tags=["cli-auth"])


@router.get("/claude/status")
async def claude_status():
    try:
        result = _check_claude_credentials()
        return result
    except Exception as e:
        print(f"Error checking Claude auth status: {e}")
        return {"authenticated": False, "email": None, "method": None, "error": str(e)}


@router.get("/codex/status")
async def codex_status():
    try:
        result = _check_codex_credentials()
        return result
    except Exception as e:
        print(f"Error checking Codex auth status: {e}")
        return {"authenticated": False, "email": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Claude credentials check
# ---------------------------------------------------------------------------

def _check_claude_credentials() -> dict:
    # Check ~/.claude/.credentials.json OAuth tokens.
    try:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        creds = json.loads(cred_path.read_text(encoding="utf-8"))
        oauth = creds.get("claudeAiOauth", {})
        if oauth and oauth.get("accessToken"):
            expires_at = oauth.get("expiresAt")
            if expires_at and time.time() * 1000 >= expires_at:
                return {"authenticated": False, "email": None, "method": None}
            return {
                "authenticated": True,
                "email": creds.get("email") or creds.get("user"),
                "method": "credentials_file",
            }
        return {"authenticated": False, "email": None, "method": None}
    except Exception:
        return {"authenticated": False, "email": None, "method": None}


# ---------------------------------------------------------------------------
# Codex credentials check
# ---------------------------------------------------------------------------

def _check_codex_credentials() -> dict:
    try:
        auth_path = Path.home() / ".codex" / "auth.json"
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        tokens = auth.get("tokens", {})

        if tokens.get("id_token") or tokens.get("access_token"):
            email = "Authenticated"
            id_token = tokens.get("id_token")
            if id_token:
                try:
                    parts = id_token.split(".")
                    if len(parts) >= 2:
                        # Add padding
                        padded = parts[1] + "=" * (-len(parts[1]) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(padded))
                        email = payload.get("email") or payload.get("user") or "Authenticated"
                except Exception:
                    pass
            return {"authenticated": True, "email": email}

        # Fallback: OPENAI_API_KEY in auth file
        if auth.get("OPENAI_API_KEY"):
            return {"authenticated": True, "email": "API Key Auth"}

        return {"authenticated": False, "email": None, "error": "No valid tokens found"}
    except FileNotFoundError:
        return {"authenticated": False, "email": None, "error": "Codex not configured"}
    except Exception as e:
        return {"authenticated": False, "email": None, "error": str(e)}
