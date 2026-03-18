"""WebSocket chat handler — port of handleChatConnection from server/index.js.

Handles /ws WebSocket connections for Claude and Codex chat sessions.
Message types: claude-command, codex-command, abort-session,
claude-permission-response, codex-permission-response,
check-session-status, get-pending-permissions,
get-active-sessions.
"""
import json
import traceback

from fastapi import WebSocket, WebSocketDisconnect

from providers.claude_sdk import (
    query_claude_sdk,
    abort_claude_session,
    is_claude_session_active,
    get_active_claude_sessions,
    resolve_tool_approval,
    get_pending_approvals_for_session,
    reconnect_session_writer,
)
from providers.codex_mcp import (
    query_codex,
    abort_codex_session,
    is_codex_session_active,
    get_active_codex_sessions,
    resolve_codex_approval,
    get_pending_codex_approvals_for_session,
)


# ---------------------------------------------------------------------------
# WebSocket writer — wraps FastAPI WebSocket for consistent interface
# ---------------------------------------------------------------------------

class WebSocketWriter:
    """Wrapper matching the interface used by providers (send(dict))."""

    def __init__(self, ws: WebSocket):
        self._ws = ws
        self.session_id: str | None = None
        self.is_websocket_writer = True

    def send(self, data: dict):
        """Send JSON data. Non-blocking fire-and-forget via task."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_send(data))
        except RuntimeError:
            pass

    async def _async_send(self, data: dict):
        try:
            await self._ws.send_json(data)
        except Exception:
            pass

    def update_websocket(self, new_ws: WebSocket):
        self._ws = new_ws

    def set_session_id(self, session_id: str):
        self.session_id = session_id

    def get_session_id(self) -> str | None:
        return self.session_id


# ---------------------------------------------------------------------------
# Connected clients set (for broadcast / project updates)
# ---------------------------------------------------------------------------

connected_clients: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------

async def handle_chat_connection(ws: WebSocket):
    """Handle a /ws chat WebSocket connection."""
    await ws.accept()
    connected_clients.add(ws)
    writer = WebSocketWriter(ws)
    print("[Chat] WebSocket connected")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            try:
                if msg_type == "claude-command":
                    print(f"[Chat] Claude command, session={'Resume' if data.get('options', {}).get('sessionId') else 'New'}")
                    await query_claude_sdk(
                        data.get("command", ""),
                        data.get("options", {}),
                        writer,
                    )

                elif msg_type == "codex-command":
                    print(f"[Chat] Codex command, session={'Resume' if data.get('options', {}).get('sessionId') else 'New'}")
                    await query_codex(
                        data.get("command", ""),
                        data.get("options", {}),
                        writer,
                    )

                elif msg_type == "abort-session":
                    sid = data.get("sessionId")
                    provider = data.get("provider", "claude")
                    print(f"[Chat] Abort {provider} session: {sid}")

                    if provider == "codex":
                        success = abort_codex_session(sid)
                    else:
                        success = await abort_claude_session(sid)

                    writer.send({
                        "type": "session-aborted",
                        "sessionId": sid,
                        "provider": provider,
                        "success": success,
                    })

                elif msg_type == "claude-permission-response":
                    req_id = data.get("requestId")
                    if req_id:
                        resolve_tool_approval(req_id, {
                            "allow": bool(data.get("allow")),
                            "updatedInput": data.get("updatedInput"),
                            "message": data.get("message"),
                            "rememberEntry": data.get("rememberEntry"),
                        })

                elif msg_type == "codex-permission-response":
                    req_id = data.get("requestId")
                    if req_id:
                        resolve_codex_approval(req_id, {
                            "allow": bool(data.get("allow")),
                            "updatedInput": data.get("updatedInput"),
                            "message": data.get("message"),
                            "rememberEntry": data.get("rememberEntry"),
                        })

                elif msg_type == "check-session-status":
                    provider = data.get("provider", "claude")
                    sid = data.get("sessionId")

                    if provider == "codex":
                        is_active = is_codex_session_active(sid)
                    else:
                        is_active = is_claude_session_active(sid)
                        if is_active:
                            reconnect_session_writer(sid, ws)

                    writer.send({
                        "type": "session-status",
                        "sessionId": sid,
                        "provider": provider,
                        "isProcessing": is_active,
                    })

                elif msg_type == "get-pending-permissions":
                    sid = data.get("sessionId")
                    provider = data.get("provider", "claude")
                    pending = []
                    if provider == "codex":
                        if sid and is_codex_session_active(sid):
                            pending = get_pending_codex_approvals_for_session(sid)
                    elif sid and is_claude_session_active(sid):
                        pending = get_pending_approvals_for_session(sid)
                    writer.send({
                        "type": "pending-permissions-response",
                        "sessionId": sid,
                        "data": pending,
                    })

                elif msg_type == "get-active-sessions":
                    sessions = {
                        "claude": get_active_claude_sessions(),
                        "codex": get_active_codex_sessions(),
                    }
                    writer.send({
                        "type": "active-sessions",
                        "sessions": sessions,
                    })

                else:
                    print(f"[Chat] Unknown message type: {msg_type}")

            except Exception as e:
                print(f"[Chat] Error handling {msg_type}: {e}")
                traceback.print_exc()
                writer.send({"type": "error", "error": str(e)})

    except WebSocketDisconnect:
        print("[Chat] WebSocket disconnected")
    except Exception as e:
        print(f"[Chat] Connection error: {e}")
    finally:
        connected_clients.discard(ws)
