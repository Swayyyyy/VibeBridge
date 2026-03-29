"""Browser gateway routes — port of server/main/browser-gateway.js.

Express routes for browser -> Main Server -> Node forwarding.
Mounted at /api/nodes.
"""
from fastapi import APIRouter, HTTPException, Request, Depends

from node_protocol import create_request, NODE_ACTIONS
from middleware.auth import authenticate_token


def create_browser_gateway(registry, node_ws_server) -> APIRouter:
    router = APIRouter(prefix="/api/nodes", tags=["nodes"])

    async def _forward(node_id: str, user: dict, action: str, params: dict, timeout_ms: int = 30000) -> dict:
        node = registry.get_node_for_user(node_id, user)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        message, request_id = create_request(node["nodeId"], action, params)
        try:
            response = await node_ws_server.send_request(node["registryKey"], message, timeout_ms)
            return response.get("payload", {}).get("data")
        except TimeoutError:
            raise HTTPException(504, f"Request to node {node_id} timed out")
        except ConnectionError as e:
            raise HTTPException(502, str(e))

    @router.get("")
    async def list_nodes(request: Request, _=Depends(authenticate_token)):
        return {"nodes": registry.get_all_nodes(request.state.user)}

    @router.get("/{node_id}")
    async def get_node(node_id: str, request: Request, _=Depends(authenticate_token)):
        node = registry.get_node_for_user(node_id, request.state.user)
        if not node:
            raise HTTPException(404, "Node not found")
        info = {k: v for k, v in node.items() if k not in {"ws", "registryKey"}}
        return info

    @router.delete("/{node_id}")
    async def delete_node(node_id: str, request: Request, _=Depends(authenticate_token)):
        node = registry.get_node_for_user(node_id, request.state.user)
        if not node:
            raise HTTPException(404, "Node not found")
        if node.get("status") == "online":
            raise HTTPException(409, "Online nodes cannot be deleted")

        registry.remove(node["registryKey"])
        return {"success": True, "nodeId": node_id}

    @router.get("/{node_id}/projects")
    async def list_projects(node_id: str, request: Request, _=Depends(authenticate_token)):
        return await _forward(node_id, request.state.user, NODE_ACTIONS["PROJECT_LIST"], {})

    @router.get("/{node_id}/projects/{project_name}/sessions")
    async def list_sessions(
        request: Request,
        node_id: str, project_name: str,
        limit: int | None = None, offset: int | None = None,
        provider: str | None = None,
        project_path: str | None = None,
        _=Depends(authenticate_token),
    ):
        params = {"projectName": project_name}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if provider:
            params["provider"] = provider
        if project_path:
            params["projectPath"] = project_path
        return await _forward(node_id, request.state.user, NODE_ACTIONS["PROJECT_SESSIONS"], params)

    @router.get("/{node_id}/projects/{project_name}/sessions/{session_id}/messages")
    async def list_messages(
        request: Request,
        node_id: str, project_name: str, session_id: str,
        limit: int | None = None, offset: int | None = None, provider: str | None = None,
        _=Depends(authenticate_token),
    ):
        params = {"projectName": project_name, "sessionId": session_id}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if provider:
            params["provider"] = provider
        return await _forward(node_id, request.state.user, NODE_ACTIONS["PROJECT_SESSION_MESSAGES"], params)

    @router.post("/{node_id}/chat")
    async def send_chat(node_id: str, request: Request, _=Depends(authenticate_token)):
        body = await request.json()
        return await _forward(node_id, request.state.user, NODE_ACTIONS["CHAT_SEND"], body, 60000)

    @router.post("/{node_id}/chat/abort")
    async def abort_chat(node_id: str, request: Request, _=Depends(authenticate_token)):
        body = await request.json()
        result = await _forward(node_id, request.state.user, NODE_ACTIONS["CHAT_ABORT"], body)
        return result or {"success": True}

    @router.get("/{node_id}/ping")
    async def ping_node(node_id: str, request: Request, _=Depends(authenticate_token)):
        return await _forward(node_id, request.state.user, NODE_ACTIONS["NODE_PING"], {})

    return router
