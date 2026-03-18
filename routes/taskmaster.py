"""TaskMaster routes — full port of server/routes/taskmaster.js.

Provides endpoints for TaskMaster integration:
- Installation/detection
- Task CRUD via CLI
- PRD file management
- PRD templates
"""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from middleware.auth import authenticate_token
from projects import extract_project_directory

router = APIRouter(prefix="/api/taskmaster", tags=["taskmaster"])

_HOME = str(Path.home())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _check_installation() -> dict:
    """Check if task-master CLI is available."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "which", "task-master",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout.strip():
            return {"isInstalled": True, "path": stdout.decode().strip()}
    except Exception:
        pass
    return {"isInstalled": False, "path": None, "reason": "task-master command not found"}


async def _detect_mcp_server() -> dict:
    """Check if TaskMaster MCP server is configured."""
    for fp in (
        os.path.join(_HOME, ".claude.json"),
        os.path.join(_HOME, ".claude", "settings.json"),
    ):
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
            for name, cfg in data.get("mcpServers", {}).items():
                if "taskmaster" in name.lower() or "task-master" in name.lower():
                    return {"hasMCPServer": True, "isConfigured": True, "name": name, "config": cfg}
        except Exception:
            continue
    return {"hasMCPServer": False, "isConfigured": False}


def _detect_taskmaster_folder(project_path: str) -> dict:
    """Detect .taskmaster folder and its essential files."""
    tm_path = os.path.join(project_path, ".taskmaster")
    if not os.path.isdir(tm_path):
        return {"hasTaskmaster": False, "hasEssentialFiles": False, "path": None}

    config_path = os.path.join(tm_path, "config.json")
    tasks_path = os.path.join(tm_path, "tasks", "tasks.json")

    config = None
    if os.path.isfile(config_path):
        try:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except Exception:
            pass

    has_essential = os.path.isfile(tasks_path) or os.path.isfile(config_path)

    return {
        "hasTaskmaster": True,
        "hasEssentialFiles": has_essential,
        "path": tm_path,
        "config": config,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _resolve_project_path(project_name: str) -> str:
    """Resolve project name to directory path, raising 404 on failure."""
    try:
        return await extract_project_directory(project_name)
    except Exception as e:
        raise HTTPException(404, f"Project not found: {e}")


async def _run_cli(args: list[str], cwd: str, stdin_text: str = "") -> tuple[int, str, str]:
    """Run a CLI command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(stdin_text.encode() if stdin_text else None)
    return proc.returncode, stdout.decode(), stderr.decode()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# ---- Installation & detection ----

@router.get("/installation")
async def installation():
    return await _check_installation()


@router.get("/installation-status")
async def installation_status(_=Depends(authenticate_token)):
    """Combined installation + MCP status."""
    inst = await _check_installation()
    mcp = await _detect_mcp_server()
    return {
        "success": True,
        "installation": inst,
        "mcpServer": mcp,
        "isReady": inst.get("isInstalled", False) and mcp.get("hasMCPServer", False),
    }


@router.get("/detect/{project_name}")
async def detect_project(project_name: str, _=Depends(authenticate_token)):
    """Detect TaskMaster configuration for a project."""
    project_path = await _resolve_project_path(project_name)

    if not os.path.isdir(project_path):
        raise HTTPException(404, "Project path not accessible")

    tm_result = _detect_taskmaster_folder(project_path)
    mcp_result = await _detect_mcp_server()

    # Determine status
    status = "not-configured"
    if tm_result["hasTaskmaster"] and tm_result["hasEssentialFiles"]:
        if mcp_result["hasMCPServer"] and mcp_result["isConfigured"]:
            status = "fully-configured"
        else:
            status = "taskmaster-only"
    elif mcp_result["hasMCPServer"] and mcp_result["isConfigured"]:
        status = "mcp-only"

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "status": status,
        "taskmaster": tm_result,
        "mcp": mcp_result,
        "timestamp": _now_iso(),
    }


@router.get("/detect-all")
async def detect_all(_=Depends(authenticate_token)):
    """Detect TaskMaster across all projects."""
    # Simplified — just return MCP status
    mcp = await _detect_mcp_server()
    return {"mcpServer": mcp, "timestamp": _now_iso()}


# Keep legacy POST endpoints for backward compat
class DetectBody(BaseModel):
    projectPath: str


@router.post("/detect")
async def detect_post(body: DetectBody, _=Depends(authenticate_token)):
    if not body.projectPath:
        raise HTTPException(400, "projectPath is required")
    tm = _detect_taskmaster_folder(body.projectPath)
    return {"found": tm["hasTaskmaster"], "path": tm.get("path"), "config": tm.get("config")}


@router.post("/detect-mcp")
async def detect_mcp(_=Depends(authenticate_token)):
    mcp = await _detect_mcp_server()
    return {"found": mcp["hasMCPServer"], "name": mcp.get("name"), "config": mcp.get("config")}


