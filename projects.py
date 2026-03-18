"""Project discovery and session reading — Python port of server/projects.js.

Supports Claude and Codex providers only.

Claude:  ~/.claude/projects/{encoded-path}/*.jsonl
Codex:   ~/.codex/sessions/**/*.jsonl
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()
CLAUDE_PROJECTS_DIR = HOME / ".claude" / "projects"
CLAUDE_CONFIG_PATH = HOME / ".claude" / "project-config.json"
CODEX_SESSIONS_DIR = HOME / ".codex" / "sessions"


# ---------------------------------------------------------------------------
# Project-directory extraction cache
# ---------------------------------------------------------------------------

_project_dir_cache: dict[str, str] = {}


def clear_project_directory_cache():
    _project_dir_cache.clear()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

async def load_project_config() -> dict:
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, CLAUDE_CONFIG_PATH.read_text, "utf-8")
        return json.loads(text)
    except Exception:
        return {}


async def save_project_config(config: dict):
    CLAUDE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config, indent=2)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, CLAUDE_CONFIG_PATH.write_text, text, "utf-8")


# ---------------------------------------------------------------------------
# Display-name helper
# ---------------------------------------------------------------------------

async def generate_display_name(project_name: str, actual_project_dir: Optional[str] = None) -> str:
    project_path = actual_project_dir or project_name.replace("-", "/")

    # Try package.json
    try:
        pkg_path = Path(project_path) / "package.json"
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, pkg_path.read_text, "utf-8")
        pkg = json.loads(text)
        if pkg.get("name"):
            return pkg["name"]
    except Exception:
        pass

    # Absolute path → last component
    if project_path.startswith("/"):
        parts = [p for p in project_path.split("/") if p]
        return parts[-1] if parts else project_path

    return project_path


# ---------------------------------------------------------------------------
# Extract actual project directory from JSONL cwd fields
# ---------------------------------------------------------------------------

async def _read_lines_from_file(file_path: Path):
    """Yield non-empty lines from a text file."""
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, file_path.read_text, "utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield line


async def extract_project_directory(project_name: str) -> str:
    if project_name in _project_dir_cache:
        return _project_dir_cache[project_name]

    # Check config for originalPath (handles dashes in real path)
    raw_config = await load_project_config()
    config_by_name = {p.get("name", ""): p for p in raw_config.get("projects", [])}
    original_path = config_by_name.get(project_name, {}).get("originalPath")
    if original_path:
        _project_dir_cache[project_name] = original_path
        return original_path

    project_dir = CLAUDE_PROJECTS_DIR / project_name
    fallback = project_name.replace("-", "/")

    try:
        if not project_dir.is_dir():
            raise FileNotFoundError

        jsonl_files = [f for f in project_dir.iterdir() if f.suffix == ".jsonl"]
        if not jsonl_files:
            _project_dir_cache[project_name] = fallback
            return fallback

        cwd_counts: dict[str, int] = {}
        latest_timestamp = 0
        latest_cwd: Optional[str] = None

        for jf in jsonl_files:
            try:
                async for line in _read_lines_from_file(jf):
                    try:
                        entry = json.loads(line)
                        cwd = entry.get("cwd")
                        if cwd:
                            cwd_counts[cwd] = cwd_counts.get(cwd, 0) + 1
                            ts = 0
                            raw_ts = entry.get("timestamp")
                            if raw_ts:
                                try:
                                    from datetime import datetime
                                    dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                                    ts = dt.timestamp()
                                except Exception:
                                    pass
                            if ts > latest_timestamp:
                                latest_timestamp = ts
                                latest_cwd = cwd
                    except Exception:
                        pass
            except Exception:
                pass

        if not cwd_counts:
            extracted = fallback
        elif len(cwd_counts) == 1:
            extracted = next(iter(cwd_counts))
        else:
            max_count = max(cwd_counts.values())
            recent_count = cwd_counts.get(latest_cwd, 0) if latest_cwd else 0
            if latest_cwd and recent_count >= max_count * 0.25:
                extracted = latest_cwd
            else:
                extracted = max(cwd_counts, key=lambda k: cwd_counts[k])

        _project_dir_cache[project_name] = extracted
        return extracted

    except Exception:
        _project_dir_cache[project_name] = fallback
        return fallback


# ---------------------------------------------------------------------------
# getProjects
# ---------------------------------------------------------------------------

async def get_projects(progress_callback=None) -> list[dict]:
    raw_config = await load_project_config()
    # Normalize: convert {"projects": [{name, path, ...}]} to {name: {path, ...}}
    config: dict[str, dict] = {}
    for p in raw_config.get("projects", []):
        key = p.get("name") or p.get("path", "")
        if key:
            config[key] = p
    projects: list[dict] = []
    existing_projects: set[str] = set()

    # Shared Codex index across all projects (built once)
    codex_index_ref: dict[str, Any] = {"sessions_by_project": None}

    directories: list[Path] = []

    try:
        if CLAUDE_PROJECTS_DIR.is_dir():
            directories = [e for e in CLAUDE_PROJECTS_DIR.iterdir() if e.is_dir()]
    except Exception:
        pass

    for d in directories:
        existing_projects.add(d.name)

    # Count manual-only projects for progress
    manual_only = [
        name for name, cfg in config.items()
        if cfg.get("manuallyAdded") and name not in existing_projects
    ]
    total_projects = len(directories) + len(manual_only)
    processed = 0

    # Process directories
    for entry in directories:
        processed += 1
        if progress_callback:
            progress_callback({
                "phase": "loading",
                "current": processed,
                "total": total_projects,
                "currentProject": entry.name,
            })

        actual_dir = await extract_project_directory(entry.name)
        custom_name = config.get(entry.name, {}).get("displayName")
        auto_name = await generate_display_name(entry.name, actual_dir)

        project: dict = {
            "name": entry.name,
            "path": actual_dir,
            "displayName": custom_name or auto_name,
            "fullPath": actual_dir,
            "isCustomName": bool(custom_name),
            "sessions": [],
            "codexSessions": [],
            "sessionMeta": {"hasMore": False, "total": 0},
        }

        try:
            session_result = await get_sessions(entry.name, 5, 0)
            project["sessions"] = session_result.get("sessions", [])
            project["sessionMeta"] = {
                "hasMore": session_result.get("hasMore", False),
                "total": session_result.get("total", 0),
            }
        except Exception:
            pass

        try:
            project["codexSessions"] = await get_codex_sessions(actual_dir, index_ref=codex_index_ref)
        except Exception:
            project["codexSessions"] = []

        projects.append(project)

    # Process manually-added projects not in directories
    for project_name in manual_only:
        processed += 1
        project_config = config[project_name]

        if progress_callback:
            progress_callback({
                "phase": "loading",
                "current": processed,
                "total": total_projects,
                "currentProject": project_name,
            })

        actual_dir = project_config.get("originalPath")
        if not actual_dir:
            try:
                actual_dir = await extract_project_directory(project_name)
            except Exception:
                actual_dir = project_name.replace("-", "/")

        project: dict = {
            "name": project_name,
            "path": actual_dir,
            "displayName": project_config.get("displayName") or await generate_display_name(project_name, actual_dir),
            "fullPath": actual_dir,
            "isCustomName": bool(project_config.get("displayName")),
            "isManuallyAdded": True,
            "sessions": [],
            "codexSessions": [],
            "sessionMeta": {"hasMore": False, "total": 0},
        }

        try:
            project["codexSessions"] = await get_codex_sessions(actual_dir, index_ref=codex_index_ref)
        except Exception:
            project["codexSessions"] = []

        projects.append(project)

    if progress_callback:
        progress_callback({"phase": "complete", "current": total_projects, "total": total_projects})

    return projects


# ---------------------------------------------------------------------------
# getSessions
# ---------------------------------------------------------------------------

async def get_sessions(project_name: str, limit: int = 5, offset: int = 0) -> dict:
    project_dir = CLAUDE_PROJECTS_DIR / project_name

    try:
        if not project_dir.is_dir():
            return {"sessions": [], "hasMore": False, "total": 0}

        jsonl_files = [
            f for f in project_dir.iterdir()
            if f.suffix == ".jsonl" and not f.name.startswith("agent-")
        ]

        if not jsonl_files:
            return {"sessions": [], "hasMore": False, "total": 0}

        # Sort newest-first by mtime
        loop = asyncio.get_event_loop()

        async def _stat(f: Path):
            st = await loop.run_in_executor(None, f.stat)
            return f, st.st_mtime

        stats = await asyncio.gather(*[_stat(f) for f in jsonl_files])
        stats.sort(key=lambda x: x[1], reverse=True)

        all_sessions: dict[str, dict] = {}
        all_entries: list[dict] = []
        uuid_to_session: dict[str, str] = {}

        for file, _ in stats:
            result = await _parse_jsonl_sessions(file)
            for session in result["sessions"]:
                if session["id"] not in all_sessions:
                    all_sessions[session["id"]] = session
            all_entries.extend(result["entries"])

            # Early exit
            if len(all_sessions) >= (limit + offset) * 2 and len(stats) >= 3:
                break

        # Build uuid -> session mapping
        for entry in all_entries:
            if entry.get("uuid") and entry.get("sessionId"):
                uuid_to_session[entry["uuid"]] = entry["sessionId"]

        # Group sessions by first user message (parentUuid == null)
        session_groups: dict[str, dict] = {}  # firstUserMsgId -> {latestSession, allSessions}
        session_to_first: dict[str, str] = {}  # sessionId -> firstUserMsgId

        for entry in all_entries:
            sid = entry.get("sessionId")
            if (sid and entry.get("type") == "user"
                    and entry.get("parentUuid") is None
                    and entry.get("uuid")):
                first_id = entry["uuid"]
                if sid not in session_to_first:
                    session_to_first[sid] = first_id
                    session = all_sessions.get(sid)
                    if session:
                        if first_id not in session_groups:
                            session_groups[first_id] = {
                                "latestSession": session,
                                "allSessions": [session],
                            }
                        else:
                            group = session_groups[first_id]
                            group["allSessions"].append(session)
                            if _parse_dt(session["lastActivity"]) > _parse_dt(group["latestSession"]["lastActivity"]):
                                group["latestSession"] = session

        grouped_ids: set[str] = set()
        for group in session_groups.values():
            for s in group["allSessions"]:
                grouped_ids.add(s["id"])

        standalone = [s for s in all_sessions.values() if s["id"] not in grouped_ids]

        latest_from_groups = []
        for group in session_groups.values():
            s = dict(group["latestSession"])
            if len(group["allSessions"]) > 1:
                s["isGrouped"] = True
                s["groupSize"] = len(group["allSessions"])
                s["groupSessions"] = [x["id"] for x in group["allSessions"]]
            latest_from_groups.append(s)

        visible = [
            s for s in (latest_from_groups + standalone)
            if not s.get("summary", "").startswith('{ "')
        ]
        visible.sort(key=lambda s: _parse_dt(s["lastActivity"]), reverse=True)

        total = len(visible)
        paginated = visible[offset: offset + limit]
        has_more = offset + limit < total

        return {
            "sessions": paginated,
            "hasMore": has_more,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    except Exception:
        return {"sessions": [], "hasMore": False, "total": 0}


def _parse_dt(value) -> float:
    """Parse ISO timestamp or datetime to float for sorting."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime
        if isinstance(value, datetime):
            return value.timestamp()
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


