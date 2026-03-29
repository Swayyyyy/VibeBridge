"""Node Server — FastAPI entry point. Port of server/index.js."""
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path for absolute imports
sys.path.insert(0, str(Path(__file__).parent))

import runtime_role
from utils.codex_token_usage import extract_codex_token_budget

runtime_role.ROLE_OVERRIDE = "node"

from fastapi import FastAPI, WebSocket, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse

from fastapi import Request, Depends, HTTPException
from database.db import initialize_database, session_names_db
from middleware.auth import authenticate_token, authenticate_websocket
from ws.chat_handler import handle_chat_connection
from ws.shell_handler import handle_shell_connection
from routes.auth import router as auth_router
from routes.user import router as user_router
from routes.settings import router as settings_router
from routes.commands import router as commands_router
from routes.projects import router as projects_router
from routes.git import router as git_router
from routes.cli_auth import router as cli_auth_router
from routes.mcp import router as mcp_router
from routes.mcp_utils import router as mcp_utils_router
from routes.codex import router as codex_router
from routes.taskmaster import router as taskmaster_router
from routes.plugins import router as plugins_router
from routes.agent import router as agent_router
from node_http_proxy import set_proxy_app
from config import (
    PORT,
    HOST,
    MAIN_SERVER_URL,
    MAIN_REGISTER_URL,
    FILE_TREE_MAX_NODES,
    FILE_TREE_MAX_DEPTH,
    CONFIG_SOURCE_LABEL,
)

app = FastAPI(title="cc_server", version="0.1.0")
set_proxy_app(app)

# CORS — match Express cors() defaults (allow all origins in dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Refreshed-Token"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(settings_router)
app.include_router(commands_router)
app.include_router(projects_router)
app.include_router(git_router)
app.include_router(cli_auth_router)
app.include_router(mcp_router)
app.include_router(mcp_utils_router)
app.include_router(codex_router)
app.include_router(taskmaster_router)
app.include_router(plugins_router)
app.include_router(agent_router)


# ---------------------------------------------------------------------------
# Nodes stub (single-node mode — no Main Server)
# ---------------------------------------------------------------------------

@app.get("/api/nodes")
async def list_nodes(_=Depends(authenticate_token)):
    """Return empty list in single-node mode."""
    return []


# Session rename (separate from /api/projects)
VALID_PROVIDERS = ["claude", "codex"]


@app.put("/api/sessions/{session_id}/rename")
async def rename_session(session_id: str, request: Request, _=Depends(authenticate_token)):
    import re
    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "", session_id)
    if not safe_id or safe_id != session_id:
        raise HTTPException(400, "Invalid sessionId")
    body = await request.json()
    summary = (body.get("summary") or "").strip()
    provider = body.get("provider", "")
    if not summary:
        raise HTTPException(400, "Summary is required")
    if len(summary) > 500:
        raise HTTPException(400, "Summary must not exceed 500 characters")
    if provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Provider must be one of: {', '.join(VALID_PROVIDERS)}")
    session_names_db.set_name(safe_id, provider, summary, request.state.user["id"])
    if provider == "codex":
        try:
            from utils.codex_session_index import sync_codex_session_index_entry

            sync_codex_session_index_entry(
                safe_id,
                fallback_name=summary,
                prefer_existing_name=False,
            )
        except Exception as exc:
            print(f"[cc_server] Failed to sync Codex session rename for {safe_id}: {exc}")
    return {"success": True}


# ---------------------------------------------------------------------------
# Inline helpers for file/filesystem endpoints
# ---------------------------------------------------------------------------

import base64
import json
import mimetypes
import re
import shutil

_HOME = str(Path.home())

# Reserved filenames (Windows compat)
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_FILE_TREE_SKIP_DIR_NAMES = {
    "node_modules",
    "dist",
    "build",
    "coverage",
    "target",
    "vendor",
    "venv",
    "__pycache__",
    "Library",
}


