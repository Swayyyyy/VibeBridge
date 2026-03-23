import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import codex_session_index


class _FakeSessionNamesDb:
    def __init__(self, names=None):
        self._names = names or {}

    def get_name(self, session_id, provider):
        return self._names.get((session_id, provider))


def _create_threads_db(db_path: Path, rows: list[dict]) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO threads (id, title, first_user_message, updated_at, source, archived)
            VALUES (:id, :title, :first_user_message, :updated_at, :source, :archived)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _reset_cache():
    codex_session_index.reset_codex_session_index_cache()
    yield
    codex_session_index.reset_codex_session_index_cache()


def test_sync_codex_session_index_entry_prefers_custom_name(monkeypatch, tmp_path: Path):
    thread_id = "019cffd5-d760-73d1-873a-2269adc9f493"
    index_path = tmp_path / "session_index.jsonl"
    db_path = tmp_path / "state_5.sqlite"

    _create_threads_db(
        db_path,
        [{
            "id": thread_id,
            "title": "DB Title",
            "first_user_message": "DB First Message",
            "updated_at": 1773917027,
            "source": "mcp",
            "archived": 0,
        }],
    )

    monkeypatch.setattr(codex_session_index, "CODEX_SESSION_INDEX_PATH", index_path)
    monkeypatch.setattr(codex_session_index, "CODEX_THREADS_DB_PATH", db_path)
    monkeypatch.setattr(
        codex_session_index,
        "session_names_db",
        _FakeSessionNamesDb({(thread_id, "codex"): "自定义 会话 名称"}),
    )

    assert codex_session_index.sync_codex_session_index_entry(
        thread_id,
        fallback_name="fallback title",
        updated_at="2026-03-20T01:02:03Z",
        prefer_existing_name=False,
    ) is True

    lines = [line for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry == {
        "id": thread_id,
        "thread_name": "自定义 会话 名称",
        "updated_at": "2026-03-20T01:02:03Z",
    }
    assert codex_session_index.is_session_indexed(thread_id) is True
    assert codex_session_index.get_session_index_entry(thread_id) == entry


def test_backfill_codex_session_index_only_adds_missing_mcp_exec(monkeypatch, tmp_path: Path):
    existing_thread_id = "019cffaf-9406-7260-9c40-2295dc761d8f"
    missing_thread_id = "019d0586-f98f-7d80-ad7c-ae7864a59830"
    vscode_thread_id = "019cfb1b-999b-7651-842d-c6b5d639cd3f"
    archived_thread_id = "019d0585-eaff-7252-aed0-f7fff20f213a"

    index_path = tmp_path / "session_index.jsonl"
    db_path = tmp_path / "state_5.sqlite"
    index_path.write_text(
        json.dumps(
            {
                "id": existing_thread_id,
                "thread_name": "Existing Exec Thread",
                "updated_at": "2026-03-18T15:25:30Z",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    _create_threads_db(
        db_path,
        [
            {
                "id": existing_thread_id,
                "title": "Existing Exec Title",
                "first_user_message": "Existing Exec First Message",
                "updated_at": 1773917000,
                "source": "exec",
                "archived": 0,
            },
            {
                "id": missing_thread_id,
                "title": "   Missing   MCP   Thread   ",
                "first_user_message": "Missing MCP First Message",
                "updated_at": 1773917027,
                "source": "mcp",
                "archived": 0,
            },
            {
                "id": vscode_thread_id,
                "title": "Visible VSCode Thread",
                "first_user_message": "Visible VSCode Thread",
                "updated_at": 1773917030,
                "source": "vscode",
                "archived": 0,
            },
            {
                "id": archived_thread_id,
                "title": "Archived MCP Thread",
                "first_user_message": "Archived MCP Thread",
                "updated_at": 1773917040,
                "source": "mcp",
                "archived": 1,
            },
        ],
    )

    monkeypatch.setattr(codex_session_index, "CODEX_SESSION_INDEX_PATH", index_path)
    monkeypatch.setattr(codex_session_index, "CODEX_THREADS_DB_PATH", db_path)
    monkeypatch.setattr(codex_session_index, "session_names_db", _FakeSessionNamesDb())

    result = codex_session_index.backfill_codex_session_index()

    assert result == {"added": 1, "skipped": 1}

    lines = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [entry["id"] for entry in lines] == [existing_thread_id, missing_thread_id]
    assert lines[1]["thread_name"] == "Missing MCP Thread"
    assert lines[1]["updated_at"] == datetime.fromtimestamp(
        1773917027,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