async def _parse_jsonl_sessions(file_path: Path) -> dict:
    """Parse a JSONL file and return sessions + raw entries."""
    sessions: dict[str, dict] = {}
    entries: list[dict] = []
    pending_summaries: dict[str, str] = {}  # leafUuid -> summary

    _SYSTEM_PREFIXES = (
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "<local-command-stdout>",
        "<system-reminder>",
        "Caveat:",
        "This session is being continued from a previous",
        "Invalid API key",
    )
    _SYSTEM_SUBSTRINGS = ('{"subtasks":', 'CRITICAL: You MUST respond with ONLY a JSON')
    _SYSTEM_EXACT = {"Warmup"}

    def _is_system(text: str) -> bool:
        if not isinstance(text, str):
            return False
        if text in _SYSTEM_EXACT:
            return True
        if any(text.startswith(p) for p in _SYSTEM_PREFIXES):
            return True
        if any(s in text for s in _SYSTEM_SUBSTRINGS):
            return True
        return False

    def _extract_user_text(content) -> Optional[str]:
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content and content[0].get("type") == "text":
            return content[0].get("text")
        return None

    def _extract_assistant_text(content) -> Optional[str]:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "text" and part.get("text"):
                    return part["text"]
        return None

    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, file_path.read_text, "utf-8")
    except Exception:
        return {"sessions": [], "entries": []}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        entries.append(entry)

        # Summaries without sessionId (pending)
        if entry.get("type") == "summary" and entry.get("summary") and not entry.get("sessionId") and entry.get("leafUuid"):
            pending_summaries[entry["leafUuid"]] = entry["summary"]

        sid = entry.get("sessionId")
        if not sid:
            continue

        if sid not in sessions:
            sessions[sid] = {
                "id": sid,
                "summary": "New Session",
                "messageCount": 0,
                "lastActivity": entry.get("timestamp", ""),
                "cwd": entry.get("cwd", ""),
                "lastUserMessage": None,
                "lastAssistantMessage": None,
            }

        session = sessions[sid]

        # Apply pending summary
        if session["summary"] == "New Session" and entry.get("parentUuid") and entry["parentUuid"] in pending_summaries:
            session["summary"] = pending_summaries[entry["parentUuid"]]

        # Update summary from summary entries with sessionId
        if entry.get("type") == "summary" and entry.get("summary"):
            session["summary"] = entry["summary"]

        msg = entry.get("message", {})
        role = msg.get("role") if msg else None
        content = msg.get("content") if msg else None

        if role == "user" and content is not None:
            text_content = _extract_user_text(content)
            if text_content and not _is_system(text_content):
                session["lastUserMessage"] = text_content

        elif role == "assistant" and content is not None:
            if not entry.get("isApiErrorMessage"):
                assistant_text = _extract_assistant_text(content)
                if assistant_text and not _is_system(assistant_text):
                    session["lastAssistantMessage"] = assistant_text

        session["messageCount"] += 1

        ts = entry.get("timestamp")
        if ts:
            session["lastActivity"] = ts

    # Fill summary from last message if still "New Session"
    for session in sessions.values():
        if session["summary"] == "New Session":
            last_msg = session["lastUserMessage"] or session["lastAssistantMessage"]
            if last_msg:
                session["summary"] = last_msg[:50] + ("..." if len(last_msg) > 50 else "")

    filtered = [s for s in sessions.values() if not s.get("summary", "").startswith('{ "')]

    return {"sessions": filtered, "entries": entries}


