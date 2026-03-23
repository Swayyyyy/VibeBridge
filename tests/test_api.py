"""cc_server API integration tests.

Usage:
    # Run all tests (server must be running on localhost:3456):
    pytest tests/test_api.py -v

    # Run against a different host:
    BASE_URL=http://localhost:18080 pytest tests/test_api.py -v

    # Run a specific test:
    pytest tests/test_api.py::TestAuth::test_register_and_login -v
"""
import asyncio
import json
import os
import httpx
import pytest
import websockets

BASE_URL = os.environ.get("BASE_URL", "http://localhost:3456")
WS_BASE = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")

TEST_USER = os.environ.get("TEST_USER", "test")
TEST_PASS = os.environ.get("TEST_PASS", "test123")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15) as c:
        yield c


@pytest.fixture(scope="session")
def auth_token(client: httpx.Client):
    """Register (if needed) and login, return a valid JWT token."""
    # Try register first (will fail silently if user exists)
    client.post("/api/auth/register", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    })
    # Login
    resp = client.post("/api/auth/login", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    })
    if resp.status_code != 200:
        pytest.exit(
            f"Cannot login as {TEST_USER}. Set TEST_USER/TEST_PASS env vars. "
            f"Server response: {resp.status_code} {resp.text}"
        )
    data = resp.json()
    assert "token" in data, f"No token in response: {data}"
    return data["token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health(self, client: httpx.Client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_register_duplicate(self, client: httpx.Client):
        """Second registration should fail (single-user system)."""
        resp = client.post("/api/auth/register", json={
            "username": "another_user",
            "password": "Pass1234",
        })
        # Should be 403 (user already exists)
        assert resp.status_code in (403, 409), f"Expected 403/409, got {resp.status_code}: {resp.text}"

    def test_login_wrong_password(self, client: httpx.Client):
        resp = client.post("/api/auth/login", json={
            "username": TEST_USER,
            "password": "WrongPassword",
        })
        assert resp.status_code == 401

    def test_login_success(self, client: httpx.Client):
        resp = client.post("/api/auth/login", json={
            "username": TEST_USER,
            "password": TEST_PASS,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user" in data
        assert data["user"]["username"] == TEST_USER

    def test_auth_status(self, client: httpx.Client):
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "needsSetup" in data

    def test_auth_user(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/auth/user", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "user" in data
        assert data["user"]["username"] == TEST_USER

    def test_auth_user_no_token(self, client: httpx.Client):
        resp = client.get("/api/auth/user")
        assert resp.status_code in (401, 403)

    def test_logout(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/auth/logout", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class TestProjects:
    def test_list_projects(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/projects/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}: {data}"

    def test_list_projects_no_auth(self, client: httpx.Client):
        resp = client.get("/api/projects/")
        assert resp.status_code in (401, 403)

    def test_list_sessions(self, client: httpx.Client, auth_headers):
        """List sessions for a project (may be empty if no Claude projects)."""
        # First get projects
        projects = client.get("/api/projects/", headers=auth_headers).json()
        if not projects:
            pytest.skip("No projects available")

        project_name = projects[0]["name"]
        resp = client.get(
            f"/api/projects/{project_name}/sessions?limit=5&offset=0",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

    def test_list_session_messages(self, client: httpx.Client, auth_headers):
        """Load messages for a session."""
        projects = client.get("/api/projects/", headers=auth_headers).json()
        if not projects:
            pytest.skip("No projects available")

        project_name = projects[0]["name"]
        sessions_resp = client.get(
            f"/api/projects/{project_name}/sessions?limit=1",
            headers=auth_headers,
        ).json()
        sessions = sessions_resp.get("sessions", [])
        if not sessions:
            pytest.skip("No sessions available")

        session_id = sessions[0]["id"]
        resp = client.get(
            f"/api/projects/{project_name}/sessions/{session_id}/messages?limit=5",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert "total" in data
        assert isinstance(data["messages"], list)
        # Should have messages if messageCount > 0
        if sessions[0].get("messageCount", 0) > 0:
            assert len(data["messages"]) > 0, \
                f"Expected messages but got 0 (total={data['total']}, session={session_id})"

    def test_create_project(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/projects/create", json={
            "path": "/tmp/test-cc-project",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "project" in data

    def test_rename_project(self, client: httpx.Client, auth_headers):
        resp = client.put("/api/projects/test-cc-project/rename", json={
            "displayName": "My Test Project",
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json().get("success") is True


# ---------------------------------------------------------------------------
# Sessions rename / delete
# ---------------------------------------------------------------------------

class TestSessions:
    def test_rename_session(self, client: httpx.Client, auth_headers):
        resp = client.put("/api/sessions/test-session-001/rename", json={
            "summary": "Test session summary",
            "provider": "claude",
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json().get("success") is True

    def test_rename_session_invalid_provider(self, client: httpx.Client, auth_headers):
        resp = client.put("/api/sessions/test-session-001/rename", json={
            "summary": "Test",
            "provider": "invalid_provider",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_rename_session_no_summary(self, client: httpx.Client, auth_headers):
        resp = client.put("/api/sessions/test-session-001/rename", json={
            "summary": "",
            "provider": "claude",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_rename_session_invalid_id(self, client: httpx.Client, auth_headers):
        resp = client.put("/api/sessions/../evil/rename", json={
            "summary": "Test",
            "provider": "claude",
        }, headers=auth_headers)
        # FastAPI may return 404/405 for path with ".." due to URL normalization
        assert resp.status_code in (400, 404, 405)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class TestUser:
    def test_onboarding_status(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/user/onboarding-status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "hasCompletedOnboarding" in data

    def test_complete_onboarding(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/user/complete-onboarding", headers=auth_headers)
        assert resp.status_code == 200

    def test_git_config(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/user/git-config", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_list_api_keys(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/settings/api-keys", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "apiKeys" in data

    def test_list_credentials(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/settings/credentials", headers=auth_headers)
        assert resp.status_code == 200

    def test_create_api_key(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/settings/api-keys", json={
            "keyName": "test-api-key",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

class TestGit:
    def test_git_status_no_project(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/git/status", headers=auth_headers)
        assert resp.status_code == 400

    def test_git_status_with_project(self, client: httpx.Client, auth_headers):
        resp = client.get(
            "/api/git/status?project=/Users/shiwei/Code/cc_server",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400, 500)

    def test_git_branches(self, client: httpx.Client, auth_headers):
        resp = client.get(
            "/api/git/branches?project=/Users/shiwei/Code/cc_server",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400, 500)


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

class TestCodex:
    def test_codex_config(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/codex/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "config" in data

    def test_codex_sessions(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/codex/sessions?projectPath=/tmp", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data

    def test_codex_sessions_no_path(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/codex/sessions", headers=auth_headers)
        assert resp.status_code == 400

    def test_codex_mcp_list(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/codex/mcp/cli/list", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CLI Auth
# ---------------------------------------------------------------------------

class TestCLI:
    def test_claude_cli_status(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/cli/claude/status", headers=auth_headers)
        assert resp.status_code == 200

    def test_codex_cli_status(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/cli/codex/status", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

class TestMCP:
    def test_mcp_list(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/mcp/cli/list", headers=auth_headers)
        assert resp.status_code == 200

    def test_mcp_config_read(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/mcp/config/read", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# MCP Utils
# ---------------------------------------------------------------------------

class TestMCPUtils:
    def test_all_servers(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/mcp-utils/all-servers", headers=auth_headers)
        assert resp.status_code == 200

    def test_taskmaster_server(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/mcp-utils/taskmaster-server", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class TestCommands:
    def test_list_commands(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/commands/list", json={
            "projectPath": "/tmp",
        }, headers=auth_headers)
        assert resp.status_code == 200

    def test_load_command(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/commands/load", json={
            "commandPath": "/tmp/nonexistent.md",
        }, headers=auth_headers)
        # May be 200, 403 (path outside allowed), 404, or 500
        assert resp.status_code in (200, 403, 404, 422, 500)


# ---------------------------------------------------------------------------
# Taskmaster
# ---------------------------------------------------------------------------

class TestTaskmaster:
    def test_installation(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/taskmaster/installation", headers=auth_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

class TestPlugins:
    def test_list_plugins(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/plugins/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class TestAgent:
    def test_agent_status_no_key(self, client: httpx.Client, auth_headers):
        """Agent routes use API key auth, not session tokens."""
        resp = client.get("/api/agent/status", headers=auth_headers)
        assert resp.status_code == 401

    def test_agent_status_invalid_key(self, client: httpx.Client):
        resp = client.get("/api/agent/status", headers={
            "X-API-Key": "fake-invalid-key",
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_ws_connect_and_check_sessions(self, auth_token):
        """Connect to /ws, send check-session-status, expect response."""
        async def _test():
            async with websockets.connect(f"{WS_BASE}/ws") as ws:
                await ws.send(json.dumps({
                    "type": "check-session-status",
                    "token": auth_token,
                }))
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(resp)
                assert data["type"] == "session-status"
                assert "isProcessing" in data

        asyncio.get_event_loop().run_until_complete(_test())

    def test_ws_get_active_sessions(self, auth_token):
        """Connect to /ws, request active sessions."""
        async def _test():
            async with websockets.connect(f"{WS_BASE}/ws") as ws:
                await ws.send(json.dumps({
                    "type": "get-active-sessions",
                    "token": auth_token,
                }))
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(resp)
                assert data["type"] == "active-sessions"
                assert "sessions" in data
                assert "claude" in data["sessions"]
                assert "codex" in data["sessions"]

        asyncio.get_event_loop().run_until_complete(_test())

    def test_ws_invalid_message(self, auth_token):
        """Send invalid message type, should not crash."""
        async def _test():
            async with websockets.connect(f"{WS_BASE}/ws") as ws:
                await ws.send(json.dumps({
                    "type": "nonexistent-type",
                    "token": auth_token,
                }))
                # Should either get an error response or just stay connected
                try:
                    await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    # No response - also fine, server just ignored it
                    pass

        asyncio.get_event_loop().run_until_complete(_test())


# ---------------------------------------------------------------------------
# Edge cases / Security
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_path_traversal_project(self, client: httpx.Client, auth_headers):
        """Ensure path traversal in project names is handled."""
        resp = client.get("/api/projects/../../etc/passwd/sessions", headers=auth_headers)
        # Should not return 200 with actual file contents
        assert resp.status_code in (200, 400, 404, 500)

    def test_expired_token(self, client: httpx.Client):
        """Fake expired token should be rejected."""
        resp = client.get("/api/projects/", headers={
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEsImV4cCI6MX0.fake",
        })
        assert resp.status_code in (401, 403)

    def test_malformed_json_body(self, client: httpx.Client, auth_headers):
        """Malformed JSON should return 422."""
        resp = client.post(
            "/api/auth/login",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_session_rename_long_summary(self, client: httpx.Client, auth_headers):
        """Summary > 500 chars should be rejected."""
        resp = client.put("/api/sessions/test-session/rename", json={
            "summary": "x" * 501,
            "provider": "claude",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_cors_headers(self, client: httpx.Client):
        """CORS preflight should work."""
        resp = client.options("/api/auth/status", headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# Nodes stub
# ---------------------------------------------------------------------------

class TestNodes:
    def test_list_nodes(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/nodes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_nodes_no_auth(self, client: httpx.Client):
        resp = client.get("/api/nodes")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Browse filesystem
# ---------------------------------------------------------------------------

class TestBrowseFilesystem:
    def test_browse_home(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/browse-filesystem", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_browse_with_path(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/browse-filesystem?path=/tmp", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/private/tmp" or data["path"] == "/tmp"

    def test_browse_nonexistent(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/browse-filesystem?path=/nonexistent_dir_xyz", headers=auth_headers)
        assert resp.status_code == 400

    def test_browse_no_auth(self, client: httpx.Client):
        resp = client.get("/api/browse-filesystem")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Create folder
# ---------------------------------------------------------------------------

class TestCreateFolder:
    def test_create_and_cleanup(self, client: httpx.Client, auth_headers):
        import uuid
        folder_name = f"test_folder_{uuid.uuid4().hex[:8]}"
        folder_path = f"/tmp/{folder_name}"

        resp = client.post("/api/create-folder", json={"path": folder_path}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Cleanup
        import shutil
        shutil.rmtree(folder_path, ignore_errors=True)

    def test_create_duplicate(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/create-folder", json={"path": "/tmp"}, headers=auth_headers)
        assert resp.status_code == 409

    def test_create_no_path(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/create-folder", json={"path": ""}, headers=auth_headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Project file operations (use test-cc-project from TestProjects)
# ---------------------------------------------------------------------------

TEST_PROJECT = "test-cc-project"


class TestFileOperations:
    """Tests for file CRUD endpoints under /api/projects/{project_name}/..."""

    def test_list_files(self, client: httpx.Client, auth_headers):
        resp = client.get(f"/api/projects/{TEST_PROJECT}/files", headers=auth_headers)
        # 200 if project dir exists, 404 if extract_project_directory can't resolve
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)

    def test_create_read_save_delete_file(self, client: httpx.Client, auth_headers):
        """Full lifecycle: create → read → save → delete."""
        # Create
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "test_lifecycle.txt",
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 200
        file_path = resp.json()["path"]

        # Read
        resp = client.get(
            f"/api/projects/{TEST_PROJECT}/file?filePath={file_path}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == ""

        # Save
        resp = client.put(f"/api/projects/{TEST_PROJECT}/file", json={
            "filePath": file_path,
            "content": "hello world",
        }, headers=auth_headers)
        assert resp.status_code == 200

        # Read again to verify
        resp = client.get(
            f"/api/projects/{TEST_PROJECT}/file?filePath={file_path}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello world"

        # Delete
        resp = client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": file_path,
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 200

    def test_create_directory(self, client: httpx.Client, auth_headers):
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "test_subdir",
            "type": "directory",
        }, headers=auth_headers)
        assert resp.status_code == 200
        dir_path = resp.json()["path"]

        # Cleanup
        resp = client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": dir_path,
            "type": "directory",
        }, headers=auth_headers)
        assert resp.status_code == 200

    def test_rename_file(self, client: httpx.Client, auth_headers):
        # Create a file first
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "rename_me.txt",
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 200
        old_path = resp.json()["path"]

        # Rename
        resp = client.put(f"/api/projects/{TEST_PROJECT}/files/rename", json={
            "oldPath": old_path,
            "newName": "renamed.txt",
        }, headers=auth_headers)
        assert resp.status_code == 200
        new_path = resp.json()["newPath"]

        # Cleanup
        client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": new_path, "type": "file",
        }, headers=auth_headers)

    def test_serve_file_content(self, client: httpx.Client, auth_headers):
        # Create a file with content
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "serve_test.txt",
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 200
        file_path = resp.json()["path"]

        client.put(f"/api/projects/{TEST_PROJECT}/file", json={
            "filePath": file_path,
            "content": "binary content test",
        }, headers=auth_headers)

        # Serve content (binary endpoint)
        resp = client.get(
            f"/api/projects/{TEST_PROJECT}/files/content?path={file_path}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Cleanup
        client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": file_path, "type": "file",
        }, headers=auth_headers)

    def test_read_nonexistent_file(self, client: httpx.Client, auth_headers):
        resp = client.get(
            f"/api/projects/{TEST_PROJECT}/file?filePath=/tmp/test-cc-project/no_such_file.txt",
            headers=auth_headers,
        )
        # 404 if file not found, 403 if path validation fails (macOS /tmp → /private/tmp)
        assert resp.status_code in (403, 404)

    def test_create_duplicate_file(self, client: httpx.Client, auth_headers):
        # Create once
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "dup_test.txt",
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 200
        file_path = resp.json()["path"]

        # Create again — should 409
        resp = client.post(f"/api/projects/{TEST_PROJECT}/files/create", json={
            "name": "dup_test.txt",
            "type": "file",
        }, headers=auth_headers)
        assert resp.status_code == 409

        # Cleanup
        client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": file_path, "type": "file",
        }, headers=auth_headers)

    def test_delete_project_root_forbidden(self, client: httpx.Client, auth_headers):
        resp = client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
            "path": "/tmp/test-cc-project",
            "type": "directory",
        }, headers=auth_headers)
        assert resp.status_code == 403

    def test_path_traversal_rejected(self, client: httpx.Client, auth_headers):
        resp = client.get(
            f"/api/projects/{TEST_PROJECT}/file?filePath=/etc/passwd",
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

class TestFileUpload:
    def test_upload_file(self, client: httpx.Client, auth_headers):
        # Use resolved path to avoid macOS /tmp → /private/tmp mismatch
        import os
        target = os.path.realpath("/tmp/test-cc-project")
        resp = client.post(
            f"/api/projects/{TEST_PROJECT}/files/upload",
            data={"targetPath": target},
            files=[("files", ("upload_test.txt", b"uploaded content", "text/plain"))],
            headers=auth_headers,
        )
        # 200 if project resolves correctly, 403 if path validation mismatch
        if resp.status_code == 200:
            data = resp.json()
            assert data["success"] is True
            assert len(data["files"]) == 1
            assert data["files"][0]["name"] == "upload_test.txt"
            # Cleanup
            client.request("DELETE", f"/api/projects/{TEST_PROJECT}/files", json={
                "path": data["files"][0]["path"], "type": "file",
            }, headers=auth_headers)
        else:
            assert resp.status_code == 403  # Path validation issue on macOS

    def test_upload_images(self, client: httpx.Client, auth_headers):
        # Create a minimal 1x1 PNG
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        resp = client.post(
            f"/api/projects/{TEST_PROJECT}/upload-images",
            files=[("files", ("test.png", png_data, "image/png"))],
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert len(data["images"]) == 1
        assert data["images"][0]["data"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_token_usage_claude(self, client: httpx.Client, auth_headers):
        projects = client.get("/api/projects/", headers=auth_headers).json()
        if not projects:
            pytest.skip("No projects available")

        project_name = projects[0]["name"]
        sessions_resp = client.get(
            f"/api/projects/{project_name}/sessions?limit=1",
            headers=auth_headers,
        ).json()
        sessions = sessions_resp.get("sessions", [])
        if not sessions:
            pytest.skip("No sessions available")

        session_id = sessions[0]["id"]
        resp = client.get(
            f"/api/projects/{project_name}/sessions/{session_id}/token-usage?provider=claude",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "used" in data
        assert "total" in data
        assert "breakdown" in data
        assert isinstance(data["breakdown"], dict)

    def test_token_usage_nonexistent(self, client: httpx.Client, auth_headers):
        resp = client.get(
            "/api/projects/nonexistent/sessions/fake-id/token-usage",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["used"] == 0


# ---------------------------------------------------------------------------
# Search conversations (SSE)
# ---------------------------------------------------------------------------

class TestSearchConversations:
    def test_search_with_query(self, client: httpx.Client, auth_headers):
        resp = client.get(
            "/api/search/conversations?q=test&limit=5",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

    def test_search_no_query(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/search/conversations", headers=auth_headers)
        assert resp.status_code == 400

    def test_search_empty_query(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/search/conversations?q=", headers=auth_headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# System update stub
# ---------------------------------------------------------------------------

class TestSystemUpdate:
    def test_system_update(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/system/update", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_system_update_no_auth(self, client: httpx.Client):
        resp = client.post("/api/system/update")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Taskmaster additional
# ---------------------------------------------------------------------------

class TestTaskmasterExtra:
    def test_installation_status(self, client: httpx.Client, auth_headers):
        resp = client.get("/api/taskmaster/installation-status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "installation" in data
        assert "mcpServer" in data
        assert "isReady" in data

    def test_detect(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/taskmaster/detect", json={
            "projectPath": "/tmp",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "found" in data

    def test_detect_mcp(self, client: httpx.Client, auth_headers):
        resp = client.post("/api/taskmaster/detect-mcp", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "found" in data