def _expand_workspace_path(input_path: str) -> str:
    """Expand ~ and resolve to absolute path."""
    if not input_path:
        return _HOME
    if input_path.startswith("~"):
        input_path = _HOME + input_path[1:]
    return str(Path(input_path).resolve())


def _validate_path_in_project(project_root: str, target_path: str) -> dict:
    """Ensure target_path is under project_root."""
    try:
        root = Path(project_root).resolve()
        target = Path(target_path).resolve()
        if root == target or root in target.parents:
            return {"valid": True, "resolved": str(target)}
        return {"valid": False, "resolved": str(target), "error": "Path is outside project root"}
    except Exception as e:
        return {"valid": False, "resolved": target_path, "error": str(e)}


def _validate_filename(name: str) -> dict:
    """Validate a filename for safety."""
    if not name or not name.strip():
        return {"valid": False, "error": "Filename is required"}
    name = name.strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        return {"valid": False, "error": "Filename contains invalid characters"}
    if name.upper() in _RESERVED_NAMES:
        return {"valid": False, "error": "Filename is a reserved name"}
    if name.startswith(".") and len(name) == 1:
        return {"valid": False, "error": "Invalid filename"}
    return {"valid": True}


def _resolve_file_tree_max_depth(dir_path: Path) -> int:
    try:
        resolved = dir_path.resolve()
        home = Path(_HOME).resolve()
        if resolved == home:
            return min(FILE_TREE_MAX_DEPTH, 2)
        if resolved == home.parent:
            return min(FILE_TREE_MAX_DEPTH, 1)
    except Exception:
        pass
    return FILE_TREE_MAX_DEPTH


def _get_file_tree(
    dir_path: Path,
    depth: int = 0,
    state: dict | None = None,
) -> list[dict]:
    """Recursively list files/dirs with guardrails for very large workspaces."""
    if state is None:
        state = {
            "count": 0,
            "max_depth": _resolve_file_tree_max_depth(dir_path),
        }

    if depth > state["max_depth"] or state["count"] >= FILE_TREE_MAX_NODES:
        return []

    result: list[dict] = []
    try:
        entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return result

    for entry in entries:
        if state["count"] >= FILE_TREE_MAX_NODES:
            break
        if entry.name.startswith("."):
            continue

        try:
            is_directory = entry.is_dir()
        except OSError:
            continue

        node: dict = {
            "name": entry.name,
            "path": str(entry),
            "type": "directory" if is_directory else "file",
        }
        state["count"] += 1

        if is_directory:
            if (
                depth < state["max_depth"]
                and entry.name not in _FILE_TREE_SKIP_DIR_NAMES
                and state["count"] < FILE_TREE_MAX_NODES
            ):
                node["children"] = _get_file_tree(entry, depth + 1, state)
            else:
                node["children"] = []
        result.append(node)
    return result


# ---------------------------------------------------------------------------
# Filesystem browsing & folder creation
# ---------------------------------------------------------------------------

@app.get("/api/browse-filesystem")
async def browse_filesystem(path: str = "", _=Depends(authenticate_token)):
    from routes.projects import validate_workspace_path

    target = _expand_workspace_path(path) if path else _HOME
    validation = await validate_workspace_path(target)
    if not validation["valid"]:
        raise HTTPException(400, validation["error"])

    resolved = validation.get("resolvedPath", target)
    p = Path(resolved)
    if not p.is_dir():
        raise HTTPException(400, "Path is not a directory")

    suggestions = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                suggestions.append({
                    "path": str(entry),
                    "name": entry.name,
                    "type": "directory",
                })
    except PermissionError:
        pass

    return {"path": str(p), "suggestions": suggestions}


