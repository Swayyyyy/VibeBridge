"""Codex provider with MCP streaming and exec fallback.

Primary path:
- Spawn `codex mcp-server`
- Call the `codex` / `codex-reply` MCP tools
- Translate `codex/event` notifications into the frontend's existing
  `codex-response` websocket payloads

Fallback path:
- Use `codex exec --json` when MCP bootstrap fails before any work starts

This keeps the currently working CLI flow as a safety net while enabling the
fuller event model that projects like Happy use.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import time
import uuid
from functools import lru_cache
from typing import Any

from pydantic import RootModel
from config import CODEX_TOOL_APPROVAL_TIMEOUT_MS

try:
    import mcp.types as mcp_types
    from mcp import ClientSession, StdioServerParameters, stdio_client
    from mcp.shared.context import RequestContext
    from mcp.shared.message import SessionMessage
except ImportError:  # pragma: no cover - fallback path covers missing MCP lib
    mcp_types = None
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None
    RequestContext = None
    SessionMessage = None


# ---------------------------------------------------------------------------
# Session & approval tracking
# ---------------------------------------------------------------------------

active_codex_sessions: dict[str, dict[str, Any]] = {}
pending_codex_approvals: dict[str, dict[str, Any]] = {}

CODEX_TOOL_APPROVAL_TIMEOUT = CODEX_TOOL_APPROVAL_TIMEOUT_MS / 1000


def _create_request_id() -> str:
    return str(uuid.uuid4())


def _move_active_session(old_session_id: str, new_session_id: str) -> None:
    """Re-key an in-flight session once Codex emits the real thread UUID."""
    if not new_session_id or old_session_id == new_session_id:
        return

    session = active_codex_sessions.pop(old_session_id, None)
    if session is not None:
        active_codex_sessions[new_session_id] = session


def _add_active_session(
    session_id: str,
    *,
    abort_event: asyncio.Event,
    provider: str,
    task: asyncio.Task | None = None,
    proc: asyncio.subprocess.Process | None = None,
) -> None:
    active_codex_sessions[session_id] = {
        "status": "running",
        "abort_event": abort_event,
        "provider": provider,
        "started_at": time.time(),
        "task": task,
        "proc": proc,
    }


def _set_active_session_process(session_id: str, proc: asyncio.subprocess.Process) -> None:
    session = active_codex_sessions.get(session_id)
    if session is not None:
        session["proc"] = proc


def _set_active_session_task(session_id: str, task: asyncio.Task | None) -> None:
    session = active_codex_sessions.get(session_id)
    if session is not None:
        session["task"] = task


def _mark_active_session_completed(session_id: str) -> None:
    session = active_codex_sessions.get(session_id)
    if session and session.get("status") != "aborted":
        session["status"] = "completed"


async def wait_for_codex_approval(
    request_id: str,
    *,
    timeout: float | None = None,
    signal_event: asyncio.Event | None = None,
    on_cancel: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Wait for the UI to approve or deny a pending Codex MCP tool request."""
    if timeout is None:
        timeout = CODEX_TOOL_APPROVAL_TIMEOUT

    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()

    def _resolve(decision: dict[str, Any]) -> None:
        if not future.done():
            future.set_result(decision)

    entry = {"resolve": _resolve, **(metadata or {})}
    pending_codex_approvals[request_id] = entry

    if signal_event and signal_event.is_set():
        on_cancel and on_cancel("cancelled")
        pending_codex_approvals.pop(request_id, None)
        return {"cancelled": True}

    cancel_task = None
    if signal_event:
        async def _wait_abort() -> None:
            await signal_event.wait()
            on_cancel and on_cancel("cancelled")
            _resolve({"cancelled": True})

        cancel_task = asyncio.create_task(_wait_abort())

    try:
        if timeout and timeout > 0:
            result = await asyncio.wait_for(future, timeout=timeout)
        else:
            result = await future
    except asyncio.TimeoutError:
        on_cancel and on_cancel("timeout")
        result = None
    finally:
        pending_codex_approvals.pop(request_id, None)
        if cancel_task and not cancel_task.done():
            cancel_task.cancel()

    return result


