"""Node HTTP self-registration with Main Server.

Activated when MAIN_REGISTER_URL is configured.
The Node periodically POSTs its reachable address to Main, and Main then
connects back to the Node at /ws/main.
"""
import asyncio
import platform
import random
from urllib.parse import urlparse, urlunparse

import httpx
from config import (
    NODE_ADVERTISE_HOST,
    NODE_ADVERTISE_PORT,
    HOST,
    NODE_ID,
    NODE_NAME,
    NODE_REGISTER_TOKEN,
    PORT,
    NODE_LABELS_LIST,
    NODE_CAPABILITIES_LIST,
    MAIN_REGISTER_URL,
)

INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 30.0
REFRESH_INTERVAL = 30.0
REQUEST_TIMEOUT = 10.0


def _normalize_register_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        return ""

    if "://" not in url:
        url = f"http://{url}"

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path.endswith("/api/nodes/register"):
        return urlunparse(parsed._replace(path=path or "/api/nodes/register"))

    if path:
        path = f"{path}/api/nodes/register"
    else:
        path = "/api/nodes/register"

    return urlunparse(parsed._replace(path=path))


def _resolve_registration_host() -> str:
    advertise_host = NODE_ADVERTISE_HOST.strip()
    if advertise_host:
        return advertise_host

    host = HOST.strip()
    if host and host not in {"0.0.0.0", "::"}:
        return host

    return ""


def _resolve_registration_port() -> int:
    if NODE_ADVERTISE_PORT > 0:
        return NODE_ADVERTISE_PORT
    return PORT


class NodeRegistrar:
    def __init__(self, register_url: str):
        self.register_url = _normalize_register_url(register_url)
        self.node_id = NODE_ID or f"node-{platform.node()}"
        self.node_name = NODE_NAME or platform.node()
        self.token = NODE_REGISTER_TOKEN
        self.port = _resolve_registration_port()
        self.labels = list(NODE_LABELS_LIST)
        self.capabilities = list(NODE_CAPABILITIES_LIST)
        self._has_registered = False

    def _build_payload(self) -> dict:
        payload = {
            "token": self.token,
            "nodeId": self.node_id,
            "nodeName": self.node_name,
            "port": self.port,
            "labels": self.labels,
            "capabilities": self.capabilities,
            "advertisePort": self.port,
        }

        host = _resolve_registration_host()
        if host:
            payload["host"] = host

        return payload

    async def _register_once(self) -> bool:
        if not self.register_url:
            print("[Node] MAIN_REGISTER_URL is empty, skipping HTTP self-registration")
            return False

        payload = self._build_payload()

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(self.register_url, json=payload)
        except Exception as exc:
            print(f"[Node] HTTP registration request failed: {exc}")
            return False

        if response.is_success:
            if not self._has_registered:
                print(
                    f"[Node] Registered with Main via HTTP at {self.register_url} "
                    f"as \"{self.node_id}\" on port {self.port}"
                )
                self._has_registered = True
            return True

        error_text = response.text.strip() or response.reason_phrase
        print(f"[Node] HTTP registration failed ({response.status_code}): {error_text}")
        return False

    async def run(self) -> None:
        retry_delay = INITIAL_RETRY_DELAY

        while True:
            registered = await self._register_once()
            if registered:
                retry_delay = INITIAL_RETRY_DELAY
                await asyncio.sleep(REFRESH_INTERVAL)
                continue

            jitter = random.random()
            delay = min(retry_delay + jitter, MAX_RETRY_DELAY)
            print(f"[Node] Retrying HTTP registration in {delay:.0f}s...")
            await asyncio.sleep(delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)


def start_node_registration() -> asyncio.Task | None:
    """Start periodic HTTP self-registration if MAIN_REGISTER_URL is configured."""
    register_url = MAIN_REGISTER_URL.strip()
    if not register_url:
        return None

    registrar = NodeRegistrar(register_url)
    return asyncio.create_task(registrar.run())