@app.post("/api/create-folder")
async def create_folder(request: Request, _=Depends(authenticate_token)):
    from routes.projects import validate_workspace_path

    body = await request.json()
    folder_path = (body.get("path") or "").strip()
    if not folder_path:
        raise HTTPException(400, "Path is required")

    expanded = _expand_workspace_path(folder_path)
    validation = await validate_workspace_path(expanded)
    if not validation["valid"]:
        raise HTTPException(400, validation["error"])

    resolved = Path(validation.get("resolvedPath", expanded))
    if not resolved.parent.is_dir():
        raise HTTPException(400, "Parent directory does not exist")
    if resolved.exists():
        raise HTTPException(409, "Path already exists")

    resolved.mkdir(parents=False, exist_ok=False)
    return {"success": True, "path": str(resolved)}


# ---------------------------------------------------------------------------
# Project file operations
# ---------------------------------------------------------------------------

@app.get("/api/projects/{project_name}/file")
async def read_file(project_name: str, filePath: str = "", _=Depends(authenticate_token)):
    from projects import extract_project_directory

    if not filePath:
        raise HTTPException(400, "filePath is required")

    project_root = await extract_project_directory(project_name)
    check = _validate_path_in_project(project_root, filePath)
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    file_path = Path(check["resolved"])
    if not file_path.is_file():
        raise HTTPException(404, "File not found")

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File is not a text file")

    return {"content": content, "path": str(file_path)}


@app.get("/api/projects/{project_name}/files/content")
async def serve_file_content(project_name: str, path: str = "", _=Depends(authenticate_token)):
    from projects import extract_project_directory

    if not path:
        raise HTTPException(400, "path is required")

    project_root = await extract_project_directory(project_name)
    check = _validate_path_in_project(project_root, path)
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    file_path = Path(check["resolved"])
    if not file_path.is_file():
        raise HTTPException(404, "File not found")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(file_path, media_type=mime_type or "application/octet-stream")


@app.put("/api/projects/{project_name}/file")
async def save_file(project_name: str, request: Request, _=Depends(authenticate_token)):
    from projects import extract_project_directory

    body = await request.json()
    file_path_str = (body.get("filePath") or "").strip()
    content = body.get("content", "")
    if not file_path_str:
        raise HTTPException(400, "filePath is required")

    project_root = await extract_project_directory(project_name)
    check = _validate_path_in_project(project_root, file_path_str)
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    file_path = Path(check["resolved"])
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    return {"success": True, "path": str(file_path)}


@app.get("/api/projects/{project_name}/files")
async def list_files(project_name: str, _=Depends(authenticate_token)):
    from projects import extract_project_directory

    project_root = await extract_project_directory(project_name)
    root_path = Path(project_root)
    if not root_path.is_dir():
        raise HTTPException(404, "Project directory not found")

    # File tree scans can be expensive on very broad workspaces. Run them
    # off the event loop so health checks and WS heartbeats stay responsive.
    tree = await asyncio.to_thread(_get_file_tree, root_path)
    return tree


@app.post("/api/projects/{project_name}/files/create")
async def create_file(project_name: str, request: Request, _=Depends(authenticate_token)):
    from projects import extract_project_directory

    body = await request.json()
    parent_path = (body.get("path") or "").strip()
    file_type = (body.get("type") or "file").strip()
    name = (body.get("name") or "").strip()

    if not name:
        raise HTTPException(400, "name is required")

    name_check = _validate_filename(name)
    if not name_check["valid"]:
        raise HTTPException(400, name_check["error"])

    project_root = await extract_project_directory(project_name)

    if parent_path:
        target_dir = Path(parent_path)
    else:
        target_dir = Path(project_root)

    check = _validate_path_in_project(project_root, str(target_dir / name))
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    new_path = Path(check["resolved"])
    if new_path.exists():
        raise HTTPException(409, "Path already exists")

    if file_type == "directory":
        new_path.mkdir(parents=True, exist_ok=False)
    else:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.touch()

    return {"success": True, "path": str(new_path), "name": name, "type": file_type}