def resolve_codex_approval(request_id: str, decision: dict[str, Any]) -> bool:
    """Resolve a pending Codex tool approval with a UI decision."""
    entry = pending_codex_approvals.get(request_id)
    if entry and "resolve" in entry:
        entry["resolve"](decision)
        return True
    return False


def get_pending_codex_approvals_for_session(session_id: str) -> list[dict[str, Any]]:
    """Return pending Codex approvals for the given session."""
    pending: list[dict[str, Any]] = []
    for req_id, entry in pending_codex_approvals.items():
        if entry.get("_sessionId") != session_id:
            continue
        pending.append({
            "requestId": req_id,
            "toolName": entry.get("_toolName", "CodexTool"),
            "input": entry.get("_input"),
            "context": entry.get("_context"),
            "sessionId": session_id,
            "receivedAt": entry.get("_receivedAt"),
        })
    pending.sort(key=lambda item: item.get("receivedAt") or 0)
    return pending


# ---------------------------------------------------------------------------
# MCP support
# ---------------------------------------------------------------------------

if mcp_types is not None:
    class CodexServerNotification(
        RootModel[mcp_types.ServerNotificationType | mcp_types.JSONRPCNotification]
    ):
        """Allow standard MCP notifications plus Codex's custom codex/event."""

    class CodexClientSession(ClientSession):
        """ClientSession that accepts codex/event as a raw JSON-RPC notification."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._receive_notification_type = CodexServerNotification

        async def _send_raw_result(self, request_id: str | int, result: dict[str, Any]) -> None:
            jsonrpc_response = mcp_types.JSONRPCResponse(
                jsonrpc="2.0",
                id=request_id,
                result=result,
            )
            await self._write_stream.send(
                SessionMessage(message=mcp_types.JSONRPCMessage(jsonrpc_response))
            )

        async def _received_request(self, responder) -> None:
            if not isinstance(responder.request.root, mcp_types.ElicitRequest):
                await super()._received_request(responder)
                return

            ctx = RequestContext[ClientSession, Any](
                request_id=responder.request_id,
                meta=responder.request_meta,
                session=self,
                lifespan_context=None,
            )

            with responder:
                response = await self._elicitation_callback(ctx, responder.request.root.params)

                if isinstance(response, dict) and "decision" in response:
                    responder._completed = True  # type: ignore[attr-defined]
                    await self._send_raw_result(responder.request_id, response)
                    return

                if isinstance(response, mcp_types.ErrorData):
                    await responder.respond(response)
                    return

                if isinstance(response, mcp_types.ElicitResult):
                    await responder.respond(response)
                    return

                await responder.respond(mcp_types.ElicitResult.model_validate(response))
else:  # pragma: no cover - only used when MCP isn't installed
    CodexServerNotification = None
    CodexClientSession = None


class CodexMcpBootstrapError(RuntimeError):
    """Raised when MCP fails before we can safely stream/execute work."""


@lru_cache(maxsize=1)
def _get_codex_mcp_subcommand() -> str:
    """Return the correct MCP subcommand for the installed Codex CLI."""
    try:
        completed = subprocess.run(
            ["codex", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "mcp-server"

    version_output = (completed.stdout or completed.stderr or "").strip()
    match = re.search(r"codex-cli\s+(\d+)\.(\d+)\.(\d+)(?:-alpha\.(\d+))?", version_output)
    if not match:
        return "mcp-server"

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    alpha = int(match.group(4)) if match.group(4) is not None else None

    if major > 0 or minor > 43:
        return "mcp-server"
    if minor == 43 and patch == 0:
        if alpha is None:
            return "mcp-server"
        return "mcp-server" if alpha >= 5 else "mcp"
    return "mcp"


def _build_codex_execution_policy(mode: str, reasoning_effort: str | None) -> dict[str, Any]:
    """Map frontend modes to Codex execution settings."""
    if mode == "acceptEdits":
        sandbox = "workspace-write"
        approval_policy = "never"
    elif mode == "bypassPermissions":
        sandbox = "danger-full-access"
        approval_policy = "never"
    elif mode == "plan":
        sandbox = "read-only"
        approval_policy = "untrusted"
    else:
        sandbox = "workspace-write"
        approval_policy = "untrusted"

    config: dict[str, Any] = {}
    if reasoning_effort:
        config["model_reasoning_effort"] = reasoning_effort

    return {
        "approval_policy": approval_policy,
        "sandbox": sandbox,
        "config": config,
    }


def _get_first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _extract_thread_id(event: dict[str, Any], meta: dict[str, Any] | None = None) -> str | None:
    meta = meta or {}
    return _get_first_string(
        event.get("thread_id"),
        event.get("threadId"),
        event.get("session_id"),
        event.get("sessionId"),
        meta.get("threadId"),
        meta.get("sessionId"),
    )


def _format_command(command: Any) -> str | None:
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        parts = [str(part) for part in command]
        return " ".join(shlex.quote(part) for part in parts)
    return None


def _extract_patch_changes(changes: Any) -> list[dict[str, Any]]:
    if not isinstance(changes, dict):
        return []

    flattened: list[dict[str, Any]] = []
    for path, change in changes.items():
        if not isinstance(path, str):
            continue
        kind = "change"
        if isinstance(change, dict):
            kind = str(change.get("type") or change.get("kind") or "change")
        flattened.append({"kind": kind, "path": path})
    return flattened


def _extract_result_text(result: Any) -> str | None:
    if result is None:
        return None

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        content = structured.get("content")
        if isinstance(content, str) and content:
            return content

    content_blocks = getattr(result, "content", None)
    if isinstance(content_blocks, list):
        text_parts: list[str] = []
        for block in content_blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)

    return None


def _extract_result_thread_id(result: Any) -> str | None:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        thread_id = structured.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


def _announce_session_if_needed(state: dict[str, Any], ws) -> None:
    if state.get("requested_session_id"):
        return
    if state.get("session_announced"):
        return
    if not state.get("actual_session_id"):
        return

    state["session_announced"] = True
    ws.send({
        "type": "session-created",
        "sessionId": state["actual_session_id"],
        "provider": "codex",
    })


def _update_codex_session_id(state: dict[str, Any], session_id: str | None, ws) -> None:
    if not session_id:
        return

    previous = state["current_session_id"]
    state["actual_session_id"] = session_id
    if previous != session_id:
        _move_active_session(previous, session_id)
        state["current_session_id"] = session_id

    _announce_session_if_needed(state, ws)


def _transform_codex_mcp_event(event: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate Codex MCP events into the frontend's existing event model."""
    event_type = event.get("type", "")

    if event_type == "session_configured":
        session_id = _get_first_string(event.get("session_id"), event.get("thread_id"))
        if session_id:
            return [{"type": "thread_started", "threadId": session_id}]
        return []

    if event_type == "task_started":
        state["saw_final_agent_message"] = False
        return [{"type": "turn_started"}]

    if event_type == "agent_message":
        message = event.get("message")
        if not isinstance(message, str) or not message.strip():
            return []

        phase = event.get("phase")
        if phase == "final_answer":
            state["saw_final_agent_message"] = True
            return [{
                "type": "item",
                "itemType": "agent_message",
                "message": {"role": "assistant", "content": message},
            }]

        return [{
            "type": "item",
            "itemType": "reasoning",
            "message": {"role": "assistant", "content": message, "isReasoning": True},
        }]

    if event_type == "agent_reasoning":
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return []
        return [{
            "type": "item",
            "itemType": "reasoning",
            "message": {"role": "assistant", "content": text, "isReasoning": True},
        }]

    if event_type == "exec_command_end":
        return [{
            "type": "item",
            "itemType": "command_execution",
            "command": _format_command(event.get("command")),
            "output": event.get("aggregated_output") or event.get("formatted_output") or event.get("stdout"),
            "exitCode": event.get("exit_code"),
            "status": event.get("status"),
        }]

    if event_type == "patch_apply_end":
        return [{
            "type": "item",
            "itemType": "file_change",
            "changes": _extract_patch_changes(event.get("changes")),
            "status": event.get("status") or ("completed" if event.get("success") else "failed"),
        }]

    if event_type in {"mcp_tool_call_end", "mcp_tool_call"}:
        return [{
            "type": "item",
            "itemType": "mcp_tool_call",
            "server": event.get("server"),
            "tool": event.get("tool"),
            "arguments": event.get("arguments"),
            "result": event.get("result"),
            "error": event.get("error"),
            "status": event.get("status"),
        }]

    if event_type == "task_complete":
        transformed: list[dict[str, Any]] = []
        final_message = event.get("last_agent_message")
        if (
            isinstance(final_message, str)
            and final_message.strip()
            and not state.get("saw_final_agent_message")
        ):
            transformed.append({
                "type": "item",
                "itemType": "agent_message",
                "message": {"role": "assistant", "content": final_message},
            })
            state["saw_final_agent_message"] = True

        transformed.append({"type": "turn_complete"})
        return transformed

    if event_type == "turn_aborted":
        reason = event.get("reason") or event.get("message") or "Turn aborted"
        return [{"type": "turn_failed", "error": {"message": reason}}]

    if event_type == "error":
        return [{"type": "error", "message": event.get("message") or "Codex error"}]

    return []


