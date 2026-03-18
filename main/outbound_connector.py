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

            existing = self.connections.get(node_id)
            if existing and existing.get("should_reconnect"):
                host_changed = existing.get("host") != host or existing.get("port") != port
                if node_id in node_tokens:
                    existing["token"] = node_tokens[node_id]

                if host_changed:
                    existing["host"] = host
                    existing["port"] = port
                    existing["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    ws = existing.get("ws")
                    if ws:
                        asyncio.create_task(ws.close())
                        scheduled_connections += 1
                    elif not existing.get("connecting"):
                        task = asyncio.create_task(self._connect(node_id))
                        self._tasks.append(task)
                        scheduled_connections += 1
                elif existing.get("ws") is None and not existing.get("connecting"):
                    existing["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    task = asyncio.create_task(self._connect(node_id))
                    self._tasks.append(task)
                    scheduled_connections += 1
                continue

            self.connections[node_id] = {
                "host": host,
                "port": port,
                "ws": None,
                "connecting": False,
                "token": node_tokens.get(node_id, self.allowed_tokens[0] if self.allowed_tokens else ""),
                "reconnect_delay": INITIAL_RECONNECT_DELAY,
                "should_reconnect": True,
            }

            task = asyncio.create_task(self._connect(node_id))
            self._tasks.append(task)
            scheduled_connections += 1

        if scheduled_connections:
            print(
                f"[Main] OutboundConnector scheduled {scheduled_connections} connection(s) "
                f"across {len(self.connections)} tracked node(s)"
            )

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

    async def _connect(self, node_id: str):
        conn = self.connections.get(node_id)
        if not conn or not conn["should_reconnect"]:
            return
        if conn.get("connecting"):
            return

        conn["connecting"] = True

        registered_node_id = node_id
        ws = None
        url = f"ws://{conn['host']}:{conn['port']}/ws/main"
        print(f"[Main] Connecting to Node {node_id} at {url}")

        try:
            ws = await asyncio.wait_for(websockets.connect(url), CONNECT_TIMEOUT)
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

                    if resolved_id != registered_node_id:
                        conn_data = self.connections.pop(registered_node_id, None)
                        if conn_data:
                            self.connections[resolved_id] = conn_data
                            conn = conn_data
                        registered_node_id = resolved_id

                    self.node_ws_server.register_outbound(resolved_id, ws, {
                        "displayName": payload.get("nodeName") or resolved_id,
                        "version": payload.get("version"),
                        "capabilities": payload.get("capabilities"),
                        "labels": payload.get("labels"),
                        "port": payload.get("advertisePort") or payload.get("port") or conn["port"],
                        "advertiseHost": payload.get("advertiseHost") or conn["host"],
                        "advertisePort": payload.get("advertisePort") or conn["port"],
                        "host": conn["host"],
                        "explicitPort": conn["port"],
                    })
                    conn["reconnect_delay"] = INITIAL_RECONNECT_DELAY
                    print(f"[Main] Node {resolved_id} registered via outbound")

                elif msg["type"] == MESSAGE_TYPES["HEARTBEAT"]:
                    self.registry.update_heartbeat(registered_node_id)
                elif msg["type"] in (MESSAGE_TYPES["RESPONSE"], MESSAGE_TYPES["EVENT"]):
                    self.node_ws_server._notify_listeners(registered_node_id, msg)

        except asyncio.TimeoutError:
            print(f"[Main] Connection to Node {node_id} timed out")
        except Exception as e:
            print(f"[Main] Outbound error for Node {node_id}: {e}")
        finally:
            conn["ws"] = None
            conn["connecting"] = False
            record = self.registry.get_node(registered_node_id)
            if record and record.get("ws") is ws:
                self.registry.unregister(registered_node_id)
                self.node_ws_server._notify_listeners(registered_node_id, {
                    "type": "node_disconnected",
                    "nodeId": registered_node_id,
                })
            await self._schedule_reconnect(registered_node_id)

    async def _schedule_reconnect(self, node_id: str):
        conn = self.connections.get(node_id)
        if not conn or not conn["should_reconnect"]:
            return

        jitter = random.random()
        delay = min(conn["reconnect_delay"] + jitter, MAX_RECONNECT_DELAY)
        print(f"[Main] Reconnecting to Node {node_id} in {delay:.0f}s...")

        await asyncio.sleep(delay)
        if conn.get("should_reconnect"):
            conn["reconnect_delay"] = min(conn["reconnect_delay"] * 2, MAX_RECONNECT_DELAY)
            asyncio.create_task(self._connect(node_id))