@app.put("/api/projects/{project_name}/files/rename")
async def rename_file(project_name: str, request: Request, _=Depends(authenticate_token)):
    from projects import extract_project_directory

    body = await request.json()
    old_path_str = (body.get("oldPath") or "").strip()
    new_name = (body.get("newName") or "").strip()

    if not old_path_str or not new_name:
        raise HTTPException(400, "oldPath and newName are required")

    name_check = _validate_filename(new_name)
    if not name_check["valid"]:
        raise HTTPException(400, name_check["error"])

    project_root = await extract_project_directory(project_name)

    check_old = _validate_path_in_project(project_root, old_path_str)
    if not check_old["valid"]:
        raise HTTPException(403, check_old["error"])

    old_path = Path(check_old["resolved"])
    if not old_path.exists():
        raise HTTPException(404, "Source path not found")

    new_path = old_path.parent / new_name
    check_new = _validate_path_in_project(project_root, str(new_path))
    if not check_new["valid"]:
        raise HTTPException(403, check_new["error"])

    if new_path.exists():
        raise HTTPException(409, "Target path already exists")

    old_path.rename(new_path)

    return {"success": True, "oldPath": str(old_path), "newPath": str(new_path), "newName": new_name}


@app.delete("/api/projects/{project_name}/files")
async def delete_file(project_name: str, request: Request, _=Depends(authenticate_token)):
    from projects import extract_project_directory

    body = await request.json()
    target_path = (body.get("path") or "").strip()
    file_type = (body.get("type") or "").strip()

    if not target_path:
        raise HTTPException(400, "path is required")

    project_root = await extract_project_directory(project_name)
    check = _validate_path_in_project(project_root, target_path)
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    resolved = Path(check["resolved"])
    if str(resolved) == str(Path(project_root).resolve()):
        raise HTTPException(403, "Cannot delete project root")

    if not resolved.exists():
        raise HTTPException(404, "Path not found")

    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()

    return {"success": True, "path": str(resolved), "type": file_type or ("directory" if resolved.is_dir() else "file")}


@app.post("/api/projects/{project_name}/files/upload")
async def upload_files(
    project_name: str,
    targetPath: str = Form(...),
    files: list[UploadFile] = File(...),
    _=Depends(authenticate_token),
):
    from projects import extract_project_directory

    project_root = await extract_project_directory(project_name)
    check = _validate_path_in_project(project_root, targetPath)
    if not check["valid"]:
        raise HTTPException(403, check["error"])

    target_dir = Path(check["resolved"])
    target_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        dest = target_dir / f.filename
        content = await f.read()
        dest.write_bytes(content)
        mime_type, _ = mimetypes.guess_type(f.filename)
        uploaded.append({
            "name": f.filename,
            "path": str(dest),
            "size": len(content),
            "mimeType": mime_type or "application/octet-stream",
        })

    return {"success": True, "files": uploaded}


@app.post("/api/projects/{project_name}/upload-images")
async def upload_images(
    project_name: str,
    files: list[UploadFile] = File(...),
    _=Depends(authenticate_token),
):
    images = []
    for f in files:
        content = await f.read()
        mime_type, _ = mimetypes.guess_type(f.filename)
        mime_type = mime_type or "application/octet-stream"
        b64 = base64.b64encode(content).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64}"
        images.append({
            "name": f.filename,
            "data": data_url,
            "size": len(content),
            "mimeType": mime_type,
        })

    return {"images": images}


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