async def _query_codex_via_mcp(command: str, options: dict[str, Any], ws) -> None:
    """Run a single Codex turn via `codex mcp-server`."""
    if not (mcp_types and CodexClientSession and StdioServerParameters and stdio_client):
        raise CodexMcpBootstrapError("Python MCP client is not available")

    requested_session_id = options.get("sessionId")
    current_session_id = requested_session_id or f"codex-{int(time.time() * 1000)}"
    cwd = options.get("cwd") or options.get("projectPath") or os.getcwd()
    model = options.get("model")
    permission_mode = options.get("permissionMode", "default")
    reasoning_effort = options.get("reasoningEffort")
    execution_policy = _build_codex_execution_policy(permission_mode, reasoning_effort)

    abort_event = asyncio.Event()
    _add_active_session(
        current_session_id,
        abort_event=abort_event,
        provider="mcp",
        task=asyncio.current_task(),
    )

    state: dict[str, Any] = {
        "requested_session_id": requested_session_id,
        "current_session_id": current_session_id,
        "actual_session_id": requested_session_id,
        "session_announced": bool(requested_session_id),
        "events_seen": 0,
        "saw_final_agent_message": False,
    }

    async def _message_handler(
        message: Any,
    ) -> None:
        if isinstance(message, Exception):
            raise message

        root = getattr(message, "root", None)
        if not isinstance(root, mcp_types.JSONRPCNotification):
            return
        if root.method != "codex/event":
            return

        params = root.params if isinstance(root.params, dict) else {}
        event = params.get("msg")
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if not isinstance(event, dict):
            return

        state["events_seen"] += 1
        _update_codex_session_id(state, _extract_thread_id(event, meta), ws)

        for transformed in _transform_codex_mcp_event(event, state):
            ws.send({
                "type": "codex-response",
                "data": transformed,
                "sessionId": state["current_session_id"],
            })

    async def _elicitation_handler(context: Any, params: Any) -> Any:
        request_id = _create_request_id()
        context_payload = {
            "message": getattr(params, "message", None),
            "codexCallId": getattr(params, "codex_call_id", None),
            "threadId": getattr(params, "threadId", None),
        }
        input_payload = {
            "command": getattr(params, "codex_command", None),
            "cwd": getattr(params, "codex_cwd", None),
            "parsedCommand": getattr(params, "codex_parsed_cmd", None),
        }

        session_for_request = state.get("actual_session_id") or requested_session_id or current_session_id

        ws.send({
            "type": "codex-permission-request",
            "requestId": request_id,
            "toolName": "CodexBash",
            "input": input_payload,
            "context": context_payload,
            "sessionId": session_for_request,
        })

        decision = await wait_for_codex_approval(
            request_id,
            timeout=CODEX_TOOL_APPROVAL_TIMEOUT,
            signal_event=abort_event,
            metadata={
                "_sessionId": session_for_request,
                "_toolName": "CodexBash",
                "_input": input_payload,
                "_context": context_payload,
                "_receivedAt": time.time(),
            },
            on_cancel=lambda reason: ws.send({
                "type": "codex-permission-cancelled",
                "requestId": request_id,
                "reason": reason,
                "sessionId": session_for_request,
            }),
        )

        if not decision:
            return {"decision": "denied"}
        if decision.get("cancelled"):
            return {"decision": "abort"}
        if decision.get("allow"):
            return {"decision": "approved"}

        denial = {"decision": "denied"}
        message = decision.get("message")
        if isinstance(message, str) and message:
            denial["reason"] = message
        return denial

    try:
        mcp_subcommand = _get_codex_mcp_subcommand()
        server = StdioServerParameters(
            command="codex",
            args=[mcp_subcommand],
            env={**os.environ, "FORCE_COLOR": "0"},
            cwd=cwd,
        )

        async with stdio_client(server) as (read_stream, write_stream):
            async with CodexClientSession(
                read_stream,
                write_stream,
                message_handler=_message_handler,
                elicitation_callback=_elicitation_handler,
            ) as session:
                await session.initialize()

                if requested_session_id:
                    tool_name = "codex-reply"
                    tool_arguments = {
                        "prompt": command,
                        "threadId": requested_session_id,
                    }
                else:
                    tool_name = "codex"
                    tool_arguments = {
                        "prompt": command,
                        "cwd": cwd,
                        "approval-policy": execution_policy["approval_policy"],
                        "sandbox": execution_policy["sandbox"],
                    }
                    if model:
                        tool_arguments["model"] = model
                    if execution_policy["config"]:
                        tool_arguments["config"] = execution_policy["config"]

                result = await session.call_tool(tool_name, tool_arguments)

        result_thread_id = _extract_result_thread_id(result)
        _update_codex_session_id(state, result_thread_id, ws)

        if getattr(result, "isError", False):
            error_text = _extract_result_text(result) or "Codex MCP call failed"
            raise RuntimeError(error_text)

        if not state.get("session_announced"):
            _announce_session_if_needed(state, ws)

        # If Codex returned only the structured response and never emitted a
        # final agent_message event, synthesize one so the frontend still shows
        # the answer.
        result_text = _extract_result_text(result)
        if result_text and not state.get("saw_final_agent_message"):
            ws.send({
                "type": "codex-response",
                "data": {
                    "type": "item",
                    "itemType": "agent_message",
                    "message": {"role": "assistant", "content": result_text},
                },
                "sessionId": state["current_session_id"],
            })
            state["saw_final_agent_message"] = True

        ws.send({
            "type": "codex-complete",
            "sessionId": state["current_session_id"],
            "actualSessionId": state.get("actual_session_id"),
            "exitCode": 0,
        })
    except asyncio.CancelledError:
        session = active_codex_sessions.get(state["current_session_id"])
        was_aborted = bool(session and session.get("status") == "aborted")
        if was_aborted:
            return
        raise
    except FileNotFoundError as exc:
        if state["events_seen"] == 0:
            raise CodexMcpBootstrapError(str(exc)) from exc

        ws.send({
            "type": "codex-error",
            "error": str(exc),
            "sessionId": state["current_session_id"],
            "actualSessionId": state.get("actual_session_id"),
        })
    except Exception as exc:
        session = active_codex_sessions.get(state["current_session_id"])
        was_aborted = bool(session and session.get("status") == "aborted")
        if was_aborted:
            return

        if state["events_seen"] == 0:
            raise CodexMcpBootstrapError(str(exc)) from exc

        print(f"[Codex MCP] Error: {exc}")
        ws.send({
            "type": "codex-error",
            "error": str(exc),
            "sessionId": state["current_session_id"],
            "actualSessionId": state.get("actual_session_id"),
        })
    finally:
        _mark_active_session_completed(state["current_session_id"])


