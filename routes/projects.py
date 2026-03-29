"""Projects routes — port of server/routes/projects.js.

Workspace creation, validation, git clone with SSE progress.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from middleware.auth import authenticate_token
from database.db import apply_custom_session_names, session_names_db

router = APIRouter(prefix="/api/projects", tags=["projects"])

FORBIDDEN_PATHS = [
    "/", "/etc", "/bin", "/sbin", "/usr", "/dev", "/proc", "/sys",
    "/var", "/boot", "/root", "/lib", "/lib64", "/opt", "/tmp", "/run",
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData", "C:\\System Volume Information", "C:\\$Recycle.Bin",
]


async def validate_workspace_path(requested_path: str) -> dict:
    """Validate that a path is safe for workspace operations."""
    try:
        absolute = os.path.abspath(requested_path)
        normalized = os.path.normpath(absolute)

        if normalized in FORBIDDEN_PATHS or normalized == "/":
            return {"valid": False, "error": "Cannot use system-critical directories as workspace locations"}

        for forbidden in FORBIDDEN_PATHS:
            if normalized == forbidden or normalized.startswith(forbidden + os.sep):
                if forbidden == "/var" and (normalized.startswith("/var/tmp") or normalized.startswith("/var/folders")):
                    continue
                return {"valid": False, "error": f"Cannot create workspace in system directory: {forbidden}"}

        # Resolve symlinks
        if os.path.exists(absolute):
            real_path = os.path.realpath(absolute)
        else:
            parent = os.path.dirname(absolute)
            if os.path.exists(parent):
                real_path = os.path.join(os.path.realpath(parent), os.path.basename(absolute))
            else:
                real_path = absolute

        return {"valid": True, "resolvedPath": real_path}
    except Exception as e:
        return {"valid": False, "error": f"Path validation failed: {e}"}


# ---------------------------------------------------------------------------
# Project config persistence (manual project addition)
# ---------------------------------------------------------------------------

_PROJECT_CONFIG_PATH = Path.home() / ".claude" / "project-config.json"


def _load_project_config() -> dict:
    try:
        return json.loads(_PROJECT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"projects": []}


def _save_project_config(config: dict):
    _PROJECT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROJECT_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def add_project_manually(project_path: str) -> dict:
    """Add a project path to the config file; return a project dict."""
    config = _load_project_config()
    projects = config.get("projects", [])

    # Check if already exists
    for p in projects:
        if p.get("path") == project_path:
            return p

    project = {
        "path": project_path,
        "name": os.path.basename(project_path),
        "manuallyAdded": True,
    }
    projects.append(project)
    config["projects"] = projects
    _save_project_config(config)
    return project


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateWorkspaceBody(BaseModel):
    workspaceType: str  # "existing" | "new"
    path: str
    githubUrl: Optional[str] = None
    githubTokenId: Optional[int] = None
    newGithubToken: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_projects(_=Depends(authenticate_token)):
    """List all discovered projects."""
    from projects import get_projects
    try:
        projects = await get_projects()
        return projects
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/create")
async def create_project(request: Request, _=Depends(authenticate_token)):
    """Manually add a project path."""
    body = await request.json()
    raw_project_path = (body.get("path") or "").strip()
    if not raw_project_path:
        raise HTTPException(400, "Project path is required")

    expanded_path = os.path.expanduser(os.path.expandvars(raw_project_path))
    validation = await validate_workspace_path(expanded_path)
    if not validation["valid"]:
        raise HTTPException(400, validation["error"])

    resolved_path = validation.get("resolvedPath") or os.path.abspath(expanded_path)
    if not os.path.exists(resolved_path):
        raise HTTPException(404, "Workspace path does not exist")
    if not os.path.isdir(resolved_path):
        raise HTTPException(400, "Workspace path must be a directory")

    project = add_project_manually(resolved_path)
    return {"success": True, "project": project}


@router.get("/{project_name}/sessions")
async def list_sessions(
    request: Request,
    project_name: str,
    limit: int = 5,
    offset: int = 0,
    provider: str = "claude",
    projectPath: Optional[str] = None,
    _=Depends(authenticate_token),
):
    from projects import extract_project_directory, get_codex_sessions, get_sessions
    try:
        normalized_provider = "codex" if provider == "codex" else "claude"
        if normalized_provider == "codex":
            project_path = projectPath or await extract_project_directory(project_name)
            all_sessions = await get_codex_sessions(project_path, 0)
            paginated_sessions = all_sessions[offset: offset + limit]
            apply_custom_session_names(paginated_sessions, "codex", request.state.user["id"])
            result = {
                "sessions": paginated_sessions,
                "hasMore": (offset + len(paginated_sessions)) < len(all_sessions),
                "total": len(all_sessions),
                "offset": offset,
                "limit": limit,
            }
        else:
            result = await get_sessions(project_name, limit, offset)
            apply_custom_session_names(result.get("sessions", []), "claude", request.state.user["id"])
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/{project_name}/sessions/{session_id}/messages")
async def list_session_messages(
    project_name: str, session_id: str,
    limit: Optional[int] = None, offset: int = 0,
    _=Depends(authenticate_token),
):
    from projects import get_session_messages
    try:
        result = await get_session_messages(project_name, session_id, limit, offset)
        if isinstance(result, list):
            return {"messages": result}
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/{project_name}/rename")
async def rename_project_endpoint(project_name: str, request: Request, _=Depends(authenticate_token)):
    body = await request.json()
    display_name = body.get("displayName", "")
    # Update project config
    config = _load_project_config()
    for p in config.get("projects", []):
        if p.get("name") == project_name or p.get("path", "").endswith(project_name):
            p["displayName"] = display_name
    _save_project_config(config)
    return {"success": True}


@router.delete("/{project_name}/sessions/{session_id}")
async def delete_session_endpoint(project_name: str, session_id: str, user=Depends(authenticate_token)):
    from projects import delete_session
    try:
        await delete_session(project_name, session_id)
        session_names_db.delete_name(session_id, "claude", user.get("id") if isinstance(user, dict) else None)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/{project_name}")
async def delete_project_endpoint(project_name: str, force: bool = False, _=Depends(authenticate_token)):
    from projects import delete_project
    try:
        await delete_project(project_name, force)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/create-workspace")
async def create_workspace(body: CreateWorkspaceBody, request: Request, _=Depends(authenticate_token)):
    if not body.workspaceType or not body.path:
        raise HTTPException(400, "workspaceType and path are required")
    if body.workspaceType not in ("existing", "new"):
        raise HTTPException(400, 'workspaceType must be "existing" or "new"')

    validation = await validate_workspace_path(body.path)
    if not validation["valid"]:
        raise HTTPException(400, validation["error"])

    abs_path = validation["resolvedPath"]

    if body.workspaceType == "existing":
        if not os.path.isdir(abs_path):
            if not os.path.exists(abs_path):
                raise HTTPException(404, "Workspace path does not exist")
            raise HTTPException(400, "Path exists but is not a directory")

        project = add_project_manually(abs_path)
        return {"success": True, "project": project, "message": "Existing workspace added successfully"}

    # New workspace
    os.makedirs(abs_path, exist_ok=True)

    if body.githubUrl:
        github_token = body.newGithubToken
        if body.githubTokenId:
            github_token = _get_github_token(body.githubTokenId, request.state.user["id"])
            if not github_token:
                _safe_rmdir(abs_path)
                raise HTTPException(404, "GitHub token not found")

        normalized_url = body.githubUrl.rstrip("/").removesuffix(".git")
        repo_name = normalized_url.split("/")[-1] or "repository"
        clone_path = os.path.join(abs_path, repo_name)

        if os.path.exists(clone_path):
            raise HTTPException(409, f'Directory "{clone_path}" already exists.')

        try:
            await _clone_repo(body.githubUrl, clone_path, github_token)
        except Exception as e:
            _safe_rmdir(clone_path)
            raise HTTPException(500, f"Failed to clone repository: {e}")

        project = add_project_manually(clone_path)
        return {"success": True, "project": project, "message": "New workspace created and repository cloned successfully"}

    project = add_project_manually(abs_path)
    return {"success": True, "project": project, "message": "New workspace created successfully"}


@router.get("/clone-progress")
async def clone_progress(
    request: Request,
    path: str = "",
    githubUrl: str = "",
    githubTokenId: Optional[int] = None,
    newGithubToken: Optional[str] = None,
    _=Depends(authenticate_token),
):
    """SSE endpoint that streams git clone progress."""

    async def _generate():
        def _event(typ: str, data: dict):
            return f"data: {json.dumps({'type': typ, **data})}\n\n"

        if not path or not githubUrl:
            yield _event("error", {"message": "workspacePath and githubUrl are required"})
            return

        validation = await validate_workspace_path(path)
        if not validation["valid"]:
            yield _event("error", {"message": validation["error"]})
            return

        abs_path = validation["resolvedPath"]
        os.makedirs(abs_path, exist_ok=True)

        github_token = newGithubToken
        if githubTokenId:
            github_token = _get_github_token(githubTokenId, request.state.user["id"])
            if not github_token:
                _safe_rmdir(abs_path)
                yield _event("error", {"message": "GitHub token not found"})
                return

        normalized = githubUrl.rstrip("/").removesuffix(".git")
        repo_name = normalized.split("/")[-1] or "repository"
        clone_path = os.path.join(abs_path, repo_name)

        if os.path.exists(clone_path):
            yield _event("error", {"message": f'Directory "{repo_name}" already exists.'})
            return

        clone_url = _inject_token(githubUrl, github_token)
        yield _event("progress", {"message": f"Cloning into '{repo_name}'..."})

        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--progress", clone_url, clone_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

        last_error = ""

        async def _read_stream(stream):
            nonlocal last_error
            pending = ""
            while True:
                chunk = await stream.read(64 * 1024)
                if not chunk:
                    tail = pending.strip()
                    if tail:
                        last_error = tail
                    break

                pending += chunk.decode(errors="replace")
                parts = re.split(r"[\r\n]+", pending)
                pending = parts.pop()

                for part in parts:
                    msg = part.strip()
                    if msg:
                        last_error = msg

        # Read both streams
        await asyncio.gather(
            _read_stream(proc.stdout),
            _read_stream(proc.stderr),
        )
        code = await proc.wait()

        if code == 0:
            project = add_project_manually(clone_path)
            yield _event("complete", {"project": project, "message": "Repository cloned successfully"})
        else:
            sanitized = _sanitize_error(last_error, github_token)
            if "Authentication failed" in last_error or "could not read Username" in last_error:
                sanitized = "Authentication failed. Please check your credentials."
            elif "Repository not found" in last_error:
                sanitized = "Repository not found. Please check the URL and ensure you have access."
            elif "already exists" in last_error:
                sanitized = "Directory already exists"
            _safe_rmdir(clone_path)
            yield _event("error", {"message": sanitized or "Git clone failed"})

    return StreamingResponse(_generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_github_token(token_id: int, user_id: int) -> Optional[str]:
    from database.db import db
    row = db.execute(
        "SELECT credential_value FROM user_credentials WHERE id = ? AND user_id = ? AND credential_type = ? AND is_active = 1",
        (token_id, user_id, "github_token"),
    ).fetchone()
    return row["credential_value"] if row else None


def _inject_token(url: str, token: Optional[str]) -> str:
    if not token:
        return url
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        return urlunparse(parsed._replace(netloc=f"{token}@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")))
    except Exception:
        return url


def _sanitize_error(message: str, token: Optional[str]) -> str:
    if not message or not token:
        return message
    return message.replace(token, "***")


async def _clone_repo(url: str, dest: str, token: Optional[str] = None):
    clone_url = _inject_token(url, token)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--progress", clone_url, dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        if "Authentication failed" in err:
            raise RuntimeError("Authentication failed. Please check your GitHub token.")
        if "Repository not found" in err:
            raise RuntimeError("Repository not found.")
        raise RuntimeError(err or "Git clone failed")


def _safe_rmdir(path: str):
    import shutil
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
