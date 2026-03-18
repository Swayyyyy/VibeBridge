"""Claude SDK provider — port of server/claude-sdk.js.

Uses Anthropic's official Python SDK for Claude Code / Claude Agent.
Key features: streaming, tool approval, session management, image handling.
"""
import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from config import CLAUDE_TOOL_APPROVAL_TIMEOUT_MS, CLAUDE_CONTEXT_WINDOW

try:
    from claude_agent_sdk import (
        ClaudeAgentOptions as ClaudeSDKOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        PermissionUpdate,
        query,
        AssistantMessage,
        UserMessage,
        SystemMessage,
        ResultMessage,
    )
except ImportError:
    # Backward-compatible fallback for older environments still on the
    # deprecated package name.
    from claude_code_sdk import (
        ClaudeCodeOptions as ClaudeSDKOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        PermissionUpdate,
        query,
        AssistantMessage,
        UserMessage,
        SystemMessage,
        ResultMessage,
    )

# ---------------------------------------------------------------------------
# Session & tool approval tracking
# ---------------------------------------------------------------------------

active_sessions: dict[str, dict] = {}
pending_tool_approvals: dict[str, dict] = {}

TOOL_APPROVAL_TIMEOUT = CLAUDE_TOOL_APPROVAL_TIMEOUT_MS / 1000
TOOLS_REQUIRING_INTERACTION = {"AskUserQuestion"}

# Claude model defaults
CLAUDE_DEFAULT_MODEL = "sonnet"


def _create_request_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Tool approval system
# ---------------------------------------------------------------------------

async def wait_for_tool_approval(
    request_id: str,
    *,
    timeout: float | None = None,
    signal_event: asyncio.Event | None = None,
    on_cancel: Any = None,
    metadata: dict | None = None,
) -> dict | None:
    """Wait for UI to respond to a tool permission request."""
    if timeout is None:
        timeout = TOOL_APPROVAL_TIMEOUT

    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()

    def _resolve(decision):
        if not future.done():
            future.set_result(decision)

    entry = {"resolve": _resolve, **(metadata or {})}
    pending_tool_approvals[request_id] = entry

    # If signal is already set (abort), resolve immediately
    if signal_event and signal_event.is_set():
        on_cancel and on_cancel("cancelled")
        pending_tool_approvals.pop(request_id, None)
        return {"cancelled": True}

    # Listen for abort
    cancel_task = None
    if signal_event:
        async def _wait_abort():
            await signal_event.wait()
            on_cancel and on_cancel("cancelled")
            _resolve({"cancelled": True})
        cancel_task = asyncio.create_task(_wait_abort())

    try:
        if timeout > 0:
            result = await asyncio.wait_for(future, timeout=timeout)
        else:
            # timeout 0 = wait indefinitely (interactive tools)
            result = await future
    except asyncio.TimeoutError:
        on_cancel and on_cancel("timeout")
        result = None
    finally:
        pending_tool_approvals.pop(request_id, None)
        if cancel_task and not cancel_task.done():
            cancel_task.cancel()

    return result


def resolve_tool_approval(request_id: str, decision: dict):
    """Resolve a pending tool approval with a UI decision."""
    entry = pending_tool_approvals.get(request_id)
    if entry and "resolve" in entry:
        entry["resolve"](decision)


def _matches_tool_permission(entry: str, tool_name: str, tool_input: Any) -> bool:
    """Check if a permission entry matches a tool + input combo."""
    if not entry or not tool_name:
        return False
    if entry == tool_name:
        return True
    # Bash(prefix:*) shorthand
    m = re.match(r"^Bash\((.+):\*\)$", entry)
    if tool_name == "Bash" and m:
        prefix = m.group(1)
        command = ""
        if isinstance(tool_input, str):
            command = tool_input.strip()
        elif isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
            command = tool_input["command"].strip()
        return command.startswith(prefix) if command else False
    return False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def add_session(session_id: str, *, abort_event: asyncio.Event, temp_paths=None, temp_dir=None, writer=None):
    active_sessions[session_id] = {
        "abort_event": abort_event,
        "start_time": time.time(),
        "status": "active",
        "temp_paths": temp_paths or [],
        "temp_dir": temp_dir,
        "writer": writer,
    }