@app.get("/api/projects/{project_name}/sessions/{session_id}/token-usage")
async def token_usage(project_name: str, session_id: str, provider: str = "claude", _=Depends(authenticate_token)):
    from projects import CLAUDE_PROJECTS_DIR, CODEX_SESSIONS_DIR

    if provider == "codex":
        # Find codex session file and read last token_count event
        try:
            codex_dir = CODEX_SESSIONS_DIR
            if not codex_dir.is_dir():
                return {"used": 0, "total": 200000, "breakdown": {"input": 0, "cacheCreation": 0, "cacheRead": 0}}

            from projects import _find_codex_jsonl_files
            all_files = await _find_codex_jsonl_files(codex_dir)

            for fpath in all_files:
                if session_id in fpath.name:
                    text = fpath.read_text(encoding="utf-8")
                    last_usage = None
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "event_msg":
                                payload = entry.get("payload", {})
                                if payload.get("type") == "token_count" and payload.get("info"):
                                    last_usage = extract_codex_token_budget(payload)
                        except Exception:
                            pass
                    if last_usage:
                        return last_usage
        except Exception:
            pass
        return {"used": 0, "total": 200000, "breakdown": {"input": 0, "cacheCreation": 0, "cacheRead": 0}}

    # Claude provider
    project_dir = CLAUDE_PROJECTS_DIR / project_name
    if not project_dir.is_dir():
        return {"used": 0, "total": 200000, "breakdown": {"input": 0, "cacheCreation": 0, "cacheRead": 0}}

    last_usage = None
    try:
        for jf in project_dir.iterdir():
            if jf.suffix != ".jsonl":
                continue
            text = jf.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("sessionId") != session_id:
                        continue
                    msg = entry.get("message", {})
                    if msg.get("role") == "assistant" and msg.get("usage"):
                        last_usage = msg["usage"]
                except Exception:
                    pass
    except Exception:
        pass

    if last_usage:
        input_tokens = last_usage.get("input_tokens", 0)
        cache_creation = last_usage.get("cache_creation_input_tokens", 0)
        cache_read = last_usage.get("cache_read_input_tokens", 0)
        output_tokens = last_usage.get("output_tokens", 0)
        return {
            "used": input_tokens + cache_creation + cache_read + output_tokens,
            "total": 200000,
            "breakdown": {
                "input": input_tokens,
                "cacheCreation": cache_creation,
                "cacheRead": cache_read,
            },
        }

    return {"used": 0, "total": 200000, "breakdown": {"input": 0, "cacheCreation": 0, "cacheRead": 0}}


# ---------------------------------------------------------------------------
# Search conversations (SSE)
# ---------------------------------------------------------------------------

@app.get("/api/search/conversations")
async def search_conversations(q: str = "", limit: int = 50, _=Depends(authenticate_token)):
    from projects import CLAUDE_PROJECTS_DIR

    if not q or not q.strip():
        raise HTTPException(400, "Query parameter 'q' is required")

    query_lower = q.strip().lower()

    async def _generate():
        count = 0
        try:
            if not CLAUDE_PROJECTS_DIR.is_dir():
                return
            for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
                if not project_dir.is_dir():
                    continue
                for jf in project_dir.iterdir():
                    if jf.suffix != ".jsonl":
                        continue
                    if count >= limit:
                        return
                    try:
                        text = jf.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    for line in text.splitlines():
                        line_stripped = line.strip()
                        if not line_stripped:
                            continue
                        if count >= limit:
                            return
                        try:
                            entry = json.loads(line_stripped)
                            msg = entry.get("message", {})
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                content = " ".join(
                                    p.get("text", "") for p in content if isinstance(p, dict)
                                )
                            if not isinstance(content, str):
                                continue
                            if query_lower in content.lower():
                                result = {
                                    "project": project_dir.name,
                                    "sessionId": entry.get("sessionId"),
                                    "timestamp": entry.get("timestamp"),
                                    "role": msg.get("role"),
                                    "snippet": content[:200],
                                }
                                yield f"data: {json.dumps(result)}\n\n"
                                count += 1
                        except Exception:
                            pass
        except Exception:
            pass
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# System update stub
# ---------------------------------------------------------------------------

@app.post("/api/system/update")
async def system_update(_=Depends(authenticate_token)):
    return {"success": False, "error": "Not supported in Python backend"}


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_chat(ws: WebSocket):
    if not (MAIN_SERVER_URL or MAIN_REGISTER_URL):
        auth_header = ws.headers.get("authorization", "")
        token = ws.query_params.get("token")
        if not token and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        user = authenticate_websocket(token)
        if not user:
            await ws.close(4003, "Authentication failed")
            return
    await handle_chat_connection(ws)


