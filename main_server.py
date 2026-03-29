"""Main Server entry point for multi-node Claude Code UI.

Port of server/main-server.js.
Central control plane:
- Accepts Node WS connections at /ws/node
- Serves browser clients at /ws (relay to nodes)
- Provides /api/nodes/* REST endpoints
- Proxies API requests to nodes via X-Node-Id header
"""
import base64
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

import runtime_role

runtime_role.ROLE_OVERRIDE = "main"

from fastapi import FastAPI, WebSocket, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from database.db import initialize_database
from database.db import user_db
from middleware.auth import authenticate_token, authenticate_websocket
from main.node_registry import NodeRegistry
from main.node_ws_server import NodeWsServer
from main.outbound_connector import OutboundConnector
from main.browser_gateway import create_browser_gateway
from main.ws_relay import WsRelay
from main.shell_relay import ShellRelay
from node_protocol import NODE_ACTIONS, create_request
from routes.account import router as account_router, admin_router as admin_router
from routes.auth import router as auth_router
from config import (
    PORT,
    HOST,
    NODE_REGISTER_TOKENS_LIST,
    NODE_ADDRESSES_LIST,
    IS_PLATFORM,
    CONFIG_SOURCE_LABEL,
)

# Parse tokens
_tokens = NODE_REGISTER_TOKENS_LIST


def _resolve_node_owner(token: str) -> dict | None:
    normalized = str(token or "").strip()
    if not normalized:
        return None
    user = user_db.get_user_by_node_register_token(normalized)
    if user and user_db.is_approved_role(user.get("role")):
        return user
    return None

# Create components
registry = NodeRegistry()
node_ws_server = NodeWsServer(registry, _tokens, token_resolver=_resolve_node_owner)
outbound_connector = OutboundConnector(registry, node_ws_server, _tokens)
node_ws_server.attach_outbound_connector(outbound_connector)
ws_relay = WsRelay(registry, node_ws_server)
shell_relay = ShellRelay(registry, node_ws_server, _tokens)

# Create FastAPI app
app = FastAPI(title="cc_server_main", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Refreshed-Token"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "main-server"}


# ---------------------------------------------------------------------------
# Auth routes (handled by Main directly)
# ---------------------------------------------------------------------------

app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Node self-registration endpoint (no JWT, uses node token)
# ---------------------------------------------------------------------------

@app.post("/api/nodes/register")
async def register_node(request: Request):
    body = await request.json()
    token = body.get("token", "")
    host = (body.get("host") or body.get("advertiseHost") or (request.client.host if request.client else "")).strip()
    port = body.get("advertisePort") or body.get("port")

    owner = _resolve_node_owner(token)
    legacy_token_allowed = bool(_tokens and token in _tokens)
    if owner is None and not legacy_token_allowed:
        raise HTTPException(403, "Invalid token")
    if not host or not port:
        raise HTTPException(400, "host and port are required")

    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "port must be an integer") from exc

    node_id = body.get("nodeId") or host
    connection_key = registry.make_registry_key(node_id, owner.get("id") if owner else None)
    existing = outbound_connector.connections.get(connection_key)
    is_new_target = not existing or existing.get("host") != host or existing.get("port") != port
    addr = f"{node_id}@{host}:{port}"
    if is_new_target:
        print(f"[Main] Node registered via HTTP callback: {addr}")
    outbound_connector.start(addr, {node_id: token})
    record = registry.get_node(connection_key)
    if record:
        record["ownerUserId"] = owner.get("id") if owner else None
        record["ownerUsername"] = owner.get("username") if owner else None
        record["ownerRole"] = owner.get("role", "user") if owner else "admin"
    return {
        "success": True,
        "mode": "main-outbound",
        "nodeId": node_id,
        "ownerUserId": owner.get("id") if owner else None,
        "message": f"Will connect to {node_id} at {host}:{port}",
    }


# ---------------------------------------------------------------------------
# Node API routes (browser gateway)
# ---------------------------------------------------------------------------

browser_gateway = create_browser_gateway(registry, node_ws_server)
app.include_router(browser_gateway)


# ---------------------------------------------------------------------------
# API proxy: forward /api/* with X-Node-Id to target Node
# ---------------------------------------------------------------------------