# ---------------------------------------------------------------------------
# getSessionMessages
# ---------------------------------------------------------------------------

async def get_session_messages(
    project_name: str,
    session_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Any:
    project_dir = CLAUDE_PROJECTS_DIR / project_name

    try:
        if not project_dir.is_dir():
            return [] if limit is None else {"messages": [], "total": 0, "hasMore": False}

        jsonl_files = [
            f for f in project_dir.iterdir()
            if f.suffix == ".jsonl" and not f.name.startswith("agent-")
        ]
        agent_files = {
            f.name: f for f in project_dir.iterdir()
            if f.suffix == ".jsonl" and f.name.startswith("agent-")
        }

        if not jsonl_files:
            return [] if limit is None else {"messages": [], "total": 0, "hasMore": False}

        messages: list[dict] = []
        loop = asyncio.get_event_loop()

        for jf in jsonl_files:
            try:
                text = await loop.run_in_executor(None, jf.read_text, "utf-8")
            except Exception:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("sessionId") == session_id:
                        messages.append(entry)
                except Exception:
                    pass

        # Collect agentIds from Task tool results
        agent_ids: set[str] = set()
        for msg in messages:
            tur = msg.get("toolUseResult")
            aid = tur.get("agentId") if isinstance(tur, dict) else None
            if aid:
                agent_ids.add(aid)

        # Load agent tools
        agent_tools_cache: dict[str, list] = {}
        for agent_id in agent_ids:
            fname = f"agent-{agent_id}.jsonl"
            if fname in agent_files:
                tools = await _parse_agent_tools(agent_files[fname])
                agent_tools_cache[agent_id] = tools

        # Attach subagent tools
        for msg in messages:
            tur = msg.get("toolUseResult")
            aid = tur.get("agentId") if isinstance(tur, dict) else None
            if aid and aid in agent_tools_cache and agent_tools_cache[aid]:
                msg["subagentTools"] = agent_tools_cache[aid]

        # Sort by timestamp
        messages.sort(key=lambda m: _parse_dt(m.get("timestamp")))

        total = len(messages)

        if limit is None:
            return messages

        start = max(0, total - offset - limit)
        end = total - offset
        paginated = messages[start:end]
        has_more = start > 0

        return {
            "messages": paginated,
            "total": total,
            "hasMore": has_more,
            "offset": offset,
            "limit": limit,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return [] if limit is None else {"messages": [], "total": 0, "hasMore": False}


async def _parse_agent_tools(file_path: Path) -> list[dict]:
    tools: list[dict] = []
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, file_path.read_text, "utf-8")
    except Exception:
        return tools

    pending: dict[str, dict] = {}  # toolId -> tool dict

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            msg = entry.get("message", {})
            role = msg.get("role") if msg else None

            if role == "assistant" and isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if part.get("type") == "tool_use":
                        tool = {
                            "toolId": part.get("id"),
                            "toolName": part.get("name"),
                            "toolInput": part.get("input"),
                            "timestamp": entry.get("timestamp"),
                        }
                        tools.append(tool)
                        if tool["toolId"]:
                            pending[tool["toolId"]] = tool

            if role == "user" and isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if part.get("type") == "tool_result":
                        tid = part.get("tool_use_id")
                        if tid and tid in pending:
                            content = part.get("content", "")
                            if isinstance(content, list):
                                content = "\n".join(c.get("text", "") for c in content)
                            elif not isinstance(content, str):
                                content = json.dumps(content)
                            pending[tid]["toolResult"] = {
                                "content": content,
                                "isError": bool(part.get("is_error")),
                            }
        except Exception:
            pass

    return tools


# ---------------------------------------------------------------------------
# Codex helpers
# ---------------------------------------------------------------------------

def _normalize_path(p: str) -> str:
    """Normalize a path for comparison."""
    if not p or not isinstance(p, str):
        return ""
    return os.path.normpath(os.path.abspath(p.strip()))


def _is_visible_codex_user_message(payload: Optional[dict]) -> bool:
    if not payload or payload.get("type") != "user_message":
        return False
    if payload.get("kind") and payload["kind"] != "plain":
        return False
    msg = payload.get("message", "")
    return isinstance(msg, str) and msg.strip() != ""


async def _find_codex_jsonl_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    try:
        for entry in directory.iterdir():
            if entry.is_dir():
                files.extend(await _find_codex_jsonl_files(entry))
            elif entry.suffix == ".jsonl":
                files.append(entry)
    except Exception:
        pass
    return files


async def _parse_codex_session_file(file_path: Path) -> Optional[dict]:
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, file_path.read_text, "utf-8")
    except Exception:
        return None

    session_meta: Optional[dict] = None
    last_timestamp: Optional[str] = None
    last_user_message: Optional[str] = None
    message_count = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        if entry.get("timestamp"):
            last_timestamp = entry["timestamp"]

        if entry.get("type") == "session_meta" and entry.get("payload"):
            p = entry["payload"]
            session_meta = {
                "id": p.get("id"),
                "cwd": p.get("cwd"),
                "model": p.get("model") or p.get("model_provider"),
                "timestamp": entry.get("timestamp"),
                "git": p.get("git"),
            }

        if entry.get("type") == "event_msg" and _is_visible_codex_user_message(entry.get("payload")):
            message_count += 1
            msg = entry["payload"].get("message")
            if msg:
                last_user_message = msg

        if (entry.get("type") == "response_item"
                and entry.get("payload", {}).get("type") == "message"
                and entry.get("payload", {}).get("role") == "assistant"):
            message_count += 1

    if session_meta:
        summary = "Codex Session"
        if last_user_message:
            summary = last_user_message[:50] + ("..." if len(last_user_message) > 50 else "")
        return {
            **session_meta,
            "timestamp": last_timestamp or session_meta.get("timestamp"),
            "summary": summary,
            "messageCount": message_count,
        }

    return None


