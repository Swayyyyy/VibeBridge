"""Handle reverse WebSocket connection from Main Server (/ws/main).

Port of handleMainConnection from server/index.js.
Main connects to Node, sends HELLO with token, Node replies with REGISTER_INFO.
"""
import asyncio
import json
import platform

from fastapi import WebSocket, WebSocketDisconnect
from config import (
    NODE_ID,
    NODE_NAME,
    NODE_REGISTER_TOKEN,
    NODE_CAPABILITIES_LIST,
    NODE_LABELS_LIST,
    PORT,
    NODE_ADVERTISE_HOST,
    NODE_ADVERTISE_PORT,
)

from node_protocol import MESSAGE_TYPES, create_message, create_response, create_event, parse_message
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
    resolve_codex_approval,
    get_pending_codex_approvals_for_session,
)
from projects import get_projects, get_sessions, get_session_messages, get_codex_session_messages

AUTH_TIMEOUT = 10  # seconds


async def handle_main_connection(ws: WebSocket):
    """Handle /ws/main — reverse connection from Main Server."""
    await ws.accept()
    print("[Node] Main Server connecting via /ws/main")

    authenticated = False
    node_id = NODE_ID or f"node-{platform.node()}"
    node_name = NODE_NAME or platform.node()
    node_token = NODE_REGISTER_TOKEN
    capabilities = list(NODE_CAPABILITIES_LIST)

    async def _send(data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    # Auth timeout
    auth_event = asyncio.Event()

    async def _auth_timeout():
        try:
            await asyncio.wait_for(auth_event.wait(), AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            if not authenticated:
                print("[Node] /ws/main auth timeout")
                await ws.close(4001, "Authentication timeout")

    timeout_task = asyncio.create_task(_auth_timeout())

    # Heartbeat task
    heartbeat_task = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = parse_message(raw)
            except Exception:
                continue

            if not authenticated:
                if msg["type"] != MESSAGE_TYPES["HELLO"]:
                    await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Expected HELLO"}))
                    continue

                token = msg.get("payload", {}).get("token", "")
                if node_token and token != node_token:
                    await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Invalid token"}))
                    await ws.close(4003, "Invalid token")
                    break

                authenticated = True
                auth_event.set()

                # Reply with node info
                info = create_message(MESSAGE_TYPES["REGISTER_INFO"], node_id, {
                    "nodeName": node_name,
                    "version": "0.1.0",
                    "capabilities": capabilities,
                    "labels": list(NODE_LABELS_LIST),
                    "port": PORT,
                    "advertiseHost": NODE_ADVERTISE_HOST,
                    "advertisePort": NODE_ADVERTISE_PORT,
                })
                await _send(info)
                print(f"[Node] Authenticated Main connection, sent REGISTER_INFO as \"{node_id}\"")

                # Start heartbeat
                async def _heartbeat():
                    while True:
                        await asyncio.sleep(15)
                        await _send(create_message(MESSAGE_TYPES["HEARTBEAT"], node_id, {}))

                heartbeat_task = asyncio.create_task(_heartbeat())
                continue

            # Authenticated — handle requests
            if msg["type"] == MESSAGE_TYPES["REQUEST"]:
                asyncio.create_task(_handle_main_request(ws, node_id, msg))

    except WebSocketDisconnect:
        print("[Node] Main Server disconnected from /ws/main")
    except Exception as e:
        print(f"[Node] /ws/main error: {e}")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        timeout_task.cancel()


async def _handle_main_request(ws: WebSocket, node_id: str, msg: dict):
    """Handle REQUEST messages from Main Server."""
    payload = msg.get("payload", {})
    action = payload.get("action")
    params = payload.get("params", {})
    request_id = msg.get("requestId")

    async def _send(data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    class ProxyWriter:
        def __init__(self):
            self.session_id = None
            self.is_websocket_writer = True

        def send(self, data):
            asyncio.create_task(_send(create_event(node_id, request_id, "chat", data)))

        def update_websocket(self, _):
            pass

        def set_session_id(self, sid):
            self.session_id = sid

    try:
        if action == "chat.send":
            original_type = params.get("originalType") or params.get("type")
            writer = ProxyWriter()
            try:
                if original_type == "claude-command":
                    await query_claude_sdk(params.get("command", ""), params.get("options", {}), writer)
                elif original_type == "codex-command":
                    await query_codex(params.get("command", ""), params.get("options", {}), writer)
                else:
                    raise ValueError(f"Unknown chat type: {original_type}")
            except Exception as e:
                await _send(create_event(node_id, request_id, "error", {"type": "error", "error": str(e)}))
            await _send(create_response(node_id, request_id, {"completed": True}))

        elif action == "project.list":
            projects = await get_projects()
            await _send(create_response(node_id, request_id, projects))

        elif action == "project.sessions":
            limit = params.get("limit")
            offset = params.get("offset")
            sessions = await get_sessions(
                params.get("projectName"),
                5 if limit is None else limit,
                0 if offset is None else offset,
            )
            await _send(create_response(node_id, request_id, sessions))

        elif action == "project.sessionMessages":
            offset = params.get("offset")
            provider = params.get("provider")
            if provider == "codex":
                messages = await get_codex_session_messages(
                    params.get("sessionId"),
                    params.get("limit"),
                    0 if offset is None else offset,
                )
            else:
                messages = await get_session_messages(
                    params.get("projectName"),
                    params.get("sessionId"),
                    params.get("limit"),
                    0 if offset is None else offset,
                )
            await _send(create_response(node_id, request_id, messages))

        elif action == "chat.abort":
            provider = params.get("provider", "claude")
            sid = params.get("sessionId")
            if provider == "codex":
                success = abort_codex_session(sid)
            else:
                success = await abort_claude_session(sid)
            await _send(create_response(node_id, request_id, {"success": success, "sessionId": sid}))

        elif action == "node.ping":
            await _send(create_response(node_id, request_id, {"pong": True, "nodeId": node_id}))

        elif action == "node.getCapabilities":
            await _send(
                create_response(
                    node_id,
                    request_id,
                    {"capabilities": list(NODE_CAPABILITIES_LIST), "labels": list(NODE_LABELS_LIST)},
                )
            )

        elif action == "permission.response":
            if params.get("requestId"):
                decision = {
                    "allow": bool(params.get("allow")),
                    "updatedInput": params.get("updatedInput"),
                    "message": params.get("message"),
                    "rememberEntry": params.get("rememberEntry"),
                }
                if params.get("provider") == "codex":
                    resolve_codex_approval(params["requestId"], decision)
                else:
                    resolve_tool_approval(params["requestId"], decision)
            await _send(create_response(node_id, request_id, {"success": True}))

        elif action == "session.reconnect":
            if params.get("sessionId"):
                writer = ProxyWriter()
                reconnect_session_writer(params["sessionId"], writer)
            await _send(create_response(node_id, request_id, {"success": True}))

        elif action == "session.checkActive":
            sessions = {
                "claude": get_active_claude_sessions(),
                "codex": get_active_codex_sessions(),
            }
            await _send(create_response(node_id, request_id, {"type": "active-sessions", "sessions": sessions}))

        elif action == "check-session-status":
            provider = params.get("provider", "claude")
            sid = params.get("sessionId")
            if provider == "codex":
                is_active = is_codex_session_active(sid)
            else:
                is_active = is_claude_session_active(sid)
            await _send(create_response(node_id, request_id, {
                "type": "session-status",
                "sessionId": sid,
                "provider": provider,
                "isProcessing": is_active,
            }))

        elif action == "get-pending-permissions":
            sid = params.get("sessionId")
            provider = params.get("provider", "claude")
            pending = []
            if provider == "codex":
                if sid and is_codex_session_active(sid):
                    pending = get_pending_codex_approvals_for_session(sid)
            elif sid and is_claude_session_active(sid):
                pending = get_pending_approvals_for_session(sid)
            await _send(create_response(node_id, request_id, {
                "type": "pending-permissions-response",
                "sessionId": sid,
                "data": pending,
            }))

        elif action == "get-active-sessions":
            sessions = {
                "claude": get_active_claude_sessions(),
                "codex": get_active_codex_sessions(),
            }
            await _send(create_response(node_id, request_id, {
                "type": "active-sessions",
                "sessions": sessions,
            }))

        else:
            await _send(create_response(node_id, request_id, None, f"Unknown action: {action}"))

    except Exception as e:
        print(f"[Node] Error handling {action}: {e}")
        await _send(create_response(node_id, request_id, None, str(e)))