PROXIED_PREFIXES = (
    "/api/projects", "/api/git", "/api/commands", "/api/settings",
    "/api/codex", "/api/mcp", "/api/mcp-utils", "/api/cli",
    "/api/plugins", "/api/taskmaster", "/api/user", "/api/sessions",
    "/api/agent", "/api/browse-filesystem", "/api/create-folder",
    "/api/search", "/api/system",
)

_REQUEST_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "x-node-id",
}

_RESPONSE_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "transfer-encoding",
}


async def _proxy_request_via_node_ws(request: Request, node: dict, decoded: dict) -> Response:
    body = await request.body()
    fwd_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _REQUEST_HOP_BY_HOP_HEADERS
    }
    fwd_headers["x-authenticated-user-id"] = str(decoded["userId"])
    if decoded.get("username"):
        fwd_headers["x-authenticated-username"] = str(decoded["username"])
    if decoded.get("role"):
        fwd_headers["x-authenticated-role"] = str(decoded["role"])

    message, request_id = create_request(
        node["nodeId"],
        NODE_ACTIONS["HTTP_PROXY"],
        {
            "method": request.method,
            "path": request.url.path,
            "queryString": request.url.query,
            "headers": fwd_headers,
            "body": base64.b64encode(body).decode("ascii"),
            "bodyEncoding": "base64",
        },
    )

    try:
        response_msg = await node_ws_server.send_request(node["registryKey"], message, timeout_ms=120000)
    except TimeoutError:
        return Response(
            content=json.dumps({"detail": f"Request to node {node['nodeId']} timed out"}),
            status_code=504,
            media_type="application/json",
        )
    except ConnectionError as exc:
        return Response(
            content=json.dumps({"detail": str(exc)}),
            status_code=502,
            media_type="application/json",
        )

    payload = response_msg.get("payload", {})
    if payload.get("error"):
        return Response(
            content=json.dumps({"detail": payload["error"]}),
            status_code=502,
            media_type="application/json",
        )

    proxied = payload.get("data") or {}
    response_body = proxied.get("body", "")
    if proxied.get("bodyEncoding") == "base64":
        content = base64.b64decode(response_body) if response_body else b""
    else:
        content = response_body.encode("utf-8")

    resp_headers = {
        key: value
        for key, value in (proxied.get("headers") or {}).items()
        if key.lower() not in _RESPONSE_HOP_BY_HOP_HEADERS
    }
    return Response(
        content=content,
        status_code=int(proxied.get("statusCode", 200)),
        headers=resp_headers,
    )


@app.middleware("http")
async def proxy_middleware(request: Request, call_next):
    """Proxy /api/* requests to Node Server when no matching Main route exists."""
    path = request.url.path.rstrip("/")

    # Only proxy known API prefixes
    if not path.startswith(PROXIED_PREFIXES):
        return await call_next(request)

    # Auth check
    from middleware.auth import authenticate_token as _auth, _verify_token
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.query_params.get("token")
    if not token:
        return Response(content='{"detail":"No token provided"}', status_code=401, media_type="application/json")
    decoded = _verify_token(token)
    if not decoded:
        return Response(content='{"detail":"Invalid token"}', status_code=403, media_type="application/json")
    if not decoded.get("role"):
        user = user_db.get_user_by_id(decoded["userId"])
        if user and user.get("role"):
            decoded["role"] = user["role"]
        else:
            decoded["role"] = "user"

    # Resolve node
    node_id = request.headers.get("x-node-id")
    if not node_id:
        nodes = registry.get_all_nodes({"id": decoded["userId"], "role": decoded.get("role", "user")})
        if len(nodes) == 1:
            node_id = nodes[0]["nodeId"]
        elif len(nodes) == 0:
            return Response(content='{"detail":"No nodes connected"}', status_code=503, media_type="application/json")
        else:
            return Response(content='{"detail":"X-Node-Id header required"}', status_code=400, media_type="application/json")

    current_user = {
        "id": decoded["userId"],
        "username": decoded.get("username"),
        "role": decoded.get("role", "user"),
    }
    node = registry.get_node_for_user(node_id, current_user)
    if not node:
        return Response(content=f'{{"detail":"Node {node_id} unavailable"}}', status_code=503, media_type="application/json")
    return await _proxy_request_via_node_ws(request, node, decoded)


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------

@app.websocket("/ws/node")
async def ws_node(ws: WebSocket):
    """Node connections."""
    await node_ws_server.handle_connection(ws)