def remove_session(session_id: str):
    active_sessions.pop(session_id, None)


def get_session(session_id: str):
    return active_sessions.get(session_id)


def get_all_sessions() -> list[str]:
    return list(active_sessions.keys())


def get_active_claude_sessions() -> list[str]:
    return [sid for sid, s in active_sessions.items() if s["status"] == "active"]


def is_claude_session_active(session_id: str) -> bool:
    s = active_sessions.get(session_id)
    return bool(s and s["status"] == "active")


# ---------------------------------------------------------------------------
# Message serialisation helpers
# ---------------------------------------------------------------------------

def _msg_to_dict(msg) -> dict:
    """Convert SDK message dataclass to a JSON-serialisable dict."""
    try:
        d = asdict(msg)
    except Exception:
        d = {"raw": str(msg)}
    # Add type tag
    if isinstance(msg, AssistantMessage):
        d["type"] = "assistant"
    elif isinstance(msg, UserMessage):
        d["type"] = "user"
    elif isinstance(msg, SystemMessage):
        d["type"] = "system"
    elif isinstance(msg, ResultMessage):
        d["type"] = "result"
    else:
        # StreamEvent
        d["type"] = getattr(msg, "type", "stream_event")
    return _normalize_sdk_message_payload(d)


def _infer_content_part_type(part: dict[str, Any]) -> str | None:
    """Infer missing content block types from Claude SDK payload shapes."""
    if not isinstance(part, dict):
        return None
    if isinstance(part.get("type"), str) and part["type"]:
        return part["type"]
    if isinstance(part.get("text"), str):
        return "text"
    if isinstance(part.get("thinking"), str):
        return "thinking"
    if part.get("tool_use_id"):
        return "tool_result"
    if part.get("id") and part.get("name"):
        return "tool_use"
    return None


def _normalize_sdk_message_payload(value: Any) -> Any:
    """Normalize SDK payloads so frontend sees stable message/content shapes."""
    if isinstance(value, list):
        return [_normalize_sdk_message_payload(item) for item in value]

    if not isinstance(value, dict):
        return value

    normalized = {
        key: _normalize_sdk_message_payload(item)
        for key, item in value.items()
    }

    content = normalized.get("content")
    if isinstance(content, list):
        normalized_content = []
        for part in content:
            if isinstance(part, dict):
                inferred_type = _infer_content_part_type(part)
                if inferred_type and not part.get("type"):
                    part = {**part, "type": inferred_type}
            normalized_content.append(part)
        normalized["content"] = normalized_content

    if normalized.get("type") in {"assistant", "user"} and not normalized.get("role"):
        normalized["role"] = normalized["type"]

    message = normalized.get("message")
    if isinstance(message, dict):
        normalized["message"] = _normalize_sdk_message_payload(message)

    return normalized


def _extract_message_session_id(msg) -> str | None:
    """Extract session_id from SDK messages across old/new package versions."""
    msg_session_id = getattr(msg, "session_id", None)
    if msg_session_id:
        return msg_session_id

    if isinstance(msg, SystemMessage):
        data = getattr(msg, "data", None)
        if isinstance(data, dict):
            raw_session_id = data.get("session_id")
            if isinstance(raw_session_id, str) and raw_session_id:
                return raw_session_id

    return None