@app.websocket("/shell")
async def ws_shell(ws: WebSocket):
    if not (MAIN_SERVER_URL or MAIN_REGISTER_URL):
        auth_header = ws.headers.get("authorization", "")
        token = ws.query_params.get("token")
        if not token and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        user = authenticate_websocket(token)
        if not user:
            await ws.close(4003, "Authentication failed")
            return
    await handle_shell_connection(ws)


@app.websocket("/ws/main")
async def ws_main(ws: WebSocket):
    """Handle reverse WebSocket connection from Main Server."""
    from ws.main_handler import handle_main_connection
    await handle_main_connection(ws)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    initialize_database()
    print(f"[cc_server] Config: {CONFIG_SOURCE_LABEL}")

    try:
        from utils.codex_session_index import backfill_codex_session_index
        from utils.codex_ide_compat import normalize_codex_threads_for_ide

        backfill_result = backfill_codex_session_index()
        if backfill_result.get("added"):
            print(
                "[cc_server] Backfilled "
                f"{backfill_result['added']} Codex session index entries"
            )

        ide_compat_result = normalize_codex_threads_for_ide()
        if ide_compat_result.get("updated"):
            print(
                "[cc_server] Normalized "
                f"{ide_compat_result['updated']} Codex IDE history entries"
            )
    except Exception as exc:
        print(f"[cc_server] Codex session index backfill skipped: {exc}")

    if MAIN_SERVER_URL and MAIN_REGISTER_URL:
        print(
            "[cc_server] Both MAIN_SERVER_URL and MAIN_REGISTER_URL are set; "
            "preferring direct Node -> Main WebSocket mode."
        )

    # Start node connector if MAIN_SERVER_URL is set
    if MAIN_SERVER_URL:
        from node_connector import start_node_connector
        from providers.claude_sdk import (
            query_claude_sdk,
            abort_claude_session,
            get_active_claude_sessions,
            get_pending_approvals_for_session,
            is_claude_session_active,
            resolve_tool_approval,
            reconnect_session_writer,
        )
        from providers.codex_mcp import (
            query_codex,
            abort_codex_session,
            get_active_codex_sessions,
            is_codex_session_active,
            get_pending_codex_approvals_for_session,
            resolve_codex_approval,
        )
        from projects import (
            extract_project_directory,
            get_codex_session_messages,
            get_codex_sessions,
            get_projects,
            get_session_messages,
            get_sessions,
        )
        start_node_connector({
            "query_claude": query_claude_sdk,
            "query_codex": query_codex,
            "abort_claude": abort_claude_session,
            "abort_codex": abort_codex_session,
            "get_active_claude": get_active_claude_sessions,
            "get_active_codex": get_active_codex_sessions,
            "get_pending_approvals": get_pending_approvals_for_session,
            "get_pending_codex_approvals": get_pending_codex_approvals_for_session,
            "is_claude_active": is_claude_session_active,
            "is_codex_active": is_codex_session_active,
            "resolve_tool_approval": resolve_tool_approval,
            "resolve_codex_approval": resolve_codex_approval,
            "reconnect_writer": reconnect_session_writer,
            "get_projects": get_projects,
            "get_sessions": get_sessions,
            "get_session_messages": get_session_messages,
            "get_codex_session_messages": get_codex_session_messages,
            "get_codex_sessions": get_codex_sessions,
            "extract_project_directory": extract_project_directory,
        })
        print(f"[cc_server] Node connector started → {MAIN_SERVER_URL}")
    elif MAIN_REGISTER_URL:
        from node_registration import start_node_registration

        start_node_registration()
        print(f"[cc_server] Node HTTP registration started → {MAIN_REGISTER_URL}")

    print(f"[cc_server] Listening on {HOST}:{PORT}")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=True)
