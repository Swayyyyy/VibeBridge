"""Plugins routes — stub port of server/routes/plugins.js.

Plugin management: scan, install, uninstall, enable/disable, serve assets, RPC proxy.
Full implementation deferred — exposes route structure with basic scanning.
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

PLUGINS_DIR = Path.home() / ".claude" / "plugins"
PLUGINS_CONFIG = PLUGINS_DIR / "config.json"


def _get_plugins_config() -> dict:
    try:
        return json.loads(PLUGINS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_plugins_config(config: dict):
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    PLUGINS_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _scan_plugins() -> list:
    """Scan the plugins directory for installed plugins."""
    plugins = []
    if not PLUGINS_DIR.is_dir():
        return plugins
    config = _get_plugins_config()
    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name = manifest.get("name", entry.name)
            plugin_cfg = config.get(name, {})
            plugins.append({
                "name": name,
                "dirName": entry.name,
                "description": manifest.get("description", ""),
                "version": manifest.get("version", "0.0.0"),
                "enabled": plugin_cfg.get("enabled", True),
                "server": manifest.get("server"),
                "ui": manifest.get("ui"),
                "serverRunning": False,  # Stub — no process manager yet
            })
        except Exception:
            continue
    return plugins


def _validate_plugin_name(name: str):
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(400, "Invalid plugin name")


@router.get("")
async def list_plugins():
    try:
        return {"plugins": _scan_plugins()}
    except Exception as e:
        raise HTTPException(500, f"Failed to scan plugins: {e}")


@router.get("/{name}/manifest")
async def get_manifest(name: str):
    _validate_plugin_name(name)
    plugins = _scan_plugins()
    plugin = next((p for p in plugins if p["name"] == name), None)
    if not plugin:
        raise HTTPException(404, "Plugin not found")
    return plugin


@router.get("/{name}/assets/{asset_path:path}")
async def get_asset(name: str, asset_path: str):
    _validate_plugin_name(name)
    if not asset_path:
        raise HTTPException(400, "No asset path specified")
    plugin_dir = PLUGINS_DIR / name
    if not plugin_dir.is_dir():
        # Try by directory name
        for d in PLUGINS_DIR.iterdir():
            if d.is_dir():
                mf = d / "manifest.json"
                if mf.is_file():
                    try:
                        m = json.loads(mf.read_text())
                        if m.get("name") == name:
                            plugin_dir = d
                            break
                    except Exception:
                        pass
    resolved = (plugin_dir / asset_path).resolve()
    if not str(resolved).startswith(str(plugin_dir.resolve())):
        raise HTTPException(403, "Path traversal not allowed")
    if not resolved.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(resolved)


class EnableBody(BaseModel):
    enabled: bool


@router.put("/{name}/enable")
async def enable_plugin(name: str, body: EnableBody):
    _validate_plugin_name(name)
    plugins = _scan_plugins()
    plugin = next((p for p in plugins if p["name"] == name), None)
    if not plugin:
        raise HTTPException(404, "Plugin not found")
    config = _get_plugins_config()
    config[name] = {**config.get(name, {}), "enabled": body.enabled}
    _save_plugins_config(config)
    return {"success": True, "name": name, "enabled": body.enabled}


class InstallBody(BaseModel):
    url: str


@router.post("/install")
async def install_plugin(body: InstallBody):
    """Stub — full git clone + npm install implementation deferred."""
    raise HTTPException(501, "Plugin installation not yet implemented in Python backend")


@router.post("/{name}/update")
async def update_plugin(name: str):
    _validate_plugin_name(name)
    raise HTTPException(501, "Plugin update not yet implemented in Python backend")


@router.delete("/{name}")
async def uninstall_plugin(name: str):
    _validate_plugin_name(name)
    raise HTTPException(501, "Plugin uninstall not yet implemented in Python backend")
