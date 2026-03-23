import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.codex_mcp import (
    CodexSessionWriter,
    _move_active_session,
    active_codex_sessions,
    reconnect_codex_session_writer,
)


class _FakeWriter:
    def __init__(self):
        self.events = []

    def send(self, data):
        self.events.append(data)


def teardown_function():
    active_codex_sessions.clear()


def test_reconnect_codex_session_writer_retargets_inflight_stream_after_session_rekey():
    original_target = _FakeWriter()
    session_writer = CodexSessionWriter(original_target)
    active_codex_sessions["draft-session"] = {
        "status": "running",
        "writer": session_writer,
    }

    _move_active_session("draft-session", "real-session")

    replacement_target = _FakeWriter()
    assert reconnect_codex_session_writer("real-session", replacement_target) is True

    session_writer.send({"type": "codex-response", "data": "hello"})

    assert original_target.events == []
    assert replacement_target.events == [{"type": "codex-response", "data": "hello"}]