async def _build_codex_sessions_index() -> dict[str, list]:
    sessions_by_project: dict[str, list] = {}

    if not CODEX_SESSIONS_DIR.is_dir():
        return sessions_by_project

    jsonl_files = await _find_codex_jsonl_files(CODEX_SESSIONS_DIR)

    for file_path in jsonl_files:
        try:
            data = await _parse_codex_session_file(file_path)
            if not data or not data.get("id"):
                continue

            norm_cwd = _normalize_path(data.get("cwd", ""))
            if not norm_cwd:
                continue

            session = {
                "id": data["id"],
                "summary": data.get("summary", "Codex Session"),
                "messageCount": data.get("messageCount", 0),
                "lastActivity": data.get("timestamp") or "",
                "cwd": data.get("cwd"),
                "model": data.get("model"),
                "filePath": str(file_path),
                "provider": "codex",
            }

            if norm_cwd not in sessions_by_project:
                sessions_by_project[norm_cwd] = []
            sessions_by_project[norm_cwd].append(session)
        except Exception:
            pass

    for sessions in sessions_by_project.values():
        sessions.sort(key=lambda s: _parse_dt(s.get("lastActivity")), reverse=True)

    return sessions_by_project


async def get_codex_sessions(
    project_path: str,
    limit: int = 5,
    index_ref: Optional[dict] = None,
) -> list[dict]:
    """Return Codex sessions for a project path.

    index_ref: optional dict with key 'sessions_by_project' used to cache
    the full index across multiple calls (matches JS indexRef pattern).
    """
    try:
        norm = _normalize_path(project_path)
        if not norm:
            return []

        if index_ref is not None:
            if index_ref.get("sessions_by_project") is None:
                index_ref["sessions_by_project"] = await _build_codex_sessions_index()
            sessions_by_project = index_ref["sessions_by_project"]
        else:
            sessions_by_project = await _build_codex_sessions_index()

        sessions = sessions_by_project.get(norm, [])
        return sessions[:limit] if limit > 0 else list(sessions)

    except Exception:
        return []


