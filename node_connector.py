"""Node Connector — connects Node Server to Main Server via WebSocket.

Port of server/node-connector.js.
Activated when MAIN_SERVER_URL is configured.
"""
import asyncio
import json
import platform
import random

import websockets
from config import (
    PORT,
    NODE_ADVERTISE_HOST,
    NODE_ADVERTISE_PORT,
    MAIN_SERVER_URL,
    NODE_ID,
    NODE_NAME,
    NODE_REGISTER_TOKEN,
    NODE_LABELS_LIST,
    NODE_CAPABILITIES_LIST,
)

from node_protocol import (
    MESSAGE_TYPES,
    NODE_ACTIONS,
    create_message,
    create_response,
    create_event,
    parse_message,
)

HEARTBEAT_INTERVAL = 15  # seconds
MAX_RECONNECT_DELAY = 30.0
INITIAL_RECONNECT_DELAY = 1.0


class NodeConnector:
    def __init__(self, config: dict):
        self.main_server_url = config["mainServerUrl"]
        self.node_id = config.get("nodeId") or f"node-{platform.node()}"
        self.node_name = config.get("nodeName") or platform.node()
        self.token = config.get("token", "")
        self.labels = config.get("labels", [])
        self.capabilities = config.get("capabilities", ["claude", "codex"])

        self.ws = None
        self.is_connected = False
        self.should_reconnect = True
        self.reconnect_delay = INITIAL_RECONNECT_DELAY
        self._heartbeat_task = None
        self._handlers = None

    def set_handlers(self, handlers: dict):
        """Set local handler functions for processing requests."""
        self._handlers = handlers

    async def connect(self):
        """Connect to Main Server."""
        print(f"[Node] Connecting to Main Server: {self.main_server_url}")

        try:
            self.ws = await websockets.connect(self.main_server_url)
            self.reconnect_delay = INITIAL_RECONNECT_DELAY

            # Send REGISTER
            reg_msg = create_message(MESSAGE_TYPES["REGISTER"], self.node_id, {
                "token": self.token,
                "nodeName": self.node_name,
                "version": "0.1.0",
                "capabilities": self.capabilities,
                "labels": self.labels,
                "port": PORT,
                "advertiseHost": NODE_ADVERTISE_HOST,
                "advertisePort": NODE_ADVERTISE_PORT,
            })
            await self.ws.send(json.dumps(reg_msg))

            async for raw in self.ws:
                try:
                    msg = parse_message(raw)
                except Exception as e:
                    print(f"[Node] Invalid message from Main: {e}")
                    continue

                if msg["type"] == MESSAGE_TYPES["REGISTER_ACK"]:
                    print(f"[Node] Registered with Main as \"{self.node_id}\"")
                    self.is_connected = True
                    self._start_heartbeat()
                elif msg["type"] == MESSAGE_TYPES["ERROR"]:
                    print(f"[Node] Error from Main: {msg.get('payload', {}).get('error')}")
                elif msg["type"] == MESSAGE_TYPES["REQUEST"]:
                    asyncio.create_task(self._handle_request(msg))

        except Exception as e:
            print(f"[Node] WS error: {e}")
        finally:
            self.is_connected = False
            self._stop_heartbeat()
            if self.should_reconnect:
                await self._schedule_reconnect()

    def disconnect(self):
        self.should_reconnect = False
        self._stop_heartbeat()
        if self.ws:
            asyncio.create_task(self.ws.close())

    def _start_heartbeat(self):
        self._stop_heartbeat()

        async def _loop():
            while self.ws:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                try:
                    msg = create_message(MESSAGE_TYPES["HEARTBEAT"], self.node_id, {})
                    await self.ws.send(json.dumps(msg))
                except Exception:
                    break

        self._heartbeat_task = asyncio.create_task(_loop())

    def _stop_heartbeat(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _schedule_reconnect(self):
        jitter = random.random()
        delay = min(self.reconnect_delay + jitter, MAX_RECONNECT_DELAY)
        print(f"[Node] Reconnecting in {delay:.0f}s...")
        await asyncio.sleep(delay)
        if self.should_reconnect:
            self.reconnect_delay = min(self.reconnect_delay * 2, MAX_RECONNECT_DELAY)
            asyncio.create_task(self.connect())

    async def _send(self, msg: dict):
        if self.ws:
            try:
                await self.ws.send(json.dumps(msg))
            except Exception:
                pass

    def _create_proxy_writer(self, request_id: str):
        """Create a writer that forwards messages to Main as events."""
        connector = self

        class ProxyWriter:
            def __init__(self):
                self.session_id = None
                self.is_websocket_writer = True

            def send(self, data):
                msg = create_event(connector.node_id, request_id, "chat", data)
                asyncio.create_task(connector._send(msg))

            def update_websocket(self, new_ws):
                pass

            def set_session_id(self, sid):
                self.session_id = sid

        return ProxyWriter()

    async def _handle_request(self, msg: dict):
        payload = msg.get("payload", {})
        action = payload.get("action")
        params = payload.get("params", {})
        request_id = msg.get("requestId")
        h = self._handlers

        if not h:
            await self._send(create_response(self.node_id, request_id, None, "Node handlers not initialized"))
            return

        try:
            if action == NODE_ACTIONS["PROJECT_LIST"]:
                data = await h["get_projects"]()
                await self._send(create_response(self.node_id, request_id, data))

            elif action == NODE_ACTIONS["PROJECT_SESSIONS"]:
                limit = params.get("limit")
                offset = params.get("offset")
                data = await h["get_sessions"](
                    params.get("projectName"),
                    5 if limit is None else limit,
                    0 if offset is None else offset,
                )
                await self._send(create_response(self.node_id, request_id, data))

            elif action == NODE_ACTIONS["PROJECT_SESSION_MESSAGES"]:
                offset = params.get("offset")
                provider = params.get("provider")
                if provider == "codex" and "get_codex_session_messages" in h:
                    data = await h["get_codex_session_messages"](
                        params.get("sessionId"), params.get("limit"), 0 if offset is None else offset
                    )
                else:
                    data = await h["get_session_messages"](
                        params.get("projectName"), params.get("sessionId"),
                        params.get("limit"), 0 if offset is None else offset,
                    )
                await self._send(create_response(self.node_id, request_id, data))

            elif action == NODE_ACTIONS["CHAT_SEND"]:
                original_type = params.get("originalType") or params.get("type")
                writer = self._create_proxy_writer(request_id)
                try:
                    if original_type == "claude-command":
                        await h["query_claude"](params.get("command", ""), params.get("options", {}), writer)
                    elif original_type == "codex-command":
                        await h["query_codex"](params.get("command", ""), params.get("options", {}), writer)
                    else:
                        raise ValueError(f"Unknown chat type: {original_type}")
                except Exception as e:
                    await self._send(create_event(self.node_id, request_id, "error", {"type": "error", "error": str(e)}))
                await self._send(create_response(self.node_id, request_id, {"completed": True}))

            elif action == NODE_ACTIONS["CHAT_ABORT"]:
                provider = params.get("provider", "claude")
                sid = params.get("sessionId")
                if provider == "codex":
                    success = h["abort_codex"](sid)
                else:
                    success = await h["abort_claude"](sid)
                await self._send(create_response(self.node_id, request_id, {"success": success, "sessionId": sid}))

            elif action == NODE_ACTIONS["NODE_PING"]:
                await self._send(create_response(self.node_id, request_id, {
                    "pong": True, "nodeId": self.node_id, "timestamp": int(asyncio.get_event_loop().time() * 1000),
                }))

            elif action == NODE_ACTIONS["NODE_GET_CAPABILITIES"]:
                await self._send(create_response(self.node_id, request_id, {
                    "capabilities": self.capabilities, "labels": self.labels,
                }))

            elif action == "permission.response":
                if params.get("requestId"):
                    decision = {
                        "allow": bool(params.get("allow")),
                        "updatedInput": params.get("updatedInput"),
                        "message": params.get("message"),
                        "rememberEntry": params.get("rememberEntry"),
                    }
                    if params.get("provider") == "codex":
                        if "resolve_codex_approval" in h:
                            h["resolve_codex_approval"](params["requestId"], decision)
                    elif "resolve_tool_approval" in h:
                        h["resolve_tool_approval"](params["requestId"], decision)
                await self._send(create_response(self.node_id, request_id, {"success": True}))

            elif action == "session.reconnect":
                if "reconnect_writer" in h and params.get("sessionId"):
                    writer = self._create_proxy_writer(request_id)
                    h["reconnect_writer"](params["sessionId"], writer)
                await self._send(create_response(self.node_id, request_id, {"success": True}))

            elif action == "session.checkActive":
                sessions = {
                    "claude": h.get("get_active_claude", lambda: [])(),
                    "codex": h.get("get_active_codex", lambda: [])(),
                }
                await self._send(create_response(self.node_id, request_id, {"type": "active-sessions", "sessions": sessions}))

            elif action == "check-session-status":
                provider = params.get("provider", "claude")
                sid = params.get("sessionId")
                if provider == "codex":
                    is_active = h.get("is_codex_active", lambda _sid: False)(sid)
                else:
                    is_active = h.get("is_claude_active", lambda _sid: False)(sid)
                await self._send(create_response(self.node_id, request_id, {
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
                    if sid and h.get("is_codex_active", lambda _sid: False)(sid):
                        pending = h.get("get_pending_codex_approvals", lambda _sid: [])(sid)
                elif sid and h.get("is_claude_active", lambda _sid: False)(sid):
                    pending = h.get("get_pending_approvals", lambda _sid: [])(sid)
                await self._send(create_response(self.node_id, request_id, {
                    "type": "pending-permissions-response",
                    "sessionId": sid,
                    "data": pending,
                }))

            elif action == "get-active-sessions":
                sessions = {
                    "claude": h.get("get_active_claude", lambda: [])(),
                    "codex": h.get("get_active_codex", lambda: [])(),
                }
                await self._send(create_response(self.node_id, request_id, {
                    "type": "active-sessions",
                    "sessions": sessions,
                }))

            else:
                await self._send(create_response(self.node_id, request_id, None, f"Unknown action: {action}"))

        except Exception as e:
            print(f"[Node] Error handling {action}: {e}")
            await self._send(create_response(self.node_id, request_id, None, str(e)))


def start_node_connector(handlers: dict) -> NodeConnector | None:
    """Start node connector if MAIN_SERVER_URL is configured."""
    url = MAIN_SERVER_URL
    if not url:
        return None

    connector = NodeConnector({
        "mainServerUrl": url,
        "nodeId": NODE_ID,
        "nodeName": NODE_NAME,
        "token": NODE_REGISTER_TOKEN,
        "labels": list(NODE_LABELS_LIST),
        "capabilities": list(NODE_CAPABILITIES_LIST),
    })
    connector.set_handlers(handlers)
    asyncio.create_task(connector.connect())
    return connector
