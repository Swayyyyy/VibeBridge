"""JWT authentication middleware — 1:1 port of server/middleware/auth.js."""
import time
from typing import Optional

import jwt
from fastapi import Request, Response, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from database.db import user_db, app_config_db
from config import IS_PLATFORM, MAIN_SERVER_URL, MAIN_REGISTER_URL, JWT_SECRET_VALUE

# JWT secret: config file > auto-generated per installation
JWT_SECRET = JWT_SECRET_VALUE or app_config_db.get_or_create_jwt_secret()

_bearer_scheme = HTTPBearer(auto_error=False)


def generate_token(user: dict) -> str:
    now = int(time.time())
    payload = {
        "userId": user["id"],
        "username": user["username"],
        "iat": now,
        "exp": now + 7 * 24 * 3600,  # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _verify_token(token: str) -> Optional[dict]:
    """Verify JWT and return decoded payload, or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def authenticate_token(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
):
    """FastAPI dependency that mimics Express authenticateToken middleware."""

    # Node mode: trust Main to forward the already-authenticated browser user.
    if MAIN_SERVER_URL or MAIN_REGISTER_URL:
        forwarded_user_id = request.headers.get("x-authenticated-user-id")
        forwarded_username = request.headers.get("x-authenticated-username")

        if forwarded_user_id and forwarded_username:
            try:
                user = user_db.ensure_shadow_user(int(forwarded_user_id), forwarded_username)
            except ValueError as exc:
                raise HTTPException(400, "Invalid forwarded user context") from exc

            request.state.user = user
            return user

        # Fallback for internal Node-only calls that do not originate from Main.
        request.state.user = {"id": 0, "username": "node"}
        return request.state.user

    # Platform mode: use first DB user
    if IS_PLATFORM:
        user = user_db.get_first_user()
        if not user:
            raise HTTPException(500, "Platform mode: No user found in database")
        request.state.user = user
        return user

    # Extract token from Bearer header or query param
    token = None
    if credentials:
        token = credentials.credentials
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Access denied. No token provided.")

    decoded = _verify_token(token)
    if not decoded:
        raise HTTPException(403, "Invalid token")

    user = user_db.get_user_by_id(decoded["userId"])
    if not user:
        raise HTTPException(401, "Invalid token. User not found.")

    # Auto-refresh: if past halfway through lifetime
    exp = decoded.get("exp")
    iat = decoded.get("iat")
    if exp and iat:
        now = int(time.time())
        half_life = (exp - iat) / 2
        if now > iat + half_life:
            new_token = generate_token(user)
            response.headers["X-Refreshed-Token"] = new_token

    request.state.user = user
    return user


def authenticate_websocket(token: Optional[str]) -> Optional[dict]:
    """WebSocket authentication — returns user dict or None."""
    if IS_PLATFORM:
        user = user_db.get_first_user()
        if user:
            return {"userId": user["id"], "username": user["username"]}
        return None

    if not token:
        return None

    decoded = _verify_token(token)
    if not decoded:
        return None

    user = user_db.get_user_by_id(decoded["userId"])
    if not user:
        return None
    return {"userId": user["id"], "username": user["username"]}
