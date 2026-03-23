import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers import codex_mcp
from providers.codex_mcp import _iter_stream_lines


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_iter_stream_lines_handles_long_single_line():
    long_line = (b"x" * 80_000) + b"\n"
    stream = _FakeStream([long_line[:20_000], long_line[20_000:60_000], long_line[60_000:]])

    async def _collect():
        return [line async for line in _iter_stream_lines(stream)]

    lines = asyncio.run(_collect())

    assert lines == [long_line]


def test_iter_stream_lines_emits_last_line_without_trailing_newline():
    payload = b'{"type":"message"}'
    stream = _FakeStream([payload[:5], payload[5:]])

    async def _collect():
        return [line async for line in _iter_stream_lines(stream)]

    lines = asyncio.run(_collect())

    assert lines == [payload]


def test_iter_stream_lines_handles_real_subprocess_long_line():
    expected = (b"x" * 80_000) + b"\n"
    script = "import sys; sys.stdout.write('x' * 80000 + '\\n'); sys.stdout.flush()"

    async def _collect():
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            return [line async for line in _iter_stream_lines(proc.stdout)]
        finally:
            await proc.wait()

    lines = asyncio.run(_collect())

    assert lines == [expected]


def test_safe_mcp_stdio_client_handles_long_jsonrpc_line():
    if codex_mcp._safe_stdio_client is None or codex_mcp.StdioServerParameters is None:
        pytest.skip("MCP client is not installed")

    output = "x" * 80_000
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "codex/event",
        "params": {
            "msg": {
                "type": "exec_command_end",
                "aggregated_output": output,
            },
        },
    })
    script = f"import sys; sys.stdout.write({payload!r} + '\\n'); sys.stdout.flush()"

    async def _collect():
        server = codex_mcp.StdioServerParameters(
            command=sys.executable,
            args=["-c", script],
        )
        async with codex_mcp._safe_stdio_client(server) as (read_stream, _write_stream):
            return await read_stream.receive()

    session_message = asyncio.run(_collect())

    assert session_message.message.root.method == "codex/event"
    assert session_message.message.root.params["msg"]["aggregated_output"] == output


def test_transform_codex_mcp_event_supports_snake_case_command_fields():
    transformed = codex_mcp._transform_codex_mcp_event(
        {
            "type": "exec_command_end",
            "parsed_cmd": ["python", "-c", "print(1)"],
            "aggregated_output": "ok",
            "cwd": "/tmp/demo",
            "exit_code": 0,
            "status": "completed",
        },
        {},
    )

    assert transformed == [{
        "type": "item",
        "itemType": "command_execution",
        "command": "python -c 'print(1)'",
        "cwd": "/tmp/demo",
        "output": "ok",
        "exitCode": 0,
        "status": "completed",
    }]
