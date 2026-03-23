import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemMessage, TextBlock

from providers import claude_sdk
from providers.claude_sdk import (
    ClaudeSessionWriter,
    active_sessions,
    abort_claude_session,
    query_claude_sdk,
    reconnect_session_writer,
)


class _FakeWriter:
    def __init__(self):
        self.events = []

    def send(self, data):
        self.events.append(data)


class _InterruptibleClaudeClient:
    instances = []

    def __init__(self, options):
        self.options = options
        self.session_id = options.resume or "new-claude-session"
        self.started = asyncio.Event()
        self.interrupted = asyncio.Event()
        self.disconnected = False
        self.prompt_payload = None
        type(self).instances.append(self)

    async def connect(self):
        return None

    async def query(self, prompt, session_id="default"):
        self.prompt_payload = [msg async for msg in prompt]
        self.started.set()

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": self.session_id})
        yield AssistantMessage(content=[TextBlock(text="working")], model="sonnet")
        await self.interrupted.wait()
        yield ResultMessage(
            subtype="interrupt",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id=self.session_id,
            stop_reason="end_turn",
        )

    async def interrupt(self):
        self.interrupted.set()

    async def disconnect(self):
        self.disconnected = True


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def teardown_function():
    active_sessions.clear()
    _InterruptibleClaudeClient.instances.clear()


def test_abort_claude_session_uses_sdk_interrupt_and_suppresses_terminal_error(monkeypatch):
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", _InterruptibleClaudeClient)

    writer = _FakeWriter()

    async def _run():
        query_task = asyncio.create_task(
            query_claude_sdk(
                "Please keep working",
                {
                    "sessionId": "resume-session",
                    "cwd": "/tmp",
                    "projectPath": "/tmp",
                    "model": "sonnet",
                },
                writer,
            )
        )

        assert await _wait_until(lambda: bool(active_sessions.get("resume-session")))
        assert _InterruptibleClaudeClient.instances
        client = _InterruptibleClaudeClient.instances[0]
        assert await _wait_until(lambda: client.started.is_set())

        aborted = await abort_claude_session("resume-session")
        assert aborted is True

        await asyncio.wait_for(query_task, timeout=1)

        assert client.interrupted.is_set()
        assert client.disconnected is True
        assert "resume-session" not in active_sessions

    asyncio.run(_run())

    event_types = [event.get("type") for event in writer.events]
    assert "claude-response" in event_types
    assert "claude-error" not in event_types
    assert "claude-complete" not in event_types


def test_reconnect_claude_session_writer_retargets_inflight_stream():
    original_target = _FakeWriter()
    session_writer = ClaudeSessionWriter(original_target)
    active_sessions["resume-session"] = {
        "status": "running",
        "writer": session_writer,
    }

    replacement_target = _FakeWriter()
    assert reconnect_session_writer("resume-session", replacement_target) is True

    session_writer.send({"type": "claude-response", "data": "hello"})

    assert original_target.events == []
    assert replacement_target.events == [{"type": "claude-response", "data": "hello"}]
