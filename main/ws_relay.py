"""WebSocket relay between browser clients and Node servers.

Port of server/main/ws-relay.js.
Handles /ws path on the Main Server — browser <-> node message relay.
"""
import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from node_protocol import MESSAGE_TYPES, NODE_ACTIONS, create_request


class WsRelay:
    def __init__(self, registry, node_ws_server):
        self.registry = registry
        self.node_ws_server = node_ws_server
        self.browser_clients: set[WebSocket] = set()

    async def handle_connection(self, ws: WebSocket):
        """Handle a new browser WebSocket connection."""
        await ws.accept()
        self.browser_clients.add(ws)

        current_node_id = None
        listener = None

        def _setup_node_listener(node_id: str):
            nonlocal current_node_id, listener

            if listener and current_node_id:
                self.node_ws_server.remove_message_listener(current_node_id, listener)

            current_node_id = node_id

            def _on_msg(msg):
                msg_type = msg.get("type")
                if msg_type == MESSAGE_TYPES["EVENT"]:
                    browser_msg = self._node_event_to_browser(msg)
                elif msg_type == MESSAGE_TYPES["RESPONSE"]:
                    browser_msg = self._node_response_to_browser(msg)
                elif msg_type == "node_disconnected":
                    browser_msg = {"type": "node_status", "nodeId": msg.get("nodeId"), "status": "offline"}
                else:
                    return

                if browser_msg:
                    asyncio.create_task(self._send(ws, browser_msg))

            listener = _on_msg
            self.node_ws_server.add_message_listener(node_id, listener)

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                target_node = data.get("nodeId")
                if target_node and target_node != current_node_id:
                    _setup_node_listener(target_node)

                if not current_node_id:
                    await self._send(ws, {"type": "error", "error": "No node selected"})
                    continue

                await self._handle_browser_message(ws, current_node_id, data)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[WsRelay] Error: {e}")
        finally:
            self.browser_clients.discard(ws)
            if listener and current_node_id:
                self.node_ws_server.remove_message_listener(current_node_id, listener)

    async def _handle_browser_message(self, ws: WebSocket, node_id: str, data: dict):
        msg_type = data.get("type", "")

        chat_types = ["claude-command", "codex-command"]
        if msg_type in chat_types:
            message, request_id = create_request(node_id, NODE_ACTIONS["CHAT_SEND"], {
                **data, "originalType": msg_type,
            })
            await self.node_ws_server.send_to_node(node_id, message)

        elif msg_type == "abort-session":
            message, _ = create_request(node_id, NODE_ACTIONS["CHAT_ABORT"], data)
            await self.node_ws_server.send_to_node(node_id, message)

        elif msg_type in {"claude-permission-response", "codex-permission-response"}:
            message, _ = create_request(node_id, "permission.response", data)
            await self.node_ws_server.send_to_node(node_id, message)

        elif msg_type == "reconnect-session":
            message, _ = create_request(node_id, "session.reconnect", data)
            await self.node_ws_server.send_to_node(node_id, message)

        elif msg_type == "check-active-sessions":
            message, _ = create_request(node_id, "session.checkActive", data)
            await self.node_ws_server.send_to_node(node_id, message)

        else:
            message, _ = create_request(node_id, msg_type, data)
            await self.node_ws_server.send_to_node(node_id, message)

    def _node_event_to_browser(self, msg: dict) -> dict | None:
        payload = msg.get("payload", {})
        data = payload.get("data")
        if data:
            return data
        return {"type": payload.get("eventType", "event"), **payload}

    def _node_response_to_browser(self, msg: dict) -> dict | None:
        payload = msg.get("payload", {})
        if payload.get("error"):
            return {"type": "error", "error": payload["error"]}
        return payload.get("data")

    async def _send(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    async def broadcast(self, message: dict):
        for client in list(self.browser_clients):
            await self._send(client, message)