async def _single_prompt_stream(command: str, session_id: str | None):
    """Wrap a single user prompt in the streaming input shape expected by the SDK."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": command},
        "parent_tool_use_id": None,
        "session_id": session_id or "",
    }


# ---------------------------------------------------------------------------
# Image handling
# ---------------------------------------------------------------------------

async def _handle_images(command: str, images: list | None, cwd: str | None):
    """Save base64 images to temp files and modify prompt."""
    temp_paths = []
    temp_dir = None

    if not images:
        return command, temp_paths, temp_dir

    work_dir = cwd or os.getcwd()
    temp_dir = os.path.join(work_dir, ".tmp", "images", str(int(time.time() * 1000)))
    os.makedirs(temp_dir, exist_ok=True)

    for i, image in enumerate(images):
        m = re.match(r"^data:([^;]+);base64,(.+)$", image.get("data", ""))
        if not m:
            continue
        mime_type, b64_data = m.group(1), m.group(2)
        ext = mime_type.split("/")[-1] or "png"
        filepath = os.path.join(temp_dir, f"image_{i}.{ext}")
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64_data))
        temp_paths.append(filepath)

    if temp_paths and command and command.strip():
        note = "\n\n[Images provided at the following paths:]\n"
        note += "\n".join(f"{i+1}. {p}" for i, p in enumerate(temp_paths))
        command += note

    return command, temp_paths, temp_dir


async def _cleanup_temp_files(temp_paths: list, temp_dir: str | None):
    """Remove temporary image files."""
    for p in temp_paths:
        try:
            os.unlink(p)
        except OSError:
            pass
    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# MCP config loading
# ---------------------------------------------------------------------------

def _load_mcp_config(cwd: str | None) -> dict | None:
    """Load MCP server configurations from ~/.claude.json."""
    config_path = Path.home() / ".claude.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    servers: dict = {}
    if isinstance(data.get("mcpServers"), dict):
        servers.update(data["mcpServers"])
    if cwd and isinstance(data.get("claudeProjects"), dict):
        proj = data["claudeProjects"].get(cwd)
        if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
            servers.update(proj["mcpServers"])

    return servers if servers else None


# ---------------------------------------------------------------------------
# Options mapping
# ---------------------------------------------------------------------------

def _map_options(options: dict) -> ClaudeSDKOptions:
    """Map frontend options to the installed Claude SDK options type."""
    tools_settings = options.get("toolsSettings") or {}
    permission_mode = options.get("permissionMode", "default")
    allowed_tools = list(tools_settings.get("allowedTools") or [])
    disallowed_tools = list(tools_settings.get("disallowedTools") or [])

    # Handle skip permissions
    sdk_permission_mode = None
    if tools_settings.get("skipPermissions") and permission_mode != "plan":
        sdk_permission_mode = "bypassPermissions"
    elif permission_mode and permission_mode != "default":
        sdk_permission_mode = permission_mode

    # Plan mode default tools
    if permission_mode == "plan":
        plan_tools = ["Read", "Task", "exit_plan_mode", "TodoRead", "TodoWrite", "WebFetch", "WebSearch"]
        for t in plan_tools:
            if t not in allowed_tools:
                allowed_tools.append(t)

    model = options.get("model") or CLAUDE_DEFAULT_MODEL
    thinking_effort = options.get("thinkingEffort") or options.get("effort")
    if thinking_effort == "ultra-high":
        thinking_effort = "max"
    elif thinking_effort not in {"low", "medium", "high", "max"}:
        thinking_effort = None

    opts = ClaudeSDKOptions(
        cwd=options.get("cwd") or options.get("projectPath"),
        model=model,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
    )

    if sdk_permission_mode:
        opts.permission_mode = sdk_permission_mode

    if thinking_effort and hasattr(opts, "effort"):
        opts.effort = thinking_effort

    # Resume session
    if options.get("sessionId"):
        opts.resume = options["sessionId"]

    # MCP servers
    mcp = _load_mcp_config(options.get("cwd"))
    if mcp:
        opts.mcp_servers = mcp

    return opts


# ---------------------------------------------------------------------------
# Token budget extraction
# ---------------------------------------------------------------------------

def _extract_token_budget(msg) -> dict | None:
    """Extract token usage from a ResultMessage."""
    if not isinstance(msg, ResultMessage):
        return None
    usage = getattr(msg, "usage", None)
    if not usage or not isinstance(usage, dict):
        return None

    # Newer claude-agent-sdk versions expose a flat usage dict, while older
    # claude-code-sdk builds nest per-model cumulative counters.
    if any(key in usage for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )):
        input_t = usage.get("input_tokens", 0) or 0
        output_t = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0
        total_used = input_t + output_t + cache_read + cache_create
    else:
        model_key = next(iter(usage), None)
        model_data = usage.get(model_key) if model_key else None
        if not model_data or not isinstance(model_data, dict):
            return None

        input_t = model_data.get("cumulativeInputTokens") or model_data.get("inputTokens", 0)
        output_t = model_data.get("cumulativeOutputTokens") or model_data.get("outputTokens", 0)
        cache_read = model_data.get("cumulativeCacheReadInputTokens") or model_data.get("cacheReadInputTokens", 0)
        cache_create = model_data.get("cumulativeCacheCreationInputTokens") or model_data.get("cacheCreationInputTokens", 0)
        total_used = input_t + output_t + cache_read + cache_create

    context_window = CLAUDE_CONTEXT_WINDOW
    return {"used": total_used, "total": context_window}


# ---------------------------------------------------------------------------
# Main query function
# ---------------------------------------------------------------------------

async def query_claude_sdk(command: str, options: dict, ws):
    """Execute a Claude query with streaming over WebSocket.

    Args:
        command: User prompt.
        options: Frontend options dict.
        ws: WebSocketWriter (has .send(dict) method).
    """
    session_id = options.get("sessionId")
    captured_session_id = session_id
    session_created_sent = False
    temp_paths: list[str] = []
    temp_dir: str | None = None
    abort_event = asyncio.Event()

    try:
        # Build SDK options
        sdk_opts = _map_options(options)

        # Handle images
        final_command, temp_paths, temp_dir = await _handle_images(
            command, options.get("images"), options.get("cwd")
        )

        # Build canUseTool callback
        allowed_tools = list(sdk_opts.allowed_tools or [])
        disallowed_tools = list(sdk_opts.disallowed_tools or [])

        async def can_use_tool(tool_name: str, tool_input: dict, context) -> Any:
            nonlocal allowed_tools, disallowed_tools

            requires_interaction = tool_name in TOOLS_REQUIRING_INTERACTION

            if not requires_interaction:
                if sdk_opts.permission_mode == "bypassPermissions":
                    return PermissionResultAllow(updated_input=tool_input)

                is_disallowed = any(_matches_tool_permission(e, tool_name, tool_input) for e in disallowed_tools)
                if is_disallowed:
                    return PermissionResultDeny(message="Tool disallowed by settings")

                is_allowed = any(_matches_tool_permission(e, tool_name, tool_input) for e in allowed_tools)
                if is_allowed:
                    return PermissionResultAllow(updated_input=tool_input)

            request_id = _create_request_id()
            ws.send({
                "type": "claude-permission-request",
                "requestId": request_id,
                "toolName": tool_name,
                "input": tool_input,
                "sessionId": captured_session_id or session_id,
            })

            timeout = 0 if requires_interaction else TOOL_APPROVAL_TIMEOUT
            decision = await wait_for_tool_approval(
                request_id,
                timeout=timeout,
                signal_event=abort_event,
                metadata={
                    "_sessionId": captured_session_id or session_id,
                    "_toolName": tool_name,
                    "_input": tool_input,
                    "_receivedAt": time.time(),
                },
                on_cancel=lambda reason: ws.send({
                    "type": "claude-permission-cancelled",
                    "requestId": request_id,
                    "reason": reason,
                    "sessionId": captured_session_id or session_id,
                }),
            )

            if not decision:
                return PermissionResultDeny(message="Permission request timed out")
            if decision.get("cancelled"):
                return PermissionResultDeny(message="Permission request cancelled")
            if decision.get("allow"):
                # Remember tool if requested
                remember_entry = decision.get("rememberEntry")
                if remember_entry and isinstance(remember_entry, str):
                    if remember_entry not in allowed_tools:
                        allowed_tools.append(remember_entry)
                    disallowed_tools = [e for e in disallowed_tools if e != remember_entry]
                return PermissionResultAllow(
                    updated_input=decision.get("updatedInput") or tool_input
                )

            return PermissionResultDeny(message=decision.get("message", "User denied tool use"))

        sdk_opts.can_use_tool = can_use_tool

        # Track session
        if captured_session_id:
            add_session(captured_session_id, abort_event=abort_event,
                        temp_paths=temp_paths, temp_dir=temp_dir, writer=ws)

        # Execute streaming query
        print(f"[Claude SDK] Starting query, session={captured_session_id or 'NEW'}, model={sdk_opts.model}")

        prompt_input = _single_prompt_stream(final_command, session_id)

        async for message in query(prompt=prompt_input, options=sdk_opts):
            # Capture session_id from init system messages, stream events, or results.
            msg_session_id = _extract_message_session_id(message)
            if msg_session_id and not captured_session_id:
                captured_session_id = msg_session_id
                add_session(captured_session_id, abort_event=abort_event,
                            temp_paths=temp_paths, temp_dir=temp_dir, writer=ws)

                if hasattr(ws, "set_session_id"):
                    ws.set_session_id(captured_session_id)

                if not session_id and not session_created_sent:
                    session_created_sent = True
                    ws.send({"type": "session-created", "sessionId": captured_session_id})

            # Transform and send
            transformed = _msg_to_dict(message)
            if getattr(message, "parent_tool_use_id", None):
                transformed["parentToolUseId"] = message.parent_tool_use_id

            ws.send({
                "type": "claude-response",
                "data": transformed,
                "sessionId": captured_session_id or session_id,
            })

            # Token budget from result
            if isinstance(message, ResultMessage):
                budget = _extract_token_budget(message)
                if budget:
                    ws.send({
                        "type": "token-budget",
                        "data": budget,
                        "sessionId": captured_session_id or session_id,
                    })

        # Cleanup
        if captured_session_id:
            remove_session(captured_session_id)
        await _cleanup_temp_files(temp_paths, temp_dir)

        ws.send({
            "type": "claude-complete",
            "sessionId": captured_session_id,
            "exitCode": 0,
            "isNewSession": not session_id and bool(command),
        })
        print(f"[Claude SDK] Complete, session={captured_session_id}")

    except Exception as e:
        print(f"[Claude SDK] Error: {e}")
        if captured_session_id:
            remove_session(captured_session_id)
        await _cleanup_temp_files(temp_paths, temp_dir)
        ws.send({
            "type": "claude-error",
            "error": str(e),
            "sessionId": captured_session_id or session_id,
        })


async def abort_claude_session(session_id: str) -> bool:
    """Abort an active Claude SDK session."""
    session = get_session(session_id)
    if not session:
        return False
    try:
        session["abort_event"].set()
        session["status"] = "aborted"
        await _cleanup_temp_files(session.get("temp_paths", []), session.get("temp_dir"))
        remove_session(session_id)
        return True
    except Exception as e:
        print(f"[Claude SDK] Error aborting {session_id}: {e}")
        return False


def get_pending_approvals_for_session(session_id: str) -> list[dict]:
    """Get pending tool approvals for a specific session."""
    pending = []
    for req_id, entry in pending_tool_approvals.items():
        if entry.get("_sessionId") == session_id:
            pending.append({
                "requestId": req_id,
                "toolName": entry.get("_toolName", "UnknownTool"),
                "input": entry.get("_input"),
                "sessionId": session_id,
                "receivedAt": entry.get("_receivedAt"),
            })
    return pending


def reconnect_session_writer(session_id: str, new_ws) -> bool:
    """Reconnect a session's writer to a new WebSocket."""
    session = get_session(session_id)
    if not session or not session.get("writer"):
        return False
    if hasattr(session["writer"], "update_websocket"):
        session["writer"].update_websocket(new_ws)
        print(f"[Claude SDK] Writer swapped for session {session_id}")
        return True
    return False
