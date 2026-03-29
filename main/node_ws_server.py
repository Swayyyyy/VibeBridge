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
    def __init__(self, registry, allowed_tokens: list[str] | None = None, token_resolver: Callable | None = None):
        self.registry = registry
        self.allowed_tokens = allowed_tokens or []
        self.token_resolver = token_resolver
        self._message_listeners: dict[str, set[Callable]] = {}
        self._outbound_connector = None

    def attach_outbound_connector(self, connector):
        self._outbound_connector = connector

    async def handle_connection(self, ws: WebSocket):
        """Handle a new WebSocket connection from a Node."""
        await ws.accept()
        registered = False
        node_id = None
        registry_key = None

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

                    owner = self._resolve_owner(token)
                    if owner is False:
                        await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Invalid token"}))
                        await ws.close(4003, "Invalid token")
                        break

                    requested_node_id = msg.get("nodeId")
                    if not requested_node_id:
                        await _send(create_message(MESSAGE_TYPES["ERROR"], None, {"error": "Missing nodeId"}))
                        await ws.close(4002, "Missing nodeId")
                        break

                    record = self.registry.register(requested_node_id, ws, {
                        "displayName": payload.get("nodeName") or requested_node_id,
                        "version": payload.get("version"),
                        "capabilities": payload.get("capabilities"),
                        "labels": payload.get("labels"),
                        "port": payload.get("advertisePort") or payload.get("port"),
                        "advertiseHost": payload.get("advertiseHost"),
                        "advertisePort": payload.get("advertisePort"),
                        "host": payload.get("advertiseHost") or (ws.client.host if ws.client else None),
                        "explicitPort": payload.get("advertisePort") or payload.get("port"),
                        "ownerUserId": owner.get("id") if isinstance(owner, dict) else None,
                        "ownerUsername": owner.get("username") if isinstance(owner, dict) else None,
                        "ownerRole": owner.get("role", "user") if isinstance(owner, dict) else "admin",
                    })
                    node_id = record["nodeId"]
                    registry_key = record["registryKey"]

                    registered = True
                    register_event.set()

                    await _send(create_message(MESSAGE_TYPES["REGISTER_ACK"], node_id, {"message": "Registered successfully"}))
                    print(f"[Main] Node registered: {node_id}")
                    continue

                # Registered — handle messages
                if msg["type"] == MESSAGE_TYPES["HEARTBEAT"]:
                    self.registry.update_heartbeat(registry_key)
                elif msg["type"] in (MESSAGE_TYPES["RESPONSE"], MESSAGE_TYPES["EVENT"]):
                    self._notify_listeners(registry_key, msg)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[Main] Node WS error{f' ({node_id})' if node_id else ''}: {e}")
        finally:
            if registry_key:
                current = self.registry.get_node(registry_key)
                if current and current.get("ws") is ws:
                    print(f"[Main] Node disconnected: {node_id}")
                    self.registry.unregister(registry_key)
                    self._notify_listeners(registry_key, {
                        "type": "node_disconnected",
                        "nodeId": node_id,
                    })

    def register_outbound(self, node_id: str, ws, info: dict):
        """Register an outbound (Main-initiated) WebSocket connection."""
        record = self.registry.register(node_id, ws, info)
        print(f"[Main] Node registered (outbound): {node_id}")
        return record

    async def send_to_node(self, registry_key: str, message: dict) -> bool:
        """Send a message to a specific node."""
        record = self.registry.get_node(registry_key)
        ws = record.get("ws") if record else None
        if not self.registry._is_ws_usable(ws):
            if self._outbound_connector:
                reconnected = await self._outbound_connector.ensure_connection(registry_key)
                if reconnected:
                    record = self.registry.get_node(registry_key)
                    ws = record.get("ws") if record else None
            if not self.registry._is_ws_usable(ws):
                return False
        try:
            await self._send_message(ws, message)
            return True
        except Exception:
            if self._outbound_connector:
                reconnected = await self._outbound_connector.ensure_connection(registry_key)
                if reconnected:
                    record = self.registry.get_node(registry_key)
                    ws = record.get("ws") if record else None
                    if self.registry._is_ws_usable(ws):
                        try:
                            await self._send_message(ws, message)
                            return True
                        except Exception:
                            return False
            return False

    async def send_request(self, registry_key: str, message: dict, timeout_ms: int = 30000) -> dict:
        """Send a request and wait for response."""
        request_id = message.get("requestId")
        if not request_id:
            raise ValueError("Request must have requestId")

        future: asyncio.Future = asyncio.get_event_loop().create_future()

        def listener(msg):
            if msg.get("requestId") == request_id and msg.get("type") == MESSAGE_TYPES["RESPONSE"]:
                if not future.done():
                    future.set_result(msg)

        self.add_message_listener(registry_key, listener)
        try:
            sent = await self.send_to_node(registry_key, message)
            if not sent:
                raise ConnectionError("Node is not connected")
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Request {request_id} timed out")
        finally:
            self.remove_message_listener(registry_key, listener)

    def add_message_listener(self, registry_key: str, listener: Callable):
        if registry_key not in self._message_listeners:
            self._message_listeners[registry_key] = set()
        self._message_listeners[registry_key].add(listener)

    def remove_message_listener(self, registry_key: str, listener: Callable):
        listeners = self._message_listeners.get(registry_key)
        if listeners:
            listeners.discard(listener)

    def _notify_listeners(self, registry_key: str, msg: dict):
        listeners = self._message_listeners.get(registry_key)
        if listeners:
            for listener in list(listeners):
                try:
                    listener(msg)
                except Exception:
                    pass

    def _resolve_owner(self, token: str):
        if self.token_resolver:
            owner = self.token_resolver(token)
            if owner:
                return owner
        if not self.allowed_tokens:
            return {}
        if token in self.allowed_tokens:
            return {}
        return False

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