# ---- Tasks ----

@router.get("/tasks/{project_name}")
async def get_tasks(project_name: str, _=Depends(authenticate_token)):
    """Get tasks from .taskmaster/tasks/tasks.json."""
    project_path = await _resolve_project_path(project_name)
    tasks_file = os.path.join(project_path, ".taskmaster", "tasks", "tasks.json")

    if not os.path.isfile(tasks_file):
        return {"projectName": project_name, "tasks": [], "message": "No tasks.json file found"}

    try:
        data = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Failed to parse tasks file: {e}")

    tasks: list = []
    current_tag = "master"

    if isinstance(data, list):
        tasks = data
    elif isinstance(data, dict):
        if "tasks" in data and isinstance(data["tasks"], list):
            tasks = data["tasks"]
        else:
            # Tagged format
            for tag in (current_tag, "master"):
                if isinstance(data.get(tag), dict) and isinstance(data[tag].get("tasks"), list):
                    tasks = data[tag]["tasks"]
                    current_tag = tag
                    break
            if not tasks:
                for key, val in data.items():
                    if isinstance(val, dict) and isinstance(val.get("tasks"), list):
                        tasks = val["tasks"]
                        current_tag = key
                        break

    transformed = []
    for t in tasks:
        transformed.append({
            "id": t.get("id"),
            "title": t.get("title", "Untitled Task"),
            "description": t.get("description", ""),
            "status": t.get("status", "pending"),
            "priority": t.get("priority", "medium"),
            "dependencies": t.get("dependencies", []),
            "createdAt": t.get("createdAt") or t.get("created") or _now_iso(),
            "updatedAt": t.get("updatedAt") or t.get("updated") or _now_iso(),
            "details": t.get("details", ""),
            "testStrategy": t.get("testStrategy") or t.get("test_strategy", ""),
            "subtasks": t.get("subtasks", []),
        })

    by_status = {}
    for s in ("pending", "in-progress", "done", "review", "deferred", "cancelled"):
        by_status[s] = sum(1 for t in transformed if t["status"] == s)

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "tasks": transformed,
        "currentTag": current_tag,
        "totalTasks": len(transformed),
        "tasksByStatus": by_status,
        "timestamp": _now_iso(),
    }


# ---- Next task ----

@router.get("/next/{project_name}")
async def next_task(project_name: str, _=Depends(authenticate_token)):
    """Get next recommended task."""
    project_path = await _resolve_project_path(project_name)

    try:
        code, stdout, stderr = await _run_cli(["task-master", "next"], cwd=project_path)
        if code == 0 and stdout.strip():
            try:
                data = json.loads(stdout)
            except Exception:
                data = {"message": stdout.strip()}
            return {
                "projectName": project_name,
                "projectPath": project_path,
                "nextTask": data,
                "timestamp": _now_iso(),
            }
    except Exception:
        pass

    # Fallback: find first pending task from tasks.json
    tasks_file = os.path.join(project_path, ".taskmaster", "tasks", "tasks.json")
    if os.path.isfile(tasks_file):
        try:
            data = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
            all_tasks = data.get("tasks", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            pending = [t for t in all_tasks if t.get("status") == "pending"]
            if pending:
                pending.sort(key=lambda t: {"high": 0, "medium": 1, "low": 2}.get(t.get("priority", "medium"), 1))
                return {
                    "projectName": project_name,
                    "projectPath": project_path,
                    "nextTask": pending[0],
                    "source": "fallback",
                    "timestamp": _now_iso(),
                }
        except Exception:
            pass

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "nextTask": None,
        "message": "No pending tasks found",
        "timestamp": _now_iso(),
    }


# ---- Initialize ----

@router.post("/initialize/{project_name}")
async def initialize(project_name: str, request: Request, _=Depends(authenticate_token)):
    """Initialize TaskMaster in a project (stub)."""
    body = await request.json() if await request.body() else {}
    raise HTTPException(501, "TaskMaster initialization not yet implemented")


@router.post("/init/{project_name}")
async def init_project(project_name: str, _=Depends(authenticate_token)):
    """Initialize TaskMaster via CLI."""
    project_path = await _resolve_project_path(project_name)

    tm_path = os.path.join(project_path, ".taskmaster")
    if os.path.isdir(tm_path):
        raise HTTPException(400, "TaskMaster already initialized")

    code, stdout, stderr = await _run_cli(
        ["npx", "task-master", "init"],
        cwd=project_path,
        stdin_text="yes\n",
    )

    if code == 0:
        return {
            "projectName": project_name,
            "projectPath": project_path,
            "message": "TaskMaster initialized successfully",
            "output": stdout,
            "timestamp": _now_iso(),
        }

    raise HTTPException(500, f"Failed to initialize TaskMaster: {stderr or stdout}")


