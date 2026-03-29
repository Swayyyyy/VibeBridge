"""Shell WebSocket relay — port of server/main/shell-relay.js.

Proxies /shell connections from browser to Node Server.
Shell is tunneled through the existing Main <-> Node WS connection so private
nodes do not need a separate callback path.
"""
import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from node_protocol import NODE_ACTIONS, create_request


class ShellRelay:
    def __init__(self, registry, node_ws_server, node_tokens: list[str] | None = None):
        self.registry = registry
        self.node_ws_server = node_ws_server
        self.node_token = (node_tokens or [""])[0] if node_tokens else ""

    async def handle_connection(self, browser_ws: WebSocket, user: dict | None = None):
        """Handle a browser /shell WebSocket connection.

        Intercepts the first message (init) to determine which node to connect to,
        then proxies all messages bidirectionally.
        """
        await browser_ws.accept()

        buffered: list[str] = []
        node_id = None
        registry_key = None
        shell_id = None
        shell_listener = None

        try:
            while True:
                raw = await browser_ws.receive_text()

                # Already connected — forward directly over the node WS tunnel
                if shell_id and node_id and registry_key:
                    message, _request_id = create_request(
                        node_id,
                        NODE_ACTIONS["SHELL_MESSAGE"],
                        {"shellId": shell_id, "raw": raw},
                    )
                    sent = await self.node_ws_server.send_to_node(registry_key, message)
                    if not sent:
                        break
                    continue

                # Parse to find target node
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    buffered.append(raw)
                    continue

                target_node_id = data.get("nodeId")
                if not target_node_id:
                    nodes = self.registry.get_all_nodes(user)
                    online = next((n for n in nodes if n["status"] == "online"), None)
                    if online:
                        target_node_id = online["nodeId"]

                if not target_node_id:
                    await browser_ws.send_json({
                        "type": "output",
                        "data": "\x1b[31mError: No node available for shell connection\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                node = self.registry.get_node_for_user(target_node_id, user)
                if not node:
                    await browser_ws.send_json({
                        "type": "output",
                        "data": f"\x1b[31mError: Node \"{target_node_id}\" is unavailable\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                buffered.append(raw)
                node_id = node["nodeId"]
                registry_key = node["registryKey"]

                open_message, shell_id = create_request(
                    node_id,
                    NODE_ACTIONS["SHELL_OPEN"],
                    {"raw": buffered[0]},
                )

                async def _forward_shell_event(msg: dict):
                    payload = msg.get("payload", {})
                    if msg.get("requestId") != shell_id:
                        return
                    if msg.get("type") == "node_disconnected":
                        try:
                            await browser_ws.close()
                        except Exception:
                            pass
                        return
                    if msg.get("type") != "event" or payload.get("eventType") != "shell":
                        return
                    event_data = payload.get("data")
                    if event_data is None:
                        return
                    try:
                        await browser_ws.send_text(json.dumps(event_data))
                    except Exception:
                        pass

                def _listener(msg: dict):
                    asyncio.create_task(_forward_shell_event(msg))

                shell_listener = _listener
                self.node_ws_server.add_message_listener(registry_key, shell_listener)

                try:
                    await self.node_ws_server.send_request(registry_key, open_message, timeout_ms=15000)
                except Exception as e:
                    if shell_listener:
                        self.node_ws_server.remove_message_listener(registry_key, shell_listener)
                        shell_listener = None
                    await browser_ws.send_json({
                        "type": "output",
                        "data": f"\x1b[31mShell connection error: {e}\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                buffered.clear()

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[Main] Shell relay error: {e}")
        finally:
            if registry_key and shell_listener:
                self.node_ws_server.remove_message_listener(registry_key, shell_listener)
            if registry_key and node_id and shell_id:
                close_message, _request_id = create_request(
                    node_id,
                    NODE_ACTIONS["SHELL_CLOSE"],
                    {"shellId": shell_id},
                )
                try:
                    await self.node_ws_server.send_to_node(registry_key, close_message)
                except Exception:
                    pass
