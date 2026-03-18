"""MCP routes — port of server/routes/mcp.js.

Claude MCP server management via `claude` CLI.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def _run_claude_cli(*args: str, cwd: Optional[str] = None) -> tuple:
    """Run `claude <args>` and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        "claude", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_list_output(output: str) -> list:
    servers = []
    for line in output.split("\n"):
        line = line.strip()
        if not line or "Checking MCP server health" in line:
            continue
        if ":" in line:
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
            stype = "http" if description.startswith("http") else "stdio"
            servers.append({"name": name, "type": stype, "status": status, "description": description})
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
        elif "URL:" in line:
            server["url"] = line.split(":", 1)[1].strip()
    return server


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class AddServerBody(BaseModel):
    name: str
    type: str = "stdio"
    command: Optional[str] = None
    args: list = []
    url: Optional[str] = None
    headers: dict = {}
    env: dict = {}
    scope: str = "user"
    projectPath: Optional[str] = None


class AddJsonBody(BaseModel):
    name: str
    jsonConfig: dict | str
    scope: str = "user"
    projectPath: Optional[str] = None


# ---------------------------------------------------------------------------
# CLI endpoints
# ---------------------------------------------------------------------------

@router.get("/cli/list")
async def cli_list():
    stdout, stderr, code = await _run_claude_cli("mcp", "list")
    if code == 0:
        return {"success": True, "output": stdout, "servers": _parse_list_output(stdout)}
    raise HTTPException(500, f"Claude CLI command failed: {stderr}")


@router.post("/cli/add")
async def cli_add(body: AddServerBody):
    cli_args = ["mcp", "add", "--scope", body.scope]
    if body.type in ("http", "sse"):
        cli_args += ["--transport", body.type, body.name, body.url or ""]
        for k, v in body.headers.items():
            cli_args += ["--header", f"{k}: {v}"]
    else:
        cli_args.append(body.name)
        for k, v in body.env.items():
            cli_args += ["-e", f"{k}={v}"]
        cli_args.append(body.command or "")
        cli_args.extend(body.args)

    cwd = body.projectPath if body.scope == "local" and body.projectPath else None
    stdout, stderr, code = await _run_claude_cli(*cli_args, cwd=cwd)
    if code == 0:
        return {"success": True, "output": stdout, "message": f'MCP server "{body.name}" added successfully'}
    raise HTTPException(400, f"Claude CLI command failed: {stderr}")


@router.post("/cli/add-json")
async def cli_add_json(body: AddJsonBody):
    parsed = body.jsonConfig if isinstance(body.jsonConfig, dict) else json.loads(body.jsonConfig)
    if not parsed.get("type"):
        raise HTTPException(400, "Missing required field: type")
    if parsed["type"] == "stdio" and not parsed.get("command"):
        raise HTTPException(400, "stdio type requires a command field")
    if parsed["type"] in ("http", "sse") and not parsed.get("url"):
        raise HTTPException(400, f"{parsed['type']} type requires a url field")

    cli_args = ["mcp", "add-json", "--scope", body.scope, body.name, json.dumps(parsed)]
    cwd = body.projectPath if body.scope == "local" and body.projectPath else None
    stdout, stderr, code = await _run_claude_cli(*cli_args, cwd=cwd)
    if code == 0:
        return {"success": True, "output": stdout, "message": f'MCP server "{body.name}" added successfully via JSON'}
    raise HTTPException(400, f"Claude CLI command failed: {stderr}")


@router.delete("/cli/remove/{name}")
async def cli_remove(name: str, scope: Optional[str] = None):
    actual_name = name
    actual_scope = scope
    if ":" in name:
        prefix, sname = name.split(":", 1)
        actual_name = sname
        actual_scope = actual_scope or prefix
    actual_scope = actual_scope or "user"

    stdout, stderr, code = await _run_claude_cli("mcp", "remove", "--scope", actual_scope, actual_name)
    if code == 0:
        return {"success": True, "output": stdout, "message": f'MCP server "{name}" removed successfully'}
    raise HTTPException(400, f"Claude CLI command failed: {stderr}")


@router.get("/cli/get/{name}")
async def cli_get(name: str):
    stdout, stderr, code = await _run_claude_cli("mcp", "get", name)
    if code == 0:
        return {"success": True, "output": stdout, "server": _parse_get_output(stdout)}
    raise HTTPException(404, f"Claude CLI command failed: {stderr}")


# ---------------------------------------------------------------------------
# Config file reading
# ---------------------------------------------------------------------------

@router.get("/config/read")
async def config_read():
    home = str(Path.home())
    config_paths = [
        os.path.join(home, ".claude.json"),
        os.path.join(home, ".claude", "settings.json"),
    ]
    config_data = None
    config_path = None
    for fp in config_paths:
        try:
            config_data = json.loads(Path(fp).read_text(encoding="utf-8"))
            config_path = fp
            break
        except Exception:
            continue

    if not config_data:
        return {"success": False, "message": "No Claude configuration file found", "servers": []}

    servers = []
    mcp_servers = config_data.get("mcpServers", {})
    for name, cfg in mcp_servers.items():
        server = {"id": name, "name": name, "type": "stdio", "scope": "user", "config": {}, "raw": cfg}
        if cfg.get("command"):
            server["type"] = "stdio"
            server["config"] = {"command": cfg["command"], "args": cfg.get("args", []), "env": cfg.get("env", {})}
        elif cfg.get("url"):
            server["type"] = cfg.get("transport", "http")
            server["config"] = {"url": cfg["url"], "headers": cfg.get("headers", {})}
        servers.append(server)

    # Local-scoped servers
    projects = config_data.get("projects", {})
    for proj_path, proj_cfg in projects.items():
        for name, cfg in proj_cfg.get("mcpServers", {}).items():
            server = {"id": f"local:{name}", "name": name, "type": "stdio", "scope": "local", "projectPath": proj_path, "config": {}, "raw": cfg}
            if cfg.get("command"):
                server["config"] = {"command": cfg["command"], "args": cfg.get("args", []), "env": cfg.get("env", {})}
            elif cfg.get("url"):
                server["type"] = cfg.get("transport", "http")
                server["config"] = {"url": cfg["url"], "headers": cfg.get("headers", {})}
            servers.append(server)

    return {"success": True, "configPath": config_path, "servers": servers}