# ---- Add task ----

@router.post("/add-task/{project_name}")
async def add_task(project_name: str, request: Request, _=Depends(authenticate_token)):
    """Add a new task."""
    project_path = await _resolve_project_path(project_name)
    body = await request.json()

    prompt = body.get("prompt")
    title = body.get("title")
    description = body.get("description")
    priority = body.get("priority", "medium")
    dependencies = body.get("dependencies")

    if not prompt and (not title or not description):
        raise HTTPException(400, 'Either "prompt" or both "title" and "description" are required')

    args = ["npx", "task-master-ai", "add-task"]
    if prompt:
        args.extend(["--prompt", prompt, "--research"])
    else:
        args.extend(["--prompt", f'Create a task titled "{title}" with description: {description}'])
    if priority:
        args.extend(["--priority", priority])
    if dependencies:
        args.extend(["--dependencies", str(dependencies)])

    code, stdout, stderr = await _run_cli(args, cwd=project_path)

    if code == 0:
        return {
            "projectName": project_name,
            "projectPath": project_path,
            "message": "Task added successfully",
            "output": stdout,
            "timestamp": _now_iso(),
        }

    raise HTTPException(500, f"Failed to add task: {stderr or stdout}")


# ---- Update task ----

@router.put("/update-task/{project_name}/{task_id}")
async def update_task(project_name: str, task_id: str, request: Request, _=Depends(authenticate_token)):
    """Update a task."""
    project_path = await _resolve_project_path(project_name)
    body = await request.json()

    status = body.get("status")
    title = body.get("title")
    description = body.get("description")
    priority = body.get("priority")
    details = body.get("details")

    # Status-only update
    if status and len(body) == 1:
        code, stdout, stderr = await _run_cli(
            ["npx", "task-master-ai", "set-status", f"--id={task_id}", f"--status={status}"],
            cwd=project_path,
        )
        if code == 0:
            return {
                "projectName": project_name,
                "projectPath": project_path,
                "taskId": task_id,
                "message": "Task status updated successfully",
                "output": stdout,
                "timestamp": _now_iso(),
            }
        raise HTTPException(500, f"Failed to update task status: {stderr or stdout}")

    # General update
    updates = []
    if title:
        updates.append(f'title: "{title}"')
    if description:
        updates.append(f'description: "{description}"')
    if priority:
        updates.append(f'priority: "{priority}"')
    if details:
        updates.append(f'details: "{details}"')

    prompt = f"Update task with the following changes: {', '.join(updates)}"
    code, stdout, stderr = await _run_cli(
        ["npx", "task-master-ai", "update-task", f"--id={task_id}", f"--prompt={prompt}"],
        cwd=project_path,
    )

    if code == 0:
        return {
            "projectName": project_name,
            "projectPath": project_path,
            "taskId": task_id,
            "message": "Task updated successfully",
            "output": stdout,
            "timestamp": _now_iso(),
        }

    raise HTTPException(500, f"Failed to update task: {stderr or stdout}")


# ---- PRD files ----

@router.get("/prd/{project_name}")
async def list_prd_files(project_name: str, _=Depends(authenticate_token)):
    """List PRD files in .taskmaster/docs/."""
    project_path = await _resolve_project_path(project_name)
    docs_path = Path(project_path) / ".taskmaster" / "docs"

    if not docs_path.is_dir():
        return {"projectName": project_name, "prdFiles": [], "message": "No .taskmaster/docs directory found"}

    prd_files = []
    for entry in docs_path.iterdir():
        if entry.is_file() and entry.suffix in (".txt", ".md"):
            stat = entry.stat()
            prd_files.append({
                "name": entry.name,
                "path": str(entry.relative_to(project_path)),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            })

    prd_files.sort(key=lambda f: f["modified"], reverse=True)

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "prdFiles": prd_files,
        "timestamp": _now_iso(),
    }


@router.post("/prd/{project_name}")
async def create_prd(project_name: str, request: Request, _=Depends(authenticate_token)):
    """Create or update a PRD file."""
    project_path = await _resolve_project_path(project_name)
    body = await request.json()

    file_name = body.get("fileName", "").strip()
    content = body.get("content", "")

    if not file_name or not content:
        raise HTTPException(400, "fileName and content are required")

    if not re.match(r'^[\w\-. ]+\.(txt|md)$', file_name):
        raise HTTPException(400, "Invalid filename")

    docs_path = Path(project_path) / ".taskmaster" / "docs"
    docs_path.mkdir(parents=True, exist_ok=True)

    file_path = docs_path / file_name
    file_path.write_text(content, encoding="utf-8")

    stat = file_path.stat()
    return {
        "projectName": project_name,
        "projectPath": project_path,
        "fileName": file_name,
        "filePath": str(file_path.relative_to(project_path)),
        "size": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "message": "PRD file saved successfully",
        "timestamp": _now_iso(),
    }


