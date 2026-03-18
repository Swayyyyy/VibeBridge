"""Application configuration loaded from TOML files."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import runtime_role

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib

_PROJECT_ROOT = Path(__file__).resolve().parent
_CONFIGS_DIR = _PROJECT_ROOT / "configs"
_DEFAULT_TERMINAL_SHELL = "/bin/zsh" if Path("/bin/zsh").is_file() else "/bin/bash"

_COMMON_DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 3000,
    },
    "database": {
        "path": "",
    },
    "auth": {
        "platform_mode": False,
        "jwt_secret": "",
    },
    "main": {
        "node_register_tokens": [],
        "node_addresses": [],
    },
    "node": {
        "main_server_url": "",
        "main_register_url": "",
        "id": "",
        "name": "",
        "register_token": "",
        "labels": [],
        "capabilities": ["claude", "codex"],
        "advertise_host": "",
        "advertise_port": 0,
    },
    "filesystem": {
        "file_tree_max_nodes": 2500,
        "file_tree_max_depth": 6,
    },
    "terminal": {
        "default_shell": _DEFAULT_TERMINAL_SHELL,
    },
    "providers": {
        "claude": {
            "tool_approval_timeout_ms": 55000,
            "context_window": 160000,
        },
        "codex": {
            "tool_approval_timeout_ms": 55000,
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged

def _detect_role() -> str:
    if runtime_role.ROLE_OVERRIDE in {"main", "node"}:
        return runtime_role.ROLE_OVERRIDE

    argv_text = " ".join(sys.argv).lower()
    if "main_server" in argv_text:
        return "main"
    if "app.py" in argv_text or "app:app" in argv_text:
        return "node"
    return "node"


def _load_toml_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a TOML table: {path}")
    return data


def _resolve_config_files(role: str) -> tuple[list[Path], dict[str, Any]]:
    data: dict[str, Any] = dict(_COMMON_DEFAULTS)
    role_path = _CONFIGS_DIR / f"{role}.toml"
    if not role_path.is_file():
        raise FileNotFoundError(f"Config file not found: {role_path.resolve()}")
    data = _deep_merge(data, _load_toml_file(role_path))
    return [role_path.resolve()], data


def _get_nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _get_string(*path: str, default: str = "") -> str:
    value = _get_nested(_CONFIG_DATA, *path)
    if value is None:
        return default
    return str(value).strip()


def _get_bool(*path: str, default: bool = False) -> bool:
    value = _get_nested(_CONFIG_DATA, *path)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_int(*path: str, default: int) -> int:
    value = _get_nested(_CONFIG_DATA, *path)
    if value is None or value == "":
        return default
    return int(value)


def _get_list(*path: str, default: list[str] | None = None) -> list[str]:
    value = _get_nested(_CONFIG_DATA, *path)
    normalized = _normalize_list(value)
    if normalized:
        return normalized
    return list(default or [])


def _resolve_project_path(raw_path: str) -> str:
    if not raw_path:
        return ""

    expanded = Path(raw_path).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((_PROJECT_ROOT / expanded).resolve())


def _format_config_source(path: Path) -> str:
    try:
        return str(path.relative_to(_PROJECT_ROOT))
    except ValueError:
        return str(path)


ROLE = _detect_role()
CONFIG_FILES, _CONFIG_DATA = _resolve_config_files(ROLE)
CONFIG_SOURCE_LABEL = ", ".join(_format_config_source(path) for path in CONFIG_FILES) or "built-in defaults"

# Shared
IS_PLATFORM = _get_bool("auth", "platform_mode", default=False)
DATABASE_PATH = _resolve_project_path(_get_string("database", "path", default=""))
JWT_SECRET_VALUE = _get_string("auth", "jwt_secret", default="")
HOST = _get_string("server", "host", default="0.0.0.0")
PORT = _get_int("server", "port", default=3000)

# Main role
NODE_REGISTER_TOKENS_LIST = _get_list("main", "node_register_tokens")
NODE_ADDRESSES_LIST = _get_list("main", "node_addresses")
NODE_REGISTER_TOKENS = ",".join(NODE_REGISTER_TOKENS_LIST)
NODE_ADDRESSES = ",".join(NODE_ADDRESSES_LIST)

# Node role
MAIN_SERVER_URL = _get_string("node", "main_server_url", default="")
MAIN_REGISTER_URL = _get_string("node", "main_register_url", default="")
NODE_ID = _get_string("node", "id", default="")
NODE_NAME = _get_string("node", "name", default="")
NODE_REGISTER_TOKEN = _get_string("node", "register_token", default="")
NODE_LABELS_LIST = _get_list("node", "labels")
NODE_CAPABILITIES_LIST = _get_list(
    "node",
    "capabilities",
    default=["claude", "codex"],
)
NODE_ADVERTISE_HOST = _get_string("node", "advertise_host", default="")
NODE_ADVERTISE_PORT = _get_int("node", "advertise_port", default=0)
NODE_LABELS = ",".join(NODE_LABELS_LIST)
NODE_CAPABILITIES = ",".join(NODE_CAPABILITIES_LIST)

# Node provider and filesystem tuning
FILE_TREE_MAX_NODES = _get_int(
    "filesystem",
    "file_tree_max_nodes",
    default=2500,
)
FILE_TREE_MAX_DEPTH = _get_int(
    "filesystem",
    "file_tree_max_depth",
    default=6,
)
DEFAULT_TERMINAL_SHELL = _get_string("terminal", "default_shell", default=_DEFAULT_TERMINAL_SHELL)
CLAUDE_TOOL_APPROVAL_TIMEOUT_MS = _get_int(
    "providers",
    "claude",
    "tool_approval_timeout_ms",
    default=55000,
)
CLAUDE_CONTEXT_WINDOW = _get_int(
    "providers",
    "claude",
    "context_window",
    default=160000,
)
CODEX_TOOL_APPROVAL_TIMEOUT_MS = _get_int(
    "providers",
    "codex",
    "tool_approval_timeout_ms",
    default=55000,
)