# ---------------------------------------------------------------------------
# Exec fallback (keeps the previously repaired path available)
# ---------------------------------------------------------------------------

def _build_codex_exec_command(command: str, options: dict[str, Any]) -> list[str]:
    session_id = options.get("sessionId")
    model = options.get("model")
    permission_mode = options.get("permissionMode", "default")
    reasoning_effort = options.get("reasoningEffort")
    execution_policy = _build_codex_execution_policy(permission_mode, reasoning_effort)

    cmd_parts = ["codex", "exec"]
    if session_id:
        cmd_parts.append("resume")

    cmd_parts.extend(["--json", "--skip-git-repo-check"])

    if model:
        cmd_parts.extend(["--model", model])

    cmd_parts.extend(["-c", f"approval_policy={json.dumps(execution_policy['approval_policy'])}"])
    cmd_parts.extend(["-c", f"sandbox_mode={json.dumps(execution_policy['sandbox'])}"])

    config = execution_policy.get("config") or {}
    if config.get("model_reasoning_effort"):
        cmd_parts.extend(["-c", f"model_reasoning_effort={json.dumps(config['model_reasoning_effort'])}"])

    if session_id:
        cmd_parts.append(session_id)

    if command:
        cmd_parts.append(command)

    return cmd_parts


def _transform_codex_exec_event(event: dict[str, Any]) -> dict[str, Any]:
    """Transform `codex exec --json` events to the frontend format."""
    event_type = event.get("type", "")

    if event_type in ("item.started", "item.updated", "item.completed"):
        item = event.get("item", {})
        item_type = item.get("type", "")

        if item_type == "agent_message":
            return {
                "type": "item",
                "itemType": "agent_message",
                "message": {"role": "assistant", "content": item.get("text", "")},
            }
        if item_type == "reasoning":
            return {
                "type": "item",
                "itemType": "reasoning",
                "message": {"role": "assistant", "content": item.get("text", ""), "isReasoning": True},
            }
        if item_type == "command_execution":
            return {
                "type": "item",
                "itemType": "command_execution",
                "command": _format_command(item.get("command")),
                "output": item.get("aggregated_output"),
                "exitCode": item.get("exit_code"),
                "status": item.get("status"),
            }
        if item_type == "file_change":
            return {
                "type": "item",
                "itemType": "file_change",
                "changes": item.get("changes"),
                "status": item.get("status"),
            }
        if item_type == "mcp_tool_call":
            return {
                "type": "item",
                "itemType": "mcp_tool_call",
                "server": item.get("server"),
                "tool": item.get("tool"),
                "arguments": item.get("arguments"),
                "result": item.get("result"),
                "error": item.get("error"),
                "status": item.get("status"),
            }
        if item_type == "error":
            return {
                "type": "item",
                "itemType": "error",
                "message": {"role": "error", "content": item.get("message", "")},
            }
        return {"type": "item", "itemType": item_type, "item": item}

    if event_type == "turn.started":
        return {"type": "turn_started"}
    if event_type == "turn.completed":
        return {"type": "turn_complete", "usage": event.get("usage")}
    if event_type == "turn.failed":
        return {"type": "turn_failed", "error": event.get("error")}
    if event_type == "thread.started":
        return {"type": "thread_started", "threadId": event.get("thread_id") or event.get("id")}
    if event_type == "error":
        return {"type": "error", "message": event.get("message")}
    return {"type": event_type, "data": event}