@router.get("/prd/{project_name}/{file_name}")
async def read_prd(project_name: str, file_name: str, _=Depends(authenticate_token)):
    """Read a specific PRD file."""
    project_path = await _resolve_project_path(project_name)
    file_path = Path(project_path) / ".taskmaster" / "docs" / file_name

    if not file_path.is_file():
        raise HTTPException(404, f'File "{file_name}" does not exist')

    content = file_path.read_text(encoding="utf-8")
    stat = file_path.stat()

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "fileName": file_name,
        "filePath": str(file_path.relative_to(project_path)),
        "content": content,
        "size": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "timestamp": _now_iso(),
    }


@router.delete("/prd/{project_name}/{file_name}")
async def delete_prd(project_name: str, file_name: str, _=Depends(authenticate_token)):
    """Delete a PRD file."""
    project_path = await _resolve_project_path(project_name)
    file_path = Path(project_path) / ".taskmaster" / "docs" / file_name

    if not file_path.is_file():
        raise HTTPException(404, f'File "{file_name}" does not exist')

    file_path.unlink()

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "fileName": file_name,
        "message": "PRD file deleted successfully",
        "timestamp": _now_iso(),
    }


# ---- Parse PRD ----

@router.post("/parse-prd/{project_name}")
async def parse_prd(project_name: str, request: Request, _=Depends(authenticate_token)):
    """Parse a PRD file to generate tasks."""
    project_path = await _resolve_project_path(project_name)
    body = await request.json()

    file_name = body.get("fileName", "prd.txt")
    num_tasks = body.get("numTasks")
    append = body.get("append", False)

    prd_path = os.path.join(project_path, ".taskmaster", "docs", file_name)
    if not os.path.isfile(prd_path):
        raise HTTPException(404, f'File "{file_name}" does not exist in .taskmaster/docs/')

    args = ["npx", "task-master-ai", "parse-prd", prd_path]
    if num_tasks:
        args.extend(["--num-tasks", str(num_tasks)])
    if append:
        args.append("--append")
    args.append("--research")

    code, stdout, stderr = await _run_cli(args, cwd=project_path)

    if code == 0:
        return {
            "projectName": project_name,
            "projectPath": project_path,
            "prdFile": file_name,
            "message": "PRD parsed and tasks generated successfully",
            "output": stdout,
            "timestamp": _now_iso(),
        }

    raise HTTPException(500, f"Failed to parse PRD: {stderr or stdout}")


# ---- PRD templates ----

@router.get("/prd-templates")
async def prd_templates(_=Depends(authenticate_token)):
    """Get available PRD templates."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    templates = [
        {
            "id": "web-app",
            "name": "Web Application",
            "description": "Template for web application projects",
            "category": "web",
        },
        {
            "id": "api-service",
            "name": "API Service",
            "description": "Template for backend API services",
            "category": "backend",
        },
        {
            "id": "cli-tool",
            "name": "CLI Tool",
            "description": "Template for command-line tools",
            "category": "tools",
        },
        {
            "id": "mobile-app",
            "name": "Mobile Application",
            "description": "Template for mobile app projects",
            "category": "mobile",
        },
    ]
    return {"templates": templates, "timestamp": _now_iso()}


# ---- Apply template ----

@router.post("/apply-template/{project_name}")
async def apply_template(project_name: str, request: Request, _=Depends(authenticate_token)):
    """Apply a PRD template to a project."""
    project_path = await _resolve_project_path(project_name)
    body = await request.json()

    template_id = body.get("templateId", "")
    file_name = body.get("fileName", "prd.txt")
    customizations = body.get("customizations", {})

    if not template_id:
        raise HTTPException(400, "templateId is required")

    # Create docs directory
    docs_path = Path(project_path) / ".taskmaster" / "docs"
    docs_path.mkdir(parents=True, exist_ok=True)

    # Generate basic template content
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    app_name = customizations.get("appName", "[Your App Name]")
    author = customizations.get("author", "[Your Name]")

    content = f"""# Product Requirements Document

## Overview
**Product Name:** {app_name}
**Template:** {template_id}
**Date:** {today}
**Author:** {author}

## Executive Summary
Brief description of the project.

## Product Goals
- Goal 1
- Goal 2
- Goal 3

## User Stories
1. As a user, I want to...
2. As a user, I want to...

## Technical Requirements
[To be defined]

## Success Metrics
[To be defined]

## Timeline
[To be defined]
"""

    file_path = docs_path / file_name
    file_path.write_text(content, encoding="utf-8")

    return {
        "projectName": project_name,
        "projectPath": project_path,
        "templateId": template_id,
        "fileName": file_name,
        "message": "Template applied successfully",
        "timestamp": _now_iso(),
    }
