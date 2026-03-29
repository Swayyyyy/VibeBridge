"""Codex routes — port of server/routes/codex.js.

Codex config (TOML), sessions, and MCP server management.
Session reading is stubbed — full implementation in Phase 5 (projects.py).
"""
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database.db import session_names_db, apply_custom_session_names
from middleware.auth import authenticate_token
from utils.codex_cli import get_codex_cli_env, resolve_codex_cli

router = APIRouter(prefix="/api/codex", tags=["codex"])


# ---------------------------------------------------------------------------
# Config (TOML)
# ---------------------------------------------------------------------------

def _read_codex_config() -> dict:
    """Read ~/.codex/config.toml and return parsed config."""
    config_path = Path.home() / ".codex" / "config.toml"
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # fallback
        except ImportError:
            # No TOML parser available — try simple parsing
            return _read_codex_config_fallback(config_path)

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return {
            "model": data.get("model"),
            "mcpServers": data.get("mcp_servers", {}),
            "approvalMode": data.get("approval_mode", "suggest"),
        }
    except FileNotFoundError:
        return {"model": None, "mcpServers": {}, "approvalMode": "suggest"}
    except Exception as e:
        raise RuntimeError(f"Error reading Codex config: {e}")


def _read_codex_config_fallback(config_path: Path) -> dict:
    """Minimal TOML parser fallback for simple key=value."""
    try:
        text = config_path.read_text(encoding="utf-8")
        model = None
        approval_mode = "suggest"
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("model"):
                model = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("approval_mode"):
                approval_mode = line.split("=", 1)[1].strip().strip('"').strip("'")
        return {"model": model, "mcpServers": {}, "approvalMode": approval_mode}
    except FileNotFoundError:
        return {"model": None, "mcpServers": {}, "approvalMode": "suggest"}


@router.get("/config")
async def get_config():
    try:
        config = _read_codex_config()
        return {"success": True, "config": config}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Sessions (stub — full implementation in Phase 5)
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def get_sessions(projectPath: str = "", limit: int = 5, user=Depends(authenticate_token)):
    if not projectPath:
        raise HTTPException(400, "projectPath query parameter required")
    from projects import get_codex_sessions

    try:
        sessions = await get_codex_sessions(projectPath, limit)
        apply_custom_session_names(sessions, "codex", user.get("id") if isinstance(user, dict) else None)
        return {"success": True, "sessions": sessions}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
    _=Depends(authenticate_token),
):
    from projects import get_codex_session_messages

    try:
        result = await get_codex_session_messages(session_id, limit, offset)
        if isinstance(result, list):
            return {"success": True, "messages": result}
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(authenticate_token)):
    from projects import delete_codex_session

    try:
        await delete_codex_session(session_id)
        session_names_db.delete_name(session_id, "codex", user.get("id") if isinstance(user, dict) else None)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def _run_codex_cli(*args: str) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        resolve_codex_cli(), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=get_codex_cli_env(),
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode


def _parse_list_output(output: str) -> list:
    servers = []
    for line in output.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        idx = line.index(":")
        name = line[:idx].strip()
        if not name:
            continue
        rest = line[idx + 1:].strip()
        description = rest
        status = "unknown"
        if "✓" in rest or "✗" in rest:
            m = re.match(r"(.*?)\s*-\s*([✓✗].*)", rest)
            if m:
                description = m.group(1).strip()
                status = "connected" if "✓" in m.group(2) else "failed"
        servers.append({"name": name, "type": "stdio", "status": status, "description": description})
    return servers


def _parse_get_output(output: str) -> dict:
    try:
        m = re.search(r"\{[\s\S]*\}", output)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    server: dict = {"raw_output": output}
    for line in output.split("\n"):
        if "Name:" in line:
            server["name"] = line.split(":", 1)[1].strip()
        elif "Type:" in line:
            server["type"] = line.split(":", 1)[1].strip()
        elif "Command:" in line:
            server["command"] = line.split(":", 1)[1].strip()
    return server


# ---------------------------------------------------------------------------
# Codex MCP management endpoints
# ---------------------------------------------------------------------------

@router.get("/mcp/cli/list")
async def mcp_list():
    stdout, stderr, code = await _run_codex_cli("mcp", "list")
    if code == 0:
        return {"success": True, "output": stdout, "servers": _parse_list_output(stdout)}
    if "ENOENT" in stderr or code == 127:
        raise HTTPException(503, "Codex CLI not installed")
    raise HTTPException(500, f"Codex CLI command failed: {stderr}")


class AddMcpBody(BaseModel):
    name: str
    command: str
    args: list = []
    env: dict = {}


@router.post("/mcp/cli/add")
async def mcp_add(body: AddMcpBody):
    if not body.name or not body.command:
        raise HTTPException(400, "name and command are required")
    cli_args = ["mcp", "add", body.name]
    for k, v in body.env.items():
        cli_args += ["-e", f"{k}={v}"]
    cli_args += ["--", body.command] + body.args

    stdout, stderr, code = await _run_codex_cli(*cli_args)
    if code == 0:
        return {"success": True, "output": stdout, "message": f'MCP server "{body.name}" added successfully'}
    raise HTTPException(400, f"Codex CLI command failed: {stderr}")


@router.delete("/mcp/cli/remove/{name}")
async def mcp_remove(name: str):
    stdout, stderr, code = await _run_codex_cli("mcp", "remove", name)
    if code == 0:
        return {"success": True, "output": stdout, "message": f'MCP server "{name}" removed successfully'}
    raise HTTPException(400, f"Codex CLI command failed: {stderr}")


@router.get("/mcp/cli/get/{name}")
async def mcp_get(name: str):
    stdout, stderr, code = await _run_codex_cli("mcp", "get", name)
    if code == 0:
        return {"success": True, "output": stdout, "server": _parse_get_output(stdout)}
    raise HTTPException(404, f"Codex CLI command failed: {stderr}")


@router.get("/mcp/config/read")
async def mcp_config_read():
    config_path = Path.home() / ".codex" / "config.toml"
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {"success": True, "configPath": str(config_path), "servers": []}

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"success": True, "configPath": str(config_path), "servers": []}

    servers = []
    for name, cfg in data.get("mcp_servers", {}).items():
        servers.append({
            "id": name,
            "name": name,
            "type": "stdio",
            "scope": "user",
            "config": {"command": cfg.get("command", ""), "args": cfg.get("args", []), "env": cfg.get("env", {})},
            "raw": cfg,
        })
    return {"success": True, "configPath": str(config_path), "servers": servers}
