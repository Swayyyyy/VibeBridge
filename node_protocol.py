"""Shared protocol definitions for Main <-> Node communication.

Port of server/node-protocol.js.
"""
import json
import time

# Message types
MESSAGE_TYPES = {
    "REGISTER": "register",
    "REGISTER_ACK": "register_ack",
    "HELLO": "hello",
    "REGISTER_INFO": "register_info",
    "HEARTBEAT": "heartbeat",
    "REQUEST": "request",
    "RESPONSE": "response",
    "EVENT": "event",
    "ERROR": "error",
}

NODE_ACTIONS = {
    "PROJECT_LIST": "project.list",
    "PROJECT_SESSIONS": "project.sessions",
    "PROJECT_SESSION_MESSAGES": "project.sessionMessages",
    "CHAT_SEND": "chat.send",
    "CHAT_ABORT": "chat.abort",
    "NODE_PING": "node.ping",
    "NODE_GET_CAPABILITIES": "node.getCapabilities",
}

_request_counter = 0


def generate_request_id() -> str:
    global _request_counter
    _request_counter += 1
    return f"req_{int(time.time() * 1000)}_{_request_counter}"


def create_message(msg_type: str, node_id: str | None, payload: dict | None = None, request_id: str | None = None) -> dict:
    return {
        "type": msg_type,
        "nodeId": node_id,
        "timestamp": int(time.time() * 1000),
        "requestId": request_id,
        "payload": payload or {},
    }


def parse_message(raw) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    msg = json.loads(raw)
    if not msg.get("type"):
        raise ValueError("Missing message type")
    return msg


def create_request(node_id: str, action: str, params: dict) -> tuple[dict, str]:
    """Returns (message, request_id)."""
    request_id = generate_request_id()
    message = create_message(MESSAGE_TYPES["REQUEST"], node_id, {"action": action, "params": params}, request_id)
    return message, request_id


def create_response(node_id: str, request_id: str, data=None, error: str | None = None) -> dict:
    return create_message(MESSAGE_TYPES["RESPONSE"], node_id, {"data": data, "error": error}, request_id)


def create_event(node_id: str, request_id: str, event_type: str, data=None) -> dict:
    return create_message(MESSAGE_TYPES["EVENT"], node_id, {"eventType": event_type, "data": data}, request_id)
