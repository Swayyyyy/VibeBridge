"""MCP utilities routes — port of server/routes/mcp-utils.js.

Endpoints for MCP server detection and configuration utilities.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/mcp-utils", tags=["mcp-utils"])


def _detect_taskmaster_mcp() -> dict:
    """Check if TaskMaster MCP server is configured in Claude settings."""
    home = str(Path.home())
    for fp in (os.path.join(home, ".claude.json"), os.path.join(home, ".claude", "settings.json")):
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
            mcp = data.get("mcpServers", {})
            for name, cfg in mcp.items():
                if "taskmaster" in name.lower() or "task-master" in name.lower():
                    return {"found": True, "name": name, "config": cfg, "configPath": fp}
        except Exception:
            continue
    return {"found": False}


def _get_all_mcp_servers() -> dict:
    """Read all configured MCP servers from Claude config files."""
    home = str(Path.home())
    servers = []
    for fp in (os.path.join(home, ".claude.json"), os.path.join(home, ".claude", "settings.json")):
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
            for name, cfg in data.get("mcpServers", {}).items():
                servers.append({"name": name, "scope": "user", "config": cfg, "configPath": fp})
            for proj_path, proj_cfg in data.get("projects", {}).items():
                for name, cfg in proj_cfg.get("mcpServers", {}).items():
                    servers.append({"name": name, "scope": "local", "projectPath": proj_path, "config": cfg, "configPath": fp})
            break
        except Exception:
            continue
    return {"servers": servers, "count": len(servers)}


@router.get("/taskmaster-server")
async def taskmaster_server():
    try:
        return _detect_taskmaster_mcp()
    except Exception as e:
        raise HTTPException(500, f"Failed to detect TaskMaster MCP server: {e}")


@router.get("/all-servers")
async def all_servers():
    try:
        return _get_all_mcp_servers()
    except Exception as e:
        raise HTTPException(500, f"Failed to get MCP servers: {e}")
