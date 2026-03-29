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

    @staticmethod
    def make_registry_key(node_id: str, owner_user_id: int | None) -> str:
        owner_segment = str(owner_user_id) if owner_user_id is not None else "legacy"
        return f"{owner_segment}::{node_id}"

    @staticmethod
    def _is_ws_usable(ws) -> bool:
        if ws is None:
            return False

        client_state = getattr(ws, "client_state", None)
        application_state = getattr(ws, "application_state", None)
        if client_state is not None or application_state is not None:
            state_names = [
                getattr(value, "name", str(value)).upper()
                for value in (client_state, application_state)
                if value is not None
            ]
            if state_names:
                if any("DISCONNECT" in name or "CLOSED" in name for name in state_names):
                    return False
                if all("CONNECTED" in name for name in state_names):
                    return True

        closed = getattr(ws, "closed", None)
        if isinstance(closed, bool):
            return not closed

        state = getattr(ws, "state", None)
        if state is not None:
            state_name = getattr(state, "name", None)
            if state_name:
                normalized = str(state_name).upper()
                if "OPEN" in normalized or "CONNECTED" in normalized:
                    return True
                if "CLOSED" in normalized or "DISCONNECT" in normalized:
                    return False

        try:
            return bool(ws)
        except Exception:
            return True

    def _effective_status(self, record: dict) -> str:
        status = record.get("status", NODE_STATUS_OFFLINE)
        if status == NODE_STATUS_ONLINE and not self._is_ws_usable(record.get("ws")):
            return NODE_STATUS_OFFLINE
        return status

    def start_health_check(self, interval: float = 5.0):
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(interval))

    def stop_health_check(self):
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None

    def register(self, node_id: str, ws, info: dict | None = None) -> dict:
        info = info or {}
        registry_key = self.make_registry_key(node_id, info.get("ownerUserId"))
        existing = self.nodes.get(registry_key)

        # Close old WS if replaced
        if existing and existing.get("ws") and existing["ws"] is not ws:
            try:
                asyncio.create_task(existing["ws"].close(1000, "Replaced by new connection"))
            except Exception:
                pass

        now = time.time()
        record = {
            "registryKey": registry_key,
            "nodeId": node_id,
            "displayName": info.get("displayName") or info.get("nodeName") or node_id,
            "ownerUserId": info.get("ownerUserId"),
            "ownerUsername": info.get("ownerUsername"),
            "ownerRole": info.get("ownerRole", "user"),
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
        self.nodes[registry_key] = record
        return record

    def unregister(self, registry_key: str):
        record = self.nodes.get(registry_key)
        if record:
            record["status"] = NODE_STATUS_OFFLINE
            record["ws"] = None

    def remove(self, registry_key: str) -> dict | None:
        record = self.nodes.pop(registry_key, None)
        if record and record.get("ws"):
            try:
                asyncio.create_task(record["ws"].close(1000, "Removed from registry"))
            except Exception:
                pass
            record["ws"] = None
        return record

    def get_node(self, registry_key: str) -> dict | None:
        return self.nodes.get(registry_key)

    @staticmethod
    def _can_user_access_node(user: dict | None, record: dict) -> bool:
        if user is None:
            return False
        owner_user_id = record.get("ownerUserId")
        resolved_user_id = user.get("id", user.get("userId"))
        return owner_user_id is not None and owner_user_id == resolved_user_id

    def get_all_nodes(self, user: dict | None = None) -> list[dict]:
        result = []
        for r in self.nodes.values():
            if user is not None and not self._can_user_access_node(user, r):
                continue
            status = self._effective_status(r)
            if status != r.get("status"):
                r["status"] = status
            host = r.get("explicitHost") or r.get("advertiseHost") or "localhost"
            port = r.get("explicitPort") or r.get("advertisePort") or r.get("port")
            result.append({
                "nodeId": r["nodeId"],
                "displayName": r["displayName"],
                "status": status,
                "version": r["version"],
                "capabilities": r["capabilities"],
                "labels": r["labels"],
                "ownerUserId": r.get("ownerUserId"),
                "ownerUsername": r.get("ownerUsername"),
                "host": host,
                "port": port,
                "connectedAt": r["connectedAt"],
                "lastSeenAt": r["lastSeenAt"],
            })
        return result

    def update_heartbeat(self, registry_key: str):
        record = self.nodes.get(registry_key)
        if record:
            record["lastSeenAt"] = time.time()
            record["status"] = NODE_STATUS_ONLINE

    def is_online(self, registry_key: str) -> bool:
        record = self.nodes.get(registry_key)
        if not record:
            return False
        status = self._effective_status(record)
        if status != record.get("status"):
            record["status"] = status
        return status == NODE_STATUS_ONLINE

    def get_node_for_user(self, node_id: str, user: dict | None) -> dict | None:
        for record in self.nodes.values():
            if record.get("nodeId") != node_id:
                continue
            if not self._can_user_access_node(user, record):
                continue
            return record
        return None

    def get_node_address(self, registry_key: str) -> dict | None:
        record = self.nodes.get(registry_key)
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
