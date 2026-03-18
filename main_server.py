"""Main Server entry point for multi-node Claude Code UI.

Port of server/main-server.js.
Central control plane:
- Accepts Node WS connections at /ws/node
- Serves browser clients at /ws (relay to nodes)
- Provides /api/nodes/* REST endpoints
- Proxies API requests to nodes via X-Node-Id header
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

import runtime_role

runtime_role.ROLE_OVERRIDE = "main"

import httpx
from fastapi import FastAPI, WebSocket, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from database.db import initialize_database
from middleware.auth import authenticate_token, authenticate_websocket
from main.node_registry import NodeRegistry
from main.node_ws_server import NodeWsServer
from main.outbound_connector import OutboundConnector
from main.browser_gateway import create_browser_gateway
from main.ws_relay import WsRelay
from main.shell_relay import ShellRelay
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

# Create components
registry = NodeRegistry()
node_ws_server = NodeWsServer(registry, _tokens)
outbound_connector = OutboundConnector(registry, node_ws_server, _tokens)
ws_relay = WsRelay(registry, node_ws_server)
shell_relay = ShellRelay(registry, _tokens)

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


# ---------------------------------------------------------------------------
# Node self-registration endpoint (no JWT, uses node token)
# ---------------------------------------------------------------------------

@app.post("/api/nodes/register")
async def register_node(request: Request):
    body = await request.json()
    token = body.get("token", "")
    host = (body.get("host") or body.get("advertiseHost") or (request.client.host if request.client else "")).strip()
    port = body.get("advertisePort") or body.get("port")

    if _tokens and token not in _tokens:
        raise HTTPException(403, "Invalid token")
    if not host or not port:
        raise HTTPException(400, "host and port are required")

    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "port must be an integer") from exc

    node_id = body.get("nodeId") or host
    existing = outbound_connector.connections.get(node_id)
    is_new_target = not existing or existing.get("host") != host or existing.get("port") != port
    addr = f"{node_id}@{host}:{port}"
    if is_new_target:
        print(f"[Main] Node registered via HTTP callback: {addr}")
    outbound_connector.start(addr, {node_id: token})
    return {
        "success": True,
        "mode": "main-outbound",
        "nodeId": node_id,
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

    # Resolve node
    node_id = request.headers.get("x-node-id")
    if not node_id:
        nodes = registry.get_all_nodes()
        if len(nodes) == 1:
            node_id = nodes[0]["nodeId"]
        elif len(nodes) == 0:
            return Response(content='{"detail":"No nodes connected"}', status_code=503, media_type="application/json")
        else:
            return Response(content='{"detail":"X-Node-Id header required"}', status_code=400, media_type="application/json")

    addr = registry.get_node_address(node_id)
    if not addr:
        return Response(content=f'{{"detail":"Node {node_id} unavailable"}}', status_code=503, media_type="application/json")

    target_url = f"http://{addr['host']}:{addr['port']}{request.url.path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Forward headers, fix host
    fwd_headers = dict(request.headers)
    fwd_headers["host"] = f"{addr['host']}:{addr['port']}"
    fwd_headers["x-authenticated-user-id"] = str(decoded["userId"])
    if decoded.get("username"):
        fwd_headers["x-authenticated-username"] = str(decoded["username"])

    async with httpx.AsyncClient() as client:
        try:
            body = await request.body()
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                content=body,
                timeout=60.0,
            )
            # Filter hop-by-hop headers
            resp_headers = {k: v for k, v in resp.headers.items()
                           if k.lower() not in ("transfer-encoding", "connection")}
            return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
        except httpx.RequestError as e:
            return Response(content=f'{{"detail":"Proxy error: {e}"}}', status_code=502, media_type="application/json")


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
    await ws_relay.handle_connection(ws)


@app.websocket("/shell")
async def ws_shell(ws: WebSocket):
    """Browser shell relay connections."""
    token = ws.query_params.get("token")
    user = authenticate_websocket(token)
    if not user:
        await ws.close(4003, "Authentication failed")
        return
    await shell_relay.handle_connection(ws)


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
