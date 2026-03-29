"""Main-owned account and admin user management routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database.db import user_db
from middleware.auth import authenticate_token, require_creator, require_staff

router = APIRouter(prefix="/api/account", tags=["account"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserRoleBody(BaseModel):
    role: str


@router.get("/profile")
async def get_profile(request: Request, _=Depends(authenticate_token)):
    user = request.state.user
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role", "user"),
            "nodeRegisterToken": user.get("node_register_token") if user.get("role") != "pending" else None,
        },
    }


@router.post("/node-register-token/rotate")
async def rotate_node_register_token(request: Request, _=Depends(authenticate_token)):
    if request.state.user.get("role") == "pending":
        raise HTTPException(403, "Pending users cannot rotate node tokens")
    user_id = request.state.user["id"]
    token = user_db.rotate_node_register_token(user_id)
    return {"success": True, "nodeRegisterToken": token}


@admin_router.get("/users")
async def list_users(request: Request, _=Depends(authenticate_token)):
    require_staff(request)
    return {"success": True, "users": user_db.list_users()}


@admin_router.post("/users/{user_id}/approve")
async def approve_user(user_id: int, request: Request, _=Depends(authenticate_token)):
    require_staff(request)
    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.get("role") != "pending":
        raise HTTPException(400, "Only pending users can be approved")

    success = user_db.update_role(user_id, "user")
    if not success:
        raise HTTPException(404, "User not found")
    return {"success": True, "user": user_db.get_user_by_id(user_id)}


@admin_router.post("/users/{user_id}/role")
async def update_user_role(user_id: int, body: UserRoleBody, request: Request, _=Depends(authenticate_token)):
    require_creator(request)
    if body.role not in {"admin", "user", "pending"}:
        raise HTTPException(400, "Role must be admin, user, or pending")

    users = user_db.list_users()
    creator_count = sum(1 for user in users if user.get("role") == "creator")
    target = next((user for user in users if user["id"] == user_id), None)
    if not target:
        raise HTTPException(404, "User not found")
    if target.get("role") == "creator":
        raise HTTPException(400, "Creator role cannot be reassigned")
    if target.get("role") == "admin" and body.role != "admin":
        remaining_admins = sum(1 for user in users if user.get("role") == "admin" and user["id"] != user_id)
        if creator_count == 0 and remaining_admins <= 0:
            raise HTTPException(400, "Cannot remove the last elevated user")

    success = user_db.update_role(user_id, body.role)
    if not success:
        raise HTTPException(404, "User not found")
    return {"success": True, "user": user_db.get_user_by_id(user_id)}


@admin_router.post("/users/{user_id}/node-register-token/rotate")
async def admin_rotate_node_register_token(user_id: int, request: Request, _=Depends(authenticate_token)):
    require_creator(request)
    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")
    token = user_db.rotate_node_register_token(user_id)
    return {"success": True, "nodeRegisterToken": token}


@admin_router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, _=Depends(authenticate_token)):
    current_user = require_staff(request)
    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")

    if current_user.get("id") == user_id:
        raise HTTPException(400, "You cannot delete your own account")

    current_role = current_user.get("role")
    target_role = target.get("role")

    if current_role == "admin":
        if target_role != "user":
            raise HTTPException(403, "Admins can only delete normal users")
    elif current_role != "creator":
        raise HTTPException(403, "Staff access required")

    success = user_db.delete_user(user_id)
    if not success:
        raise HTTPException(404, "User not found")
    return {"success": True, "userId": user_id}