async def get_codex_session_messages(
    session_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Any:
    """Return messages for a Codex session."""

    def _extract_codex_text(content) -> str:
        if not isinstance(content, list):
            return content or ""
        parts = []
        for item in content:
            if item.get("type") in ("input_text", "output_text", "text"):
                parts.append(item.get("text", ""))
        return "\n".join(filter(None, parts))

    try:
        if not CODEX_SESSIONS_DIR.is_dir():
            return {"messages": [], "total": 0, "hasMore": False}

        # Find the session file
        all_files = await _find_codex_jsonl_files(CODEX_SESSIONS_DIR)
        session_file: Optional[Path] = None
        for f in all_files:
            if session_id in f.name and f.suffix == ".jsonl":
                session_file = f
                break

        # Fallback: scan files for matching session_meta
        if session_file is None:
            for f in all_files:
                try:
                    loop = asyncio.get_event_loop()
                    text = await loop.run_in_executor(None, f.read_text, "utf-8")
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if (entry.get("type") == "session_meta"
                                    and entry.get("payload", {}).get("id") == session_id):
                                session_file = f
                                break
                        except Exception:
                            pass
                    if session_file:
                        break
                except Exception:
                    pass

        if session_file is None:
            return {"messages": [], "total": 0, "hasMore": False}

        messages: list[dict] = []
        token_usage: Optional[dict] = None

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, session_file.read_text, "utf-8")

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                payload = entry.get("payload") or {}
                entry_type = entry.get("type", "")

                # Token usage
                if (entry_type == "event_msg"
                        and payload.get("type") == "token_count"
                        and payload.get("info")):
                    info = payload["info"]
                    if info.get("total_token_usage"):
                        token_usage = {
                            "used": info["total_token_usage"].get("total_tokens", 0),
                            "total": info.get("model_context_window", 200000),
                        }

                # User message
                if entry_type == "event_msg" and _is_visible_codex_user_message(payload):
                    messages.append({
                        "type": "user",
                        "timestamp": entry.get("timestamp"),
                        "message": {"role": "user", "content": payload.get("message")},
                    })

                # Assistant message
                if (entry_type == "response_item"
                        and payload.get("type") == "message"
                        and payload.get("role") == "assistant"):
                    text_content = _extract_codex_text(payload.get("content"))
                    if text_content and text_content.strip():
                        messages.append({
                            "type": "assistant",
                            "timestamp": entry.get("timestamp"),
                            "message": {"role": "assistant", "content": text_content},
                        })

                # Reasoning / thinking
                if entry_type == "response_item" and payload.get("type") == "reasoning":
                    summary_text = "\n".join(
                        s.get("text", "") for s in (payload.get("summary") or [])
                    ).strip()
                    if summary_text:
                        messages.append({
                            "type": "thinking",
                            "timestamp": entry.get("timestamp"),
                            "message": {"role": "assistant", "content": summary_text},
                        })

                # Tool use (function_call)
                if entry_type == "response_item" and payload.get("type") == "function_call":
                    tool_name = payload.get("name", "")
                    tool_input = payload.get("arguments", "")
                    if tool_name == "shell_command":
                        tool_name = "Bash"
                        try:
                            args = json.loads(payload.get("arguments", "{}"))
                            tool_input = json.dumps({"command": args.get("command")})
                        except Exception:
                            pass
                    messages.append({
                        "type": "tool_use",
                        "timestamp": entry.get("timestamp"),
                        "toolName": tool_name,
                        "toolInput": tool_input,
                        "toolCallId": payload.get("call_id"),
                    })

                # Tool result (function_call_output)
                if entry_type == "response_item" and payload.get("type") == "function_call_output":
                    messages.append({
                        "type": "tool_result",
                        "timestamp": entry.get("timestamp"),
                        "toolCallId": payload.get("call_id"),
                        "output": payload.get("output", ""),
                    })

                # Custom tool call (apply_patch etc.)
                if entry_type == "response_item" and payload.get("type") == "custom_tool_call":
                    tool_name = payload.get("name", "custom_tool")
                    tool_input_raw = payload.get("input", "")
                    if tool_name == "apply_patch":
                        import re
                        m = re.search(r"\*\*\* Update File: (.+)", tool_input_raw)
                        file_path_str = m.group(1).strip() if m else "unknown"
                        patch_lines = tool_input_raw.split("\n")
                        old_lines, new_lines = [], []
                        for pl in patch_lines:
                            if pl.startswith("-") and not pl.startswith("---"):
                                old_lines.append(pl[1:])
                            elif pl.startswith("+") and not pl.startswith("+++"):
                                new_lines.append(pl[1:])
                        messages.append({
                            "type": "tool_use",
                            "timestamp": entry.get("timestamp"),
                            "toolName": "Edit",
                            "toolInput": json.dumps({
                                "file_path": file_path_str,
                                "old_string": "\n".join(old_lines),
                                "new_string": "\n".join(new_lines),
                            }),
                            "toolCallId": payload.get("call_id"),
                        })
                    else:
                        messages.append({
                            "type": "tool_use",
                            "timestamp": entry.get("timestamp"),
                            "toolName": tool_name,
                            "toolInput": tool_input_raw,
                            "toolCallId": payload.get("call_id"),
                        })

                # Custom tool output
                if entry_type == "response_item" and payload.get("type") == "custom_tool_call_output":
                    messages.append({
                        "type": "tool_result",
                        "timestamp": entry.get("timestamp"),
                        "toolCallId": payload.get("call_id"),
                        "output": payload.get("output", ""),
                    })

            except Exception:
                pass

        messages.sort(key=lambda m: _parse_dt(m.get("timestamp")))
        total = len(messages)

        if limit is not None:
            start = max(0, total - offset - limit)
            end = total - offset
            return {
                "messages": messages[start:end],
                "total": total,
                "hasMore": start > 0,
                "offset": offset,
                "limit": limit,
                "tokenUsage": token_usage,
            }

        return {"messages": messages, "tokenUsage": token_usage}

    except Exception:
        return {"messages": [], "total": 0, "hasMore": False}