@app.websocket("/ws")
async def ws_browser(ws: WebSocket):
    """Browser chat relay connections."""
    # Auth
    token = ws.query_params.get("token")
    user = authenticate_websocket(token)
    if not user:
        await ws.close(4003, "Authentication failed")
        return
    await ws_relay.handle_connection(ws, user)


@app.websocket("/shell")
async def ws_shell(ws: WebSocket):
    """Browser shell relay connections."""
    token = ws.query_params.get("token")
    user = authenticate_websocket(token)
    if not user:
        await ws.close(4003, "Authentication failed")
        return
    await shell_relay.handle_connection(ws, user)


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    initialize_database()
    registry.start_health_check()
    print(f"[Main Server] Config: {CONFIG_SOURCE_LABEL}")

    if NODE_ADDRESSES_LIST:
        print(f"[Main] Node addresses: {', '.join(NODE_ADDRESSES_LIST)}")
        outbound_connector.start(NODE_ADDRESSES_LIST)

    display_host = "localhost" if HOST == "0.0.0.0" else HOST
    print(f"[Main Server] Listening on {display_host}:{PORT}")
    print(f"[Main Server] Node WS: ws://{display_host}:{PORT}/ws/node")
    print(f"[Main Server] Browser WS: ws://{display_host}:{PORT}/ws")
    print(f"[Main Server] Tokens: {len(_tokens)} configured" if _tokens else "[Main Server] Tokens: NONE (open)")


@app.on_event("shutdown")
async def shutdown():
    outbound_connector.stop()
    registry.stop_health_check()


# ---------------------------------------------------------------------------
# Static files & SPA fallback (serve frontend to browsers)
# ---------------------------------------------------------------------------

import mimetypes
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DIST_DIR = Path(__file__).parent / "dist"

if DIST_DIR.is_dir():
    # Mount /assets as static files for cache-friendly serving
    assets_dir = DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Use exception handler for SPA fallback — runs only when no route matched,
    # so it won't interfere with API proxy or WebSocket routes.
    _spa_index = DIST_DIR / "index.html"

    def _resolve_dist_file(rel_path: str) -> Path | None:
        """Resolve a browser request to a file inside dist/.

        Deep-linked SPA routes like /session/<id> load index.html, but relative
        asset URLs inside that document become /session/assets/*, /session/icons/*,
        /session/manifest.json, etc. Walk suffixes of the request path so those
        nested requests still map back to the real dist/ files instead of being
        mistaken for SPA routes.
        """
        if not rel_path or ".." in rel_path:
            return None

        direct_candidate = DIST_DIR / rel_path
        if direct_candidate.is_file():
            return direct_candidate

        path_parts = Path(rel_path).parts
        for index in range(1, len(path_parts)):
            suffix_candidate = DIST_DIR.joinpath(*path_parts[index:])
            if suffix_candidate.is_file():
                return suffix_candidate

        return None

    def _looks_like_backend_path(path: str) -> bool:
        """Detect backend URLs that should never fall back to index.html."""
        parts = Path(path.strip("/")).parts
        if not parts:
            return False

        backend_roots = {"api", "ws", "shell", "health"}
        if parts[0] in backend_roots:
            return True

        # Relative URLs from /session/<id> can accidentally turn /api/* into
        # /session/api/*, so treat those as backend paths too.
        if len(parts) >= 2 and parts[0] == "session" and parts[1] in backend_roots:
            return True

        return False

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc):
        # Only serve index.html for non-API GET requests (browser navigation)
        if request.method == "GET" and not _looks_like_backend_path(request.url.path):
            # Try serving a static file first
            rel = request.url.path.lstrip("/")
            candidate = _resolve_dist_file(rel)
            if candidate:
                mt, _ = mimetypes.guess_type(str(candidate))
                return FileResponse(candidate, media_type=mt)
            if _spa_index.is_file():
                return FileResponse(_spa_index)
        # Otherwise return the original 404
        return Response(
            content='{"detail":"Not Found"}',
            status_code=404,
            media_type="application/json",
        )

    print(f"[Main Server] Serving frontend from {DIST_DIR}")
else:
    print(f"[Main Server] No dist/ directory found, frontend not served")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_server:app", host=HOST, port=PORT, reload=True)
