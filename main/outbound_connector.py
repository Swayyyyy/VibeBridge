"""OutboundConnector — Main Server actively connects to Node servers.

Port of server/main/outbound-connector.js.
Used when Main can reach Nodes but Nodes cannot reach Main.
Config format: nodeId@host:port,nodeId2@host2:port2
"""
import asyncio
import json
import random

import websockets

from node_protocol import MESSAGE_TYPES, create_message, parse_message

INITIAL_RECONNECT_DELAY = 1.0  # seconds
MAX_RECONNECT_DELAY = 30.0
CONNECT_TIMEOUT = 10.0


class OutboundConnector:
    def __init__(self, registry, node_ws_server, allowed_tokens: list[str] | None = None):
        self.registry = registry
        self.node_ws_server = node_ws_server
        self.allowed_tokens = allowed_tokens or []
        self.connections: dict[str, dict] = {}
        self._tasks: list[asyncio.Task] = []

    def _ws_is_usable(self, ws) -> bool:
        return self.registry._is_ws_usable(ws)

    def _resolve_owner(self, token: str):
        if self.node_ws_server.token_resolver:
            return self.node_ws_server.token_resolver(token)
        return None

    def _make_connection_key(self, node_id: str, token: str) -> str:
        owner = self._resolve_owner(token)
        owner_user_id = owner.get("id") if isinstance(owner, dict) else None
        return self.registry.make_registry_key(node_id, owner_user_id)

    def start(self, node_addresses: str | list[str], node_tokens: dict[str, str] | None = None):
        """Parse configured node addresses and connect to each node."""
        if isinstance(node_addresses, str):
            entries = [s.strip() for s in node_addresses.split(",") if s.strip()]
        else:
            entries = [str(entry).strip() for entry in node_addresses if str(entry).strip()]
        scheduled_connections = 0
        node_tokens = node_tokens or {}

        for entry in entries:
            if "@" in entry:
                node_id, host_port = entry.split("@", 1)
            else:
                node_id = None
                host_port = entry

            host, port = self._parse_host_port(host_port)
            if not host or not port:
                print(f"[Main] Invalid node address entry: {entry}")
                continue

            if not node_id:
                node_id = host

            token = node_tokens.get(node_id, self.allowed_tokens[0] if self.allowed_tokens else "")
            connection_key = self._make_connection_key(node_id, token)
            existing = self.connections.get(connection_key)
            if existing and existing.get("should_reconnect"):
                host_changed = existing.get("host") != host or existing.get("port") != port
                ws_usable = self._ws_is_usable(existing.get("ws"))
                if node_id in node_tokens:
                    existing["token"] = node_tokens[node_id]

                if host_changed:
                    existing["host"] = host
                    existing["port"] = port
                    existing["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    ws = existing.get("ws")
                    if ws_usable:
                        asyncio.create_task(ws.close())
                        scheduled_connections += 1
                    else:
                        existing["ws"] = None
                        if not existing.get("connecting"):
                            task = asyncio.create_task(self._connect(connection_key))
                            self._tasks.append(task)
                            scheduled_connections += 1
                elif not ws_usable and not existing.get("connecting"):
                    existing["ws"] = None
                    existing["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    task = asyncio.create_task(self._connect(connection_key))
                    self._tasks.append(task)
                    scheduled_connections += 1
                elif existing.get("ws") is None and not existing.get("connecting"):
                    task = asyncio.create_task(self._connect(connection_key))
                    self._tasks.append(task)
                    scheduled_connections += 1
                continue

            self.connections[connection_key] = {
                "registryKey": connection_key,
                "nodeId": node_id,
                "host": host,
                "port": port,
                "ws": None,
                "connecting": False,
                "token": token,
                "reconnect_delay": INITIAL_RECONNECT_DELAY,
                "should_reconnect": True,
            }

            task = asyncio.create_task(self._connect(connection_key))
            self._tasks.append(task)
            scheduled_connections += 1

        if scheduled_connections:
            print(
                f"[Main] OutboundConnector scheduled {scheduled_connections} connection(s) "
                f"across {len(self.connections)} tracked node(s)"
            )

    async def ensure_connection(self, registry_key: str, timeout: float = 8.0) -> bool:
        conn = self.connections.get(registry_key)
        if not conn or not conn.get("should_reconnect"):
            return False

        existing_record = self.registry.get_node(registry_key)
        if existing_record and self._ws_is_usable(existing_record.get("ws")):
            return True

        if not conn.get("connecting"):
            conn["ws"] = None
            conn["reconnect_delay"] = INITIAL_RECONNECT_DELAY
            task = asyncio.create_task(self._connect(registry_key))
            self._tasks.append(task)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            record = self.registry.get_node(registry_key)
            if record and self._ws_is_usable(record.get("ws")) and record.get("status") == "online":
                return True
            await asyncio.sleep(0.1)

        return False

    def stop(self):
        for conn in self.connections.values():
            conn["should_reconnect"] = False
            ws = conn.get("ws")
            if ws:
                asyncio.create_task(ws.close())
        for task in self._tasks:
            task.cancel()

    def _parse_host_port(self, host_port: str) -> tuple[str, int]:
        last_colon = host_port.rfind(":")
        if last_colon == -1:
            return host_port, 3001
        host = host_port[:last_colon]
        try:
            port = int(host_port[last_colon + 1:])
        except ValueError:
            port = 3001
        return host, port

    async def _connect(self, registry_key: str):
        conn = self.connections.get(registry_key)
        if not conn or not conn["should_reconnect"]:
            return
        if conn.get("connecting"):
            return

        conn["connecting"] = True

        registered_node_id = conn["nodeId"]
        current_registry_key = registry_key
        ws = None
        url = f"ws://{conn['host']}:{conn['port']}/ws/main"
        print(f"[Main] Connecting to Node {registered_node_id} at {url}")

        try:
            ws = await asyncio.wait_for(
                websockets.connect(url, max_size=None),
                CONNECT_TIMEOUT,
            )
            conn["ws"] = ws

            # Send HELLO
            hello = create_message(MESSAGE_TYPES["HELLO"], None, {
                "token": conn.get("token", self.allowed_tokens[0] if self.allowed_tokens else ""),
            })
            await ws.send(json.dumps(hello))

            # Listen for messages
            async for raw in ws:
                try:
                    msg = parse_message(raw)
                except Exception:
                    continue

                if msg["type"] == MESSAGE_TYPES["REGISTER_INFO"]:
                    payload = msg.get("payload", {})
                    resolved_id = msg.get("nodeId") or registered_node_id
                    token = conn.get("token", "")
                    owner = self._resolve_owner(token)

                    if resolved_id != registered_node_id:
                        next_registry_key = self._make_connection_key(resolved_id, token)
                        conn_data = self.connections.pop(current_registry_key, None)
                        if conn_data:
                            conn_data["registryKey"] = next_registry_key
                            conn_data["nodeId"] = resolved_id
                            self.connections[next_registry_key] = conn_data
                            conn = conn_data
                        current_registry_key = next_registry_key
                        registered_node_id = resolved_id

                    record = self.node_ws_server.register_outbound(resolved_id, ws, {
                        "displayName": payload.get("nodeName") or resolved_id,
                        "version": payload.get("version"),
                        "capabilities": payload.get("capabilities"),
                        "labels": payload.get("labels"),
                        "port": payload.get("advertisePort") or payload.get("port") or conn["port"],
                        "advertiseHost": payload.get("advertiseHost") or conn["host"],
                        "advertisePort": payload.get("advertisePort") or conn["port"],
                        "host": conn["host"],
                        "explicitPort": conn["port"],
                        "ownerUserId": owner.get("id") if isinstance(owner, dict) else None,
                        "ownerUsername": owner.get("username") if isinstance(owner, dict) else None,
                        "ownerRole": owner.get("role", "user") if isinstance(owner, dict) else "admin",
                    })
                    current_registry_key = record["registryKey"]
                    conn["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    print(f"[Main] Node {resolved_id} registered via outbound")

                elif msg["type"] == MESSAGE_TYPES["HEARTBEAT"]:
                    self.registry.update_heartbeat(current_registry_key)
                elif msg["type"] in (MESSAGE_TYPES["RESPONSE"], MESSAGE_TYPES["EVENT"]):
                    self.node_ws_server._notify_listeners(current_registry_key, msg)

        except asyncio.TimeoutError:
            print(f"[Main] Connection to Node {registered_node_id} timed out")
        except Exception as e:
            print(f"[Main] Outbound error for Node {registered_node_id}: {e}")
        finally:
            conn["ws"] = None
            conn["connecting"] = False
            record = self.registry.get_node(current_registry_key)
            if record and record.get("ws") is ws:
                self.registry.unregister(current_registry_key)
                self.node_ws_server._notify_listeners(current_registry_key, {
                    "type": "node_disconnected",
                    "nodeId": registered_node_id,
                })
            await self._schedule_reconnect(current_registry_key)

    async def _schedule_reconnect(self, registry_key: str):
        conn = self.connections.get(registry_key)
        if not conn or not conn["should_reconnect"]:
            return

        jitter = random.random()
        delay = min(conn["reconnect_delay"] + jitter, MAX_RECONNECT_DELAY)
        print(f"[Main] Reconnecting to Node {conn['nodeId']} in {delay:.0f}s...")

        await asyncio.sleep(delay)
        if conn.get("should_reconnect"):
            conn["reconnect_delay"] = min(conn["reconnect_delay"] * 2, MAX_RECONNECT_DELAY)
            asyncio.create_task(self._connect(registry_key))
