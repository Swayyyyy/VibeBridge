"""Slash commands routes.

Provider-aware slash commands:
- Claude custom commands live in `.claude/commands`
- Codex custom prompts live in `.codex/prompts`
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.frontmatter import parse_frontmatter

router = APIRouter(prefix="/api/commands", tags=["commands"])

SUPPORTED_PROVIDERS = {"claude", "codex"}

CLAUDE_MODELS = {
    "DEFAULT": "claude-sonnet-4-20250514",
    "OPTIONS": [
        {"value": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4"},
        {"value": "claude-opus-4-20250514", "label": "Claude Opus 4"},
        {"value": "claude-haiku-4-20250514", "label": "Claude Haiku 4"},
    ],
}

CODEX_MODELS = {
    "DEFAULT": "codex-mini-latest",
    "OPTIONS": [
        {"value": "codex-mini-latest", "label": "Codex Mini"},
        {"value": "o4-mini", "label": "o4-mini"},
        {"value": "o3", "label": "o3"},
        {"value": "gpt-4.1", "label": "GPT 4.1"},
    ],
}

CLAUDE_BUILTIN_COMMANDS = [
    {"name": "/help", "description": "Show help for Claude Code commands", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/clear", "description": "Clear the conversation history", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/model", "description": "Show the current Claude model", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/cost", "description": "Display token usage and cost information", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/init", "description": "Open or create CLAUDE.md for project instructions", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/memory", "description": "Open CLAUDE.md memory file for editing", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/permissions", "description": "Open Claude permission settings", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/review", "description": "Ask Claude to review the current changes", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/config", "description": "Open settings and configuration", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/status", "description": "Show current session status", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/rewind", "description": "Rewind the conversation to a previous state", "namespace": "builtin", "metadata": {"type": "builtin"}},
]

CODEX_BUILTIN_COMMANDS = [
    {"name": "/help", "description": "Show help for Codex slash commands", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/clear", "description": "Clear the conversation history", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/init", "description": "Open or create AGENTS.md for Codex instructions", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/status", "description": "Show current session configuration", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/permissions", "description": "Open Codex permission controls", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/model", "description": "Show the current model and reasoning effort", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/review", "description": "Ask Codex to review the current changes", "namespace": "builtin", "metadata": {"type": "builtin"}},
    {"name": "/config", "description": "Open settings and configuration", "namespace": "builtin", "metadata": {"type": "builtin"}},
]


def _normalize_provider(provider: Optional[str]) -> str:
    if isinstance(provider, str):
        normalized = provider.strip().lower()
        if normalized in SUPPORTED_PROVIDERS:
            return normalized
    return "claude"


def _get_builtin_commands(provider: str) -> list[dict]:
    if provider == "codex":
        return CODEX_BUILTIN_COMMANDS
    return CLAUDE_BUILTIN_COMMANDS


def _get_custom_command_roots(provider: str, project_path: Optional[str]) -> list[tuple[str, str]]:
    if provider == "codex":
        project_root = os.path.join(project_path, ".codex", "prompts") if project_path else None
        user_root = os.path.join(Path.home(), ".codex", "prompts")
    else:
        project_root = os.path.join(project_path, ".claude", "commands") if project_path else None
        user_root = os.path.join(Path.home(), ".claude", "commands")

    roots: list[tuple[str, str]] = []
    if project_root:
        roots.append((project_root, "project"))
    roots.append((user_root, "user"))
    return roots


def _get_custom_label(provider: str) -> str:
    return "prompts" if provider == "codex" else "commands"


async def _scan_commands_directory(directory: str, base_dir: str, namespace: str) -> List[dict]:
    commands: List[dict] = []
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return commands

    for entry in sorted(dir_path.rglob("*.md")):
        if not entry.is_file():
            continue
        try:
            content = entry.read_text(encoding="utf-8")
            metadata, body = parse_frontmatter(content)
            rel = entry.relative_to(base_dir)
            command_name = "/" + str(rel).replace(".md", "").replace("\\", "/")
            description = metadata.get("description", "")
            if not description:
                first_line = body.strip().split("\n")[0] if body.strip() else ""
                description = re.sub(r"^#+\s*", "", first_line).strip()
            commands.append({
                "name": command_name,
                "path": str(entry),
                "relativePath": str(rel),
                "description": description,
                "namespace": namespace,
                "metadata": metadata,
            })
        except Exception as exc:
            print(f"Error parsing command file {entry}: {exc}")

    return commands


def _get_instruction_file_path(provider: str, project_path: Optional[str]) -> tuple[Optional[str], str]:
    if not project_path:
        if provider == "codex":
            return None, "Please select a project to access its AGENTS.md file"
        return None, "Please select a project to access its CLAUDE.md file"

    if provider == "codex":
        return os.path.join(project_path, "AGENTS.md"), "AGENTS.md"
    return os.path.join(project_path, "CLAUDE.md"), "CLAUDE.md"


def _handle_help(args: list, context: dict) -> dict:
    provider = _normalize_provider(context.get("provider"))
    builtins = _get_builtin_commands(provider)
    provider_title = "Codex" if provider == "codex" else "Claude Code"
    custom_label = "Custom Prompts" if provider == "codex" else "Custom Commands"
    root_name = ".codex/prompts" if provider == "codex" else ".claude/commands"

    lines = [f"# {provider_title} Commands\n\n## Built-in Commands\n"]
    for cmd in builtins:
        lines.append(f"### {cmd['name']}\n{cmd['description']}\n")
    lines.append(
        f"\n## {custom_label}\n\n"
        f"{custom_label} can be created in:\n"
        f"- Project: `{root_name}/` (project-specific)\n"
        f"- User: `~/{root_name}/` (available in all projects)\n"
    )
    return {"type": "builtin", "action": "help", "data": {"content": "\n".join(lines), "format": "markdown"}}


def _handle_clear(args: list, context: dict) -> dict:
    return {"type": "builtin", "action": "clear", "data": {"message": "Conversation history cleared"}}


def _handle_model(args: list, context: dict) -> dict:
    available = {
        "claude": [o["value"] for o in CLAUDE_MODELS["OPTIONS"]],
        "codex": [o["value"] for o in CODEX_MODELS["OPTIONS"]],
    }
    provider = _normalize_provider(context.get("provider"))
    default_model = CLAUDE_MODELS["DEFAULT"] if provider == "claude" else CODEX_MODELS["DEFAULT"]
    model = context.get("model", default_model)
    msg = f"Switching to model: {args[0]}" if args else f"Current model: {model}"
    return {"type": "builtin", "action": "model", "data": {"current": {"provider": provider, "model": model}, "available": available, "message": msg}}


def _handle_cost(args: list, context: dict) -> dict:
    tu = context.get("tokenUsage", {})
    provider = _normalize_provider(context.get("provider"))
    model = context.get("model", CLAUDE_MODELS["DEFAULT"] if provider == "claude" else CODEX_MODELS["DEFAULT"])
    used = int(tu.get("used", tu.get("totalUsed", tu.get("total_tokens", 0))) or 0)
    total = int(tu.get("total", tu.get("contextWindow", 160000)) or 160000)
    pct = round(used / total * 100, 1) if total > 0 else 0
    input_tokens = int(tu.get("inputTokens", tu.get("input", tu.get("cumulativeInputTokens", 0))) or 0)
    output_tokens = int(tu.get("outputTokens", tu.get("output", tu.get("cumulativeOutputTokens", 0))) or 0)
    rates = {"claude": (3, 15), "codex": (1.5, 6)}.get(provider, (3, 15))
    input_cost = (input_tokens or used) / 1_000_000 * rates[0]
    output_cost = output_tokens / 1_000_000 * rates[1]
    return {"type": "builtin", "action": "cost", "data": {
        "tokenUsage": {"used": used, "total": total, "percentage": pct},
        "cost": {"input": f"{input_cost:.4f}", "output": f"{output_cost:.4f}", "total": f"{input_cost + output_cost:.4f}"},
        "model": model,
    }}


def _handle_status(args: list, context: dict) -> dict:
    uptime = time.monotonic()
    minutes = int(uptime // 60)
    hours = minutes // 60
    fmt = f"{hours}h {minutes % 60}m" if hours > 0 else f"{minutes}m"
    return {"type": "builtin", "action": "status", "data": {
        "version": "0.1.0",
        "packageName": "cc_server",
        "uptime": fmt,
        "uptimeSeconds": int(uptime),
        "model": context.get("model", CLAUDE_MODELS["DEFAULT"]),
        "provider": _normalize_provider(context.get("provider")),
        "pythonVersion": sys.version.split()[0],
        "platform": sys.platform,
    }}


def _handle_memory(args: list, context: dict) -> dict:
    provider = _normalize_provider(context.get("provider"))
    path, label_or_message = _get_instruction_file_path(provider, context.get("projectPath"))
    if not path:
        return {"type": "builtin", "action": "memory", "data": {"error": "No project selected", "message": label_or_message}}

    exists = os.path.isfile(path)
    present_message = f"Opening {label_or_message} at {path}"
    missing_message = f"{label_or_message} not found at {path}. Create it to store project-specific instructions."
    return {"type": "builtin", "action": "memory", "data": {"path": path, "exists": exists, "message": present_message if exists else missing_message}}


def _handle_init(args: list, context: dict) -> dict:
    provider = _normalize_provider(context.get("provider"))
    path, label_or_message = _get_instruction_file_path(provider, context.get("projectPath"))
    if not path:
        return {"type": "builtin", "action": "memory", "data": {"error": "No project selected", "message": label_or_message}}

    exists = os.path.isfile(path)
    action_message = (
        f"Opening {label_or_message} at {path}"
        if exists
        else f"Opening {label_or_message} at {path}. Save the file to create it."
    )
    return {
        "type": "builtin",
        "action": "memory",
        "data": {
            "path": path,
            "exists": exists,
            "allowCreate": True,
            "message": action_message,
        },
    }


def _handle_permissions(args: list, context: dict) -> dict:
    provider = _normalize_provider(context.get("provider"))
    permission_mode = context.get("permissionMode", "default")
    if provider == "codex":
        message = f"Current Codex permission mode: {permission_mode}. Opening settings."
    else:
        message = "Opening Claude permission settings."
    return {
        "type": "builtin",
        "action": "permissions",
        "data": {
            "provider": provider,
            "permissionMode": permission_mode,
            "message": message,
        },
    }


def _handle_review(args: list, context: dict) -> dict:
    provider = _normalize_provider(context.get("provider"))
    if provider == "codex":
        prompt = "Review the current changes and find bugs, regressions, risky behavior, and missing tests."
    else:
        prompt = "Review the current changes and find bugs, regressions, risky behavior, and missing tests."
    return {
        "type": "builtin",
        "action": "review",
        "data": {
            "prompt": prompt,
            "message": "Starting review...",
        },
    }


def _handle_config(args: list, context: dict) -> dict:
    return {"type": "builtin", "action": "config", "data": {"message": "Opening settings..."}}


def _handle_rewind(args: list, context: dict) -> dict:
    steps = 1
    if args:
        try:
            steps = int(args[0])
        except ValueError:
            return {"type": "builtin", "action": "rewind", "data": {"error": "Invalid steps parameter", "message": "Usage: /rewind [number]"}}
    if steps < 1:
        return {"type": "builtin", "action": "rewind", "data": {"error": "Invalid steps parameter", "message": "Usage: /rewind [number]"}}
    return {"type": "builtin", "action": "rewind", "data": {"steps": steps, "message": f"Rewinding conversation by {steps} step{'s' if steps > 1 else ''}..."}}


BUILTIN_HANDLERS = {
    "/help": _handle_help,
    "/clear": _handle_clear,
    "/model": _handle_model,
    "/cost": _handle_cost,
    "/status": _handle_status,
    "/memory": _handle_memory,
    "/init": _handle_init,
    "/permissions": _handle_permissions,
    "/review": _handle_review,
    "/config": _handle_config,
    "/rewind": _handle_rewind,
}


class ListBody(BaseModel):
    projectPath: Optional[str] = None
    provider: Optional[str] = "claude"


class LoadBody(BaseModel):
    commandPath: str
    projectPath: Optional[str] = None
    provider: Optional[str] = "claude"


class ExecuteBody(BaseModel):
    commandName: str
    commandPath: Optional[str] = None
    provider: Optional[str] = "claude"
    args: list = []
    context: dict = {}


def _is_under(base: str, target: str) -> bool:
    return target.startswith(base + os.sep) or target == base


def _validate_command_path(provider: str, command_path: str, project_path: Optional[str]) -> str:
    resolved = os.path.realpath(command_path)
    valid_roots = [os.path.realpath(root) for root, _ in _get_custom_command_roots(provider, project_path)]

    if not any(_is_under(root, resolved) for root in valid_roots):
        label = _get_custom_label(provider)
        raise HTTPException(403, f"Command must be in a provider {label} directory")

    path = Path(resolved)
    if path.suffix.lower() != ".md":
        raise HTTPException(400, "Command path must point to a Markdown file")
    if not path.exists():
        raise HTTPException(404, f"Command file not found: {command_path}")
    if not path.is_file():
        raise HTTPException(400, "Command path must point to a file")

    return resolved


@router.post("/list")
async def list_commands(body: ListBody):
    try:
        provider = _normalize_provider(body.provider)
        builtins = list(_get_builtin_commands(provider))
        custom: list[dict] = []

        for root, namespace in _get_custom_command_roots(provider, body.projectPath):
            custom.extend(await _scan_commands_directory(root, root, namespace))

        custom = sorted(custom, key=lambda command: command["name"])
        return {
            "provider": provider,
            "builtIn": builtins,
            "custom": custom,
            "count": len(builtins) + len(custom),
        }
    except Exception as exc:
        print(f"Error listing commands: {exc}")
        raise HTTPException(500, f"Failed to list commands: {exc}")


@router.post("/load")
async def load_command(body: LoadBody):
    if not body.commandPath:
        raise HTTPException(400, "Command path is required")

    provider = _normalize_provider(body.provider)
    resolved = _validate_command_path(provider, body.commandPath, body.projectPath)

    try:
        content = Path(resolved).read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"Failed to read command file: {exc}")

    metadata, cmd_content = parse_frontmatter(content)
    return {"path": body.commandPath, "metadata": metadata, "content": cmd_content}


@router.post("/execute")
async def execute_command(body: ExecuteBody):
    if not body.commandName:
        raise HTTPException(400, "Command name is required")

    provider = _normalize_provider(body.provider or body.context.get("provider"))
    command_context = {**body.context, "provider": provider}
    available_builtins = {command["name"] for command in _get_builtin_commands(provider)}

    handler = BUILTIN_HANDLERS.get(body.commandName)
    if handler and body.commandName in available_builtins:
        try:
            result = handler(body.args, command_context)
            return {**result, "command": body.commandName}
        except Exception as exc:
            raise HTTPException(500, f"Command execution failed: {exc}")

    if not body.commandPath:
        raise HTTPException(404, f"Unknown command: {body.commandName}")

    resolved = _validate_command_path(provider, body.commandPath, command_context.get("projectPath"))

    try:
        content = Path(resolved).read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"Failed to read command file: {exc}")

    metadata, cmd_content = parse_frontmatter(content)

    processed = cmd_content.replace("$ARGUMENTS", " ".join(body.args))
    for index, arg in enumerate(body.args):
        processed = re.sub(rf"\${index + 1}\b", arg, processed)

    return {
        "type": "custom",
        "command": body.commandName,
        "content": processed,
        "metadata": metadata,
        "hasFileIncludes": "@" in processed,
        "hasBashCommands": "!" in processed,
    }