async def delete_codex_session(session_id: str):
    """Delete a specific Codex session JSONL file."""
    if not CODEX_SESSIONS_DIR.is_dir():
        raise FileNotFoundError(f"Codex sessions directory not found: {CODEX_SESSIONS_DIR}")

    all_files = await _find_codex_jsonl_files(CODEX_SESSIONS_DIR)

    loop = asyncio.get_event_loop()

    def _matches_session(file_path: Path) -> bool:
        if session_id in file_path.name and file_path.suffix == ".jsonl":
            return True

        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return False

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if (
                entry.get("type") == "session_meta"
                and entry.get("payload", {}).get("id") == session_id
            ):
                return True
        return False

    def _do_delete() -> bool:
        for file_path in all_files:
            if _matches_session(file_path):
                file_path.unlink()
                return True
        return False

    deleted = await loop.run_in_executor(None, _do_delete)
    if not deleted:
        raise FileNotFoundError(f"Codex session not found: {session_id}")


# ---------------------------------------------------------------------------
# Delete operations
# ---------------------------------------------------------------------------

async def delete_session(project_name: str, session_id: str):
    """Delete a specific session's JSONL file."""
    project_dir = CLAUDE_PROJECTS_DIR / project_name
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project not found: {project_name}")

    loop = asyncio.get_event_loop()

    def _do_delete():
        # Find and delete the session file
        for f in sorted(project_dir.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    first_line = fh.readline().strip()
                    if first_line:
                        entry = json.loads(first_line)
                        if entry.get("sessionId") == session_id:
                            f.unlink()
                            return True
            except Exception:
                continue

            # Also check filename pattern
            if session_id in f.stem:
                f.unlink()
                return True

        raise FileNotFoundError(f"Session not found: {session_id}")

    await loop.run_in_executor(None, _do_delete)


async def delete_project(project_name: str, force: bool = False):
    """Delete a project directory from ~/.claude/projects/."""
    import shutil

    project_dir = CLAUDE_PROJECTS_DIR / project_name
    if not project_dir.is_dir():
        # Try removing from manual config
        config = _load_manual_config()
        projects = config.get("projects", [])
        config["projects"] = [p for p in projects if p.get("name") != project_name]
        _save_manual_config(config)
        return

    if not force:
        # Check if has sessions
        sessions = list(project_dir.glob("*.jsonl"))
        if sessions:
            raise ValueError("Project has sessions. Use force=true to delete.")

    shutil.rmtree(project_dir, ignore_errors=True)

    # Also remove from manual config
    config = _load_manual_config()
    projects = config.get("projects", [])
    config["projects"] = [p for p in projects if p.get("name") != project_name]
    _save_manual_config(config)


def _load_manual_config() -> dict:
    try:
        return json.loads(CLAUDE_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"projects": []}


def _save_manual_config(config: dict):
    CLAUDE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
