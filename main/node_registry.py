"""In-memory node registry for the Main Server.

Port of server/main/node-registry.js.
Tracks connected Node servers and their status.
"""
import asyncio
import time

NODE_STATUS_ONLINE = "online"
NODE_STATUS_SUSPECT = "suspect"
NODE_STATUS_OFFLINE = "offline"

SUSPECT_TIMEOUT = 45  # seconds
OFFLINE_TIMEOUT = 75  # seconds


class NodeRegistry:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self._health_task: asyncio.Task | None = None

    def start_health_check(self, interval: float = 5.0):
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(interval))

    def stop_health_check(self):
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None

    def register(self, node_id: str, ws, info: dict | None = None) -> dict:
        info = info or {}
        existing = self.nodes.get(node_id)

        # Close old WS if replaced
        if existing and existing.get("ws") and existing["ws"] is not ws:
            try:
                asyncio.create_task(existing["ws"].close(1000, "Replaced by new connection"))
            except Exception:
                pass

        now = time.time()
        record = {
            "nodeId": node_id,
            "displayName": info.get("displayName") or info.get("nodeName") or node_id,
            "status": NODE_STATUS_ONLINE,
            "version": info.get("version", "unknown"),
            "capabilities": info.get("capabilities", []),
            "labels": info.get("labels", []),
            "port": info.get("port", 3000),
            "advertiseHost": info.get("advertiseHost"),
            "advertisePort": info.get("advertisePort"),
            "explicitHost": info.get("host"),
            "explicitPort": info.get("explicitPort"),
            "connectedAt": existing["connectedAt"] if existing else now,
            "lastSeenAt": now,
            "ws": ws,
        }
        self.nodes[node_id] = record
        return record

    def unregister(self, node_id: str):
        record = self.nodes.get(node_id)
        if record:
            record["status"] = NODE_STATUS_OFFLINE
            record["ws"] = None

    def remove(self, node_id: str) -> dict | None:
        record = self.nodes.pop(node_id, None)
        if record and record.get("ws"):
            try:
                asyncio.create_task(record["ws"].close(1000, "Removed from registry"))
            except Exception:
                pass
            record["ws"] = None
        return record

    def get_node(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def get_all_nodes(self) -> list[dict]:
        result = []
        for r in self.nodes.values():
            host = r.get("explicitHost") or r.get("advertiseHost") or "localhost"
            port = r.get("explicitPort") or r.get("advertisePort") or r.get("port")
            result.append({
                "nodeId": r["nodeId"],
                "displayName": r["displayName"],
                "status": r["status"],
                "version": r["version"],
                "capabilities": r["capabilities"],
                "labels": r["labels"],
                "host": host,
                "port": port,
                "connectedAt": r["connectedAt"],
                "lastSeenAt": r["lastSeenAt"],
            })
        return result

    def update_heartbeat(self, node_id: str):
        record = self.nodes.get(node_id)
        if record:
            record["lastSeenAt"] = time.time()
            record["status"] = NODE_STATUS_ONLINE

    def is_online(self, node_id: str) -> bool:
        record = self.nodes.get(node_id)
        return bool(record and record["status"] == NODE_STATUS_ONLINE)

    def get_node_address(self, node_id: str) -> dict | None:
        record = self.nodes.get(node_id)
        if not record:
            return None

        host = record.get("explicitHost") or record.get("advertiseHost") or "localhost"

        port = record.get("explicitPort") or record.get("advertisePort") or record.get("port", 3000)
        return {"host": host, "port": port}

    async def _health_loop(self, interval: float):
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            for record in self.nodes.values():
                if record["status"] == NODE_STATUS_OFFLINE:
                    continue
                elapsed = now - record["lastSeenAt"]
                if elapsed > OFFLINE_TIMEOUT:
                    record["status"] = NODE_STATUS_OFFLINE
                    record["ws"] = None
                elif elapsed > SUSPECT_TIMEOUT:
                    record["status"] = NODE_STATUS_SUSPECT
