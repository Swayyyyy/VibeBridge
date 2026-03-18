"""WebSocket server for accepting Node connections on /ws/node path.

Port of server/main/node-ws-server.js.
Handles registration, heartbeat, and message routing.
"""
import asyncio
import json
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect

from node_protocol import MESSAGE_TYPES, create_message, parse_message

REGISTER_TIMEOUT = 10  # seconds


class NodeWsServer:
    def __init__(self, registry, allowed_tokens: list[str] | None = None):
        self.registry = registry
        self.allowed_tokens = allowed_tokens or []
        self._message_listeners: dict[str, set[Callable]] = {}

    async def handle_connection(self, ws: WebSocket):
        """Handle a new WebSocket connection from a Node."""
        await ws.accept()
        registered = False
        node_id = None

        async def _send(msg: dict):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

        try:
            # Registration timeout
            register_event = asyncio.Event()

            async def _timeout():
                try:
                    await asyncio.wait_for(register_event.wait(), REGISTER_TIMEOUT)
                except asyncio.TimeoutError:
                    if not registered:
                        await ws.close(4001, "Registration timeout")

            timeout_task = asyncio.create_task(_timeout())

            while True:
                raw = await ws.receive_text()
                try:
                    msg = parse_message(raw)
                except Exception as e:
                    await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": str(e)}))
                    continue

                if not registered:
                    if msg["type"] != MESSAGE_TYPES["REGISTER"]:
                        await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Must register first"}))
                        continue

                    payload = msg.get("payload", {})
                    token = payload.get("token", "")

                    if not self._validate_token(token):
                        await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Invalid token"}))
                        await ws.close(4003, "Invalid token")
                        break

                    node_id = msg.get("nodeId")
                    if not node_id:
                        await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Missing nodeId"}))
                        await ws.close(4002, "Missing nodeId")
                        break

                    self.registry.register(node_id, ws, {
                        "displayName": payload.get("nodeName") or node_id,
                        "version": payload.get("version"),
                        "capabilities": payload.get("capabilities"),
                        "labels": payload.get("labels"),
                        "port": payload.get("advertisePort") or payload.get("port"),
                        "advertiseHost": payload.get("advertiseHost"),
                        "advertisePort": payload.get("advertisePort"),
                        "host": payload.get("advertiseHost") or (ws.client.host if ws.client else None),
                        "explicitPort": payload.get("advertisePort") or payload.get("port"),
                    })

                    registered = True
                    register_event.set()

                    await _send(create_message(MESSAGE_TYPES["REGISTER_ACK"], node_id, {"message": "Registered successfully"}))
                    print(f"[Main] Node registered: {node_id}")
                    continue

                # Registered — handle messages
                if msg["type"] == MESSAGE_TYPES["HEARTBEAT"]:
                    self.registry.update_heartbeat(node_id)
                elif msg["type"] in (MESSAGE_TYPES["RESPONSE"], MESSAGE_TYPES["EVENT"]):
                    self._notify_listeners(node_id, msg)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[Main] Node WS error{f' ({node_id})' if node_id else ''}: {e}")
        finally:
            if node_id:
                current = self.registry.get_node(node_id)
                if current and current.get("ws") is ws:
                    print(f"[Main] Node disconnected: {node_id}")
                    self.registry.unregister(node_id)
                    self._notify_listeners(node_id, {
                        "type": "node_disconnected",
                        "nodeId": node_id,
                    })

    def register_outbound(self, node_id: str, ws, info: dict):
        """Register an outbound (Main-initiated) WebSocket connection."""
        self.registry.register(node_id, ws, info)
        print(f"[Main] Node registered (outbound): {node_id}")

    async def send_to_node(self, node_id: str, message: dict) -> bool:
        """Send a message to a specific node."""
        record = self.registry.get_node(node_id)
        if not record or not record.get("ws"):
            return False
        try:
            await self._send_message(record["ws"], message)
            return True
        except Exception:
            return False

    async def send_request(self, node_id: str, message: dict, timeout_ms: int = 30000) -> dict:
        """Send a request and wait for response."""
        request_id = message.get("requestId")
        if not request_id:
            raise ValueError("Request must have requestId")

        future: asyncio.Future = asyncio.get_event_loop().create_future()

        def listener(msg):
            if msg.get("requestId") == request_id and msg.get("type") == MESSAGE_TYPES["RESPONSE"]:
                if not future.done():
                    future.set_result(msg)

        self.add_message_listener(node_id, listener)
        try:
            sent = await self.send_to_node(node_id, message)
            if not sent:
                raise ConnectionError(f"Node {node_id} is not connected")
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Request {request_id} timed out")
        finally:
            self.remove_message_listener(node_id, listener)

    def add_message_listener(self, node_id: str, listener: Callable):
        if node_id not in self._message_listeners:
            self._message_listeners[node_id] = set()
        self._message_listeners[node_id].add(listener)

    def remove_message_listener(self, node_id: str, listener: Callable):
        listeners = self._message_listeners.get(node_id)
        if listeners:
            listeners.discard(listener)

    def _notify_listeners(self, node_id: str, msg: dict):
        listeners = self._message_listeners.get(node_id)
        if listeners:
            for listener in list(listeners):
                try:
                    listener(msg)
                except Exception:
                    pass

    def _validate_token(self, token: str) -> bool:
        if not self.allowed_tokens:
            return True
        return token in self.allowed_tokens

    async def _send_message(self, ws, message: dict):
        """Send a JSON message to either an inbound FastAPI WS or outbound websockets client."""
        if hasattr(ws, "send_json"):
            await ws.send_json(message)
            return

        data = json.dumps(message)
        if hasattr(ws, "send_text"):
            await ws.send_text(data)
            return

        await ws.send(data)
