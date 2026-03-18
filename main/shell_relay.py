"""Shell WebSocket relay — port of server/main/shell-relay.js.

Proxies /shell connections from browser to Node Server.
Shell is a PTY stream requiring direct bidirectional WebSocket forwarding.
"""
import asyncio
import json

import websockets
from fastapi import WebSocket, WebSocketDisconnect


class ShellRelay:
    def __init__(self, registry, node_tokens: list[str] | None = None):
        self.registry = registry
        self.node_token = (node_tokens or [""])[0] if node_tokens else ""

    async def handle_connection(self, browser_ws: WebSocket):
        """Handle a browser /shell WebSocket connection.

        Intercepts the first message (init) to determine which node to connect to,
        then proxies all messages bidirectionally.
        """
        await browser_ws.accept()

        node_ws = None
        buffered: list[str] = []

        async def _forward_from_node():
            """Forward messages from node to browser."""
            nonlocal node_ws
            try:
                async for msg in node_ws:
                    text = msg if isinstance(msg, str) else msg.decode("utf-8", errors="replace")
                    try:
                        await browser_ws.send_text(text)
                    except Exception:
                        break
            except Exception:
                pass
            # Node WS closed — close browser
            try:
                await browser_ws.close()
            except Exception:
                pass

        forward_task = None

        try:
            while True:
                raw = await browser_ws.receive_text()

                # Already connected — forward directly
                if node_ws:
                    try:
                        await node_ws.send(raw)
                    except Exception:
                        break
                    continue

                # Parse to find target node
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    buffered.append(raw)
                    continue

                node_id = data.get("nodeId")
                if not node_id:
                    nodes = self.registry.get_all_nodes()
                    online = next((n for n in nodes if n["status"] == "online"), None)
                    if online:
                        node_id = online["nodeId"]

                if not node_id:
                    await browser_ws.send_json({
                        "type": "output",
                        "data": "\x1b[31mError: No node available for shell connection\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                addr = self.registry.get_node_address(node_id)
                if not addr:
                    await browser_ws.send_json({
                        "type": "output",
                        "data": f"\x1b[31mError: Node \"{node_id}\" is unavailable\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                token_param = f"?token={self.node_token}" if self.node_token else ""
                node_url = f"ws://{addr['host']}:{addr['port']}/shell{token_param}"
                print(f"[Main] Shell relay: connecting to node {node_id} at {node_url}")

                buffered.append(raw)

                try:
                    node_ws = await websockets.connect(node_url)
                except Exception as e:
                    await browser_ws.send_json({
                        "type": "output",
                        "data": f"\x1b[31mShell connection error: {e}\x1b[0m\r\n",
                    })
                    await browser_ws.close()
                    return

                # Send buffered messages
                for msg in buffered:
                    await node_ws.send(msg)
                buffered.clear()

                # Start forwarding from node
                forward_task = asyncio.create_task(_forward_from_node())

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[Main] Shell relay error: {e}")
        finally:
            if node_ws:
                try:
                    await node_ws.close()
                except Exception:
                    pass
            if forward_task and not forward_task.done():
                forward_task.cancel()