async def _query_codex_via_exec(command: str, options: dict[str, Any], ws) -> None:
    """Run a Codex turn via `codex exec --json`."""
    session_id = options.get("sessionId")
    current_session_id = session_id or f"codex-{int(time.time() * 1000)}"
    actual_session_id = session_id
    cwd = options.get("cwd") or options.get("projectPath") or os.getcwd()
    abort_event = asyncio.Event()
    session_announced = False

    _add_active_session(
        current_session_id,
        abort_event=abort_event,
        provider="exec",
        task=asyncio.current_task(),
    )

    try:
        cmd_parts = _build_codex_exec_command(command, options)
        print(f"[Codex Exec] Running: {' '.join(cmd_parts)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, "FORCE_COLOR": "0"},
        )
        _set_active_session_process(current_session_id, proc)

        while True:
            if abort_event.is_set():
                proc.kill()
                break

            line = await asyncio.wait_for(proc.stdout.readline(), timeout=300)
            if not line:
                break

            text = line.decode("utf-8", errors="replace")
            try:
                event_data = json.loads(text.strip())
                transformed = _transform_codex_exec_event(event_data)

                real_thread_id = transformed.get("threadId")
                if real_thread_id:
                    actual_session_id = real_thread_id
                    _move_active_session(current_session_id, real_thread_id)
                    current_session_id = real_thread_id

                    if not session_announced:
                        ws.send({
                            "type": "session-created",
                            "sessionId": current_session_id,
                            "provider": "codex",
                        })
                        session_announced = True

                ws.send({
                    "type": "codex-response",
                    "data": transformed,
                    "sessionId": current_session_id,
                })
            except (json.JSONDecodeError, ValueError):
                if text.strip():
                    print(f"[Codex Exec] Non-JSON stdout: {text.rstrip()}")

        await proc.wait()
        exit_code = proc.returncode

        stderr_data = await proc.stderr.read()
        stderr_text = ""
        if stderr_data:
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            if stderr_text:
                print(f"[Codex Exec] stderr: {stderr_text}")

        if exit_code and exit_code != 0:
            ws.send({
                "type": "codex-error",
                "error": stderr_text or f"Codex exited with status {exit_code}",
                "sessionId": current_session_id,
                "actualSessionId": actual_session_id,
            })
            return

        if not session_announced:
            ws.send({
                "type": "session-created",
                "sessionId": current_session_id,
                "provider": "codex",
            })

        ws.send({
            "type": "codex-complete",
            "sessionId": current_session_id,
            "actualSessionId": actual_session_id,
            "exitCode": exit_code or 0,
        })
    except asyncio.TimeoutError:
        ws.send({
            "type": "codex-error",
            "error": "Codex query timed out",
            "sessionId": current_session_id,
            "actualSessionId": actual_session_id,
        })
    except FileNotFoundError:
        ws.send({
            "type": "codex-error",
            "error": "Codex CLI not found. Install with: npm install -g @openai/codex",
            "sessionId": current_session_id,
            "actualSessionId": actual_session_id,
        })
    except Exception as exc:
        session = active_codex_sessions.get(current_session_id)
        was_aborted = bool(session and session.get("status") == "aborted")
        if not was_aborted:
            print(f"[Codex Exec] Error: {exc}")
            ws.send({
                "type": "codex-error",
                "error": str(exc),
                "sessionId": current_session_id,
                "actualSessionId": actual_session_id,
            })
    finally:
        _mark_active_session_completed(current_session_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def query_codex(command: str, options: dict[str, Any], ws) -> None:
    """Execute a Codex query with MCP primary path and exec fallback."""
    try:
        await _query_codex_via_mcp(command, options, ws)
        return
    except CodexMcpBootstrapError as exc:
        print(f"[Codex] MCP bootstrap failed, falling back to exec: {exc}")

    await _query_codex_via_exec(command, options, ws)


def abort_codex_session(session_id: str) -> bool:
    """Abort an active Codex session."""
    session = active_codex_sessions.get(session_id)
    if not session:
        return False

    session["status"] = "aborted"
    session["abort_event"].set()

    task = session.get("task")
    if session.get("provider") == "mcp" and isinstance(task, asyncio.Task) and not task.done():
        task.cancel()

    proc = session.get("proc")
    if proc and getattr(proc, "returncode", None) is None:
        try:
            proc.kill()
        except Exception:
            pass
    return True


def is_codex_session_active(session_id: str) -> bool:
    session = active_codex_sessions.get(session_id)
    return bool(session and session.get("status") == "running")


def get_active_codex_sessions() -> list[str]:
    return [sid for sid, session in active_codex_sessions.items() if session.get("status") == "running"]


# ---------------------------------------------------------------------------
# Periodic cleanup of completed sessions
# ---------------------------------------------------------------------------

_cleanup_task = None


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(300)
        now = time.time()
        max_age = 30 * 60
        to_remove = [
            sid
            for sid, session in active_codex_sessions.items()
            if session.get("status") != "running" and now - session["started_at"] > max_age
        ]
        for sid in to_remove:
            active_codex_sessions.pop(sid, None)


def start_cleanup_task() -> None:
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_task = asyncio.create_task(_cleanup_loop())
