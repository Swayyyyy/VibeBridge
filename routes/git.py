"""Git routes — 1:1 port of server/routes/git.js.

Removed Cursor references. AI commit message generation is a stub until Phase 3.
"""
import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth import authenticate_token

router = APIRouter(prefix="/api/git", tags=["git"])

COMMIT_DIFF_CHARACTER_LIMIT = 500_000


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

async def _git(*args: str, cwd: str) -> str:
    """Run a git command and return stdout. Raises on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        raise RuntimeError(err_msg or f"git {' '.join(args)} failed with code {proc.returncode}")
    return stdout.decode(errors="replace")


# ---------------------------------------------------------------------------
# Validation helpers (defense-in-depth)
# ---------------------------------------------------------------------------

def _validate_commit_ref(commit: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._~^{}@/\-]+$", commit):
        raise ValueError("Invalid commit reference")
    return commit


def _validate_branch_name(branch: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._/\-]+$", branch):
        raise ValueError("Invalid branch name")
    return branch


def _validate_file_path(file: str, project_path: Optional[str] = None) -> str:
    if not file or "\0" in file:
        raise ValueError("Invalid file path")
    if project_path:
        resolved = os.path.abspath(os.path.join(project_path, file))
        root = os.path.abspath(project_path) + os.sep
        if not resolved.startswith(root) and resolved != os.path.abspath(project_path):
            raise ValueError("Invalid file path: path traversal detected")
    return file


def _validate_remote_name(remote: str) -> str:
    if not re.match(r"^[a-zA-Z0-9._\-]+$", remote):
        raise ValueError("Invalid remote name")
    return remote


def _validate_project_path(p: str) -> str:
    if not p or "\0" in p:
        raise ValueError("Invalid project path")
    resolved = os.path.abspath(p)
    if not os.path.isabs(resolved):
        raise ValueError("Invalid project path: must be absolute")
    if resolved in ("/", os.sep):
        raise ValueError("Invalid project path: root directory not allowed")
    return resolved


# ---------------------------------------------------------------------------
# Project path resolution
# ---------------------------------------------------------------------------

async def _get_project_path(project: str) -> str:
    """Resolve project name to actual path.

    For now, treat the project param as a literal path.
    Full encoded-name resolution comes in Phase 5 (projects.py).
    """
    # Try literal path first
    if os.path.isdir(project):
        return _validate_project_path(project)

    # Try decoding Claude-style encoded name (/ replaced with -)
    decoded = "/" + project.lstrip("-").replace("-", "/")
    if os.path.isdir(decoded):
        return _validate_project_path(decoded)

    raise ValueError(f'Unable to resolve project path for "{project}"')


# ---------------------------------------------------------------------------
# Git repository helpers
# ---------------------------------------------------------------------------

async def _validate_git_repo(path: str):
    if not os.path.exists(path):
        raise RuntimeError(f"Project path not found: {path}")
    out = await _git("rev-parse", "--is-inside-work-tree", cwd=path)
    if out.strip() != "true":
        raise RuntimeError("Not inside a git work tree")
    await _git("rev-parse", "--show-toplevel", cwd=path)


async def _current_branch(path: str) -> str:
    try:
        out = await _git("symbolic-ref", "--short", "HEAD", cwd=path)
        if out.strip():
            return out.strip()
    except Exception:
        pass
    out = await _git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    return out.strip()


async def _has_commits(path: str) -> bool:
    try:
        await _git("rev-parse", "--verify", "HEAD", cwd=path)
        return True
    except Exception as e:
        msg = str(e).lower()
        if "unknown revision" in msg or "ambiguous argument" in msg or "bad revision" in msg:
            return False
        raise


async def _repo_root(path: str) -> str:
    return (await _git("rev-parse", "--show-toplevel", cwd=path)).strip()


def _normalize_path(fp: str) -> str:
    return fp.replace("\\", "/").lstrip("./").lstrip("/").strip()


def _parse_status_paths(output: str) -> list:
    paths = []
    for line in output.split("\n"):
        line = line.rstrip()
        if not line.strip():
            continue
        sp = line[3:]
        renamed = sp.split(" -> ")
        paths.append(_normalize_path(renamed[1] if len(renamed) > 1 else sp))
    return [p for p in paths if p]


def _build_candidates(project_path: str, repo_root: str, fp: str) -> list:
    norm = _normalize_path(fp)
    proj_rel = _normalize_path(os.path.relpath(project_path, repo_root))
    candidates = [norm]
    if proj_rel and proj_rel != "." and not norm.startswith(f"{proj_rel}/"):
        candidates.append(f"{proj_rel}/{norm}")
    return list(dict.fromkeys(c for c in candidates if c))


async def _resolve_repo_file(project_path: str, fp: str) -> tuple:
    _validate_file_path(fp)
    root = await _repo_root(project_path)
    candidates = _build_candidates(project_path, root, fp)
    for c in candidates:
        out = await _git("status", "--porcelain", "--", c, cwd=root)
        if out.strip():
            return root, c
    norm = _normalize_path(fp)
    if "/" not in norm:
        out = await _git("status", "--porcelain", cwd=root)
        changed = _parse_status_paths(out)
        matches = [p for p in changed if p == norm or p.endswith(f"/{norm}")]
        if len(matches) == 1:
            return root, matches[0]
    return root, candidates[0]


def _strip_diff_headers(diff: str) -> str:
    if not diff:
        return ""
    lines = diff.split("\n")
    result = []
    started = False
    for line in lines:
        if any(line.startswith(p) for p in ("diff --git", "index ", "new file mode", "deleted file mode", "---", "+++")):
            continue
        if line.startswith("@@") or started:
            started = True
            result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class ProjectBody(BaseModel):
    project: str


class CommitBody(BaseModel):
    project: str
    message: str
    files: list


class BranchBody(BaseModel):
    project: str
    branch: str


class FileBody(BaseModel):
    project: str
    file: str


class GenCommitBody(BaseModel):
    project: str
    files: list
    provider: str = "claude"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def git_status(project: str = "", _=Depends(authenticate_token)):
    if not project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        has_commits = await _has_commits(pp)
        out = await _git("status", "--porcelain", cwd=pp)

        modified, added, deleted, untracked = [], [], [], []
        for line in out.split("\n"):
            if not line.strip():
                continue
            st = line[:2]
            f = line[3:]
            if st in ("M ", " M", "MM"):
                modified.append(f)
            elif st in ("A ", "AM"):
                added.append(f)
            elif st in ("D ", " D"):
                deleted.append(f)
            elif st == "??":
                untracked.append(f)

        return {"branch": branch, "hasCommits": has_commits, "modified": modified, "added": added, "deleted": deleted, "untracked": untracked}
    except Exception as e:
        msg = str(e)
        is_not_repo = "not a git repository" in msg.lower() or "not inside a git work tree" in msg.lower()
        return {"error": msg if is_not_repo else "Git operation failed", "details": msg}


@router.get("/diff")
async def git_diff(project: str = "", file: str = "", _=Depends(authenticate_token)):
    if not project or not file:
        raise HTTPException(400, "Project name and file path are required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        root, rel = await _resolve_repo_file(pp, file)

        status_out = await _git("status", "--porcelain", "--", rel, cwd=root)
        is_untracked = status_out.startswith("??")
        is_deleted = status_out.strip().startswith("D ") or status_out.strip().startswith(" D")

        if is_untracked:
            fp = os.path.join(root, rel)
            if os.path.isdir(fp):
                diff = f"Directory: {rel}\n(Cannot show diff for directories)"
            else:
                content = Path(fp).read_text(encoding="utf-8", errors="replace")
                lines = content.split("\n")
                diff = f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{l}" for l in lines)
        elif is_deleted:
            content = await _git("show", f"HEAD:{rel}", cwd=root)
            lines = content.split("\n")
            diff = f"--- a/{rel}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n" + "\n".join(f"-{l}" for l in lines)
        else:
            unstaged = await _git("diff", "--", rel, cwd=root)
            if unstaged:
                diff = _strip_diff_headers(unstaged)
            else:
                staged = await _git("diff", "--cached", "--", rel, cwd=root)
                diff = _strip_diff_headers(staged)
        return {"diff": diff}
    except Exception as e:
        return {"error": str(e)}


@router.get("/file-with-diff")
async def file_with_diff(project: str = "", file: str = "", _=Depends(authenticate_token)):
    if not project or not file:
        raise HTTPException(400, "Project name and file path are required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        root, rel = await _resolve_repo_file(pp, file)

        status_out = await _git("status", "--porcelain", "--", rel, cwd=root)
        is_untracked = status_out.startswith("??")
        is_deleted = status_out.strip().startswith("D ") or status_out.strip().startswith(" D")

        current_content = ""
        old_content = ""

        if is_deleted:
            old_content = await _git("show", f"HEAD:{rel}", cwd=root)
            current_content = old_content
        else:
            fp = os.path.join(root, rel)
            if os.path.isdir(fp):
                raise HTTPException(400, "Cannot show diff for directories")
            current_content = Path(fp).read_text(encoding="utf-8", errors="replace")
            if not is_untracked:
                try:
                    old_content = await _git("show", f"HEAD:{rel}", cwd=root)
                except Exception:
                    old_content = ""

        return {"currentContent": current_content, "oldContent": old_content, "isDeleted": is_deleted, "isUntracked": is_untracked}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@router.post("/initial-commit")
async def initial_commit(body: ProjectBody, _=Depends(authenticate_token)):
    if not body.project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        if await _has_commits(pp):
            raise HTTPException(400, "Repository already has commits. Use regular commit instead.")
        await _git("add", ".", cwd=pp)
        out = await _git("commit", "-m", "Initial commit", cwd=pp)
        return {"success": True, "output": out, "message": "Initial commit created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        if "nothing to commit" in str(e):
            raise HTTPException(400, "Nothing to commit")
        raise HTTPException(500, str(e))


@router.post("/commit")
async def commit(body: CommitBody, _=Depends(authenticate_token)):
    if not body.project or not body.message or not body.files:
        raise HTTPException(400, "Project name, commit message, and files are required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        root = await _repo_root(pp)
        for f in body.files:
            _, rel = await _resolve_repo_file(pp, f)
            await _git("add", "--", rel, cwd=root)
        out = await _git("commit", "-m", body.message, cwd=root)
        return {"success": True, "output": out}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/revert-local-commit")
async def revert_local_commit(body: ProjectBody, _=Depends(authenticate_token)):
    if not body.project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        if not await _has_commits(pp):
            raise HTTPException(400, "No local commit to revert")
        try:
            await _git("reset", "--soft", "HEAD~1", cwd=pp)
        except Exception as e:
            msg = str(e)
            if "HEAD~1" in msg and ("unknown revision" in msg or "ambiguous argument" in msg):
                await _git("update-ref", "-d", "HEAD", cwd=pp)
            else:
                raise
        return {"success": True, "output": "Latest local commit reverted successfully. Changes were kept staged."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/branches")
async def branches(project: str = "", _=Depends(authenticate_token)):
    if not project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        out = await _git("branch", "-a", cwd=pp)
        seen = set()
        result = []
        for line in out.split("\n"):
            b = line.strip()
            if not b or "->" in b:
                continue
            if b.startswith("* "):
                b = b[2:]
            if b.startswith("remotes/origin/"):
                b = b[15:]
            if b not in seen:
                seen.add(b)
                result.append(b)
        return {"branches": result}
    except Exception as e:
        return {"error": str(e)}


@router.post("/checkout")
async def checkout(body: BranchBody, _=Depends(authenticate_token)):
    if not body.project or not body.branch:
        raise HTTPException(400, "Project name and branch are required")
    try:
        pp = await _get_project_path(body.project)
        _validate_branch_name(body.branch)
        out = await _git("checkout", body.branch, cwd=pp)
        return {"success": True, "output": out}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/create-branch")
async def create_branch(body: BranchBody, _=Depends(authenticate_token)):
    if not body.project or not body.branch:
        raise HTTPException(400, "Project name and branch name are required")
    try:
        pp = await _get_project_path(body.project)
        _validate_branch_name(body.branch)
        out = await _git("checkout", "-b", body.branch, cwd=pp)
        return {"success": True, "output": out}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/commits")
async def commits(project: str = "", limit: int = 10, _=Depends(authenticate_token)):
    if not project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        safe_limit = min(max(limit, 1), 100)
        out = await _git("log", "--pretty=format:%H|%an|%ae|%ad|%s", "--date=relative", "-n", str(safe_limit), cwd=pp)
        result = []
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            h, author, email, date = parts[0], parts[1], parts[2], parts[3]
            msg = "|".join(parts[4:])
            stats = ""
            try:
                sout = await _git("show", "--stat", "--format=", h, cwd=pp)
                lines = sout.strip().split("\n")
                stats = lines[-1] if lines else ""
            except Exception:
                pass
            result.append({"hash": h, "author": author, "email": email, "date": date, "message": msg, "stats": stats})
        return {"commits": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/commit-diff")
async def commit_diff(project: str = "", commit: str = "", _=Depends(authenticate_token)):
    if not project or not commit:
        raise HTTPException(400, "Project name and commit hash are required")
    try:
        pp = await _get_project_path(project)
        _validate_commit_ref(commit)
        out = await _git("show", commit, cwd=pp)
        truncated = len(out) > COMMIT_DIFF_CHARACTER_LIMIT
        diff = out[:COMMIT_DIFF_CHARACTER_LIMIT] + "\n\n... Diff truncated ..." if truncated else out
        return {"diff": diff, "isTruncated": truncated}
    except Exception as e:
        return {"error": str(e)}


@router.post("/generate-commit-message")
async def generate_commit_message(body: GenCommitBody, _=Depends(authenticate_token)):
    """Stub — full AI integration comes in Phase 3."""
    if not body.project or not body.files:
        raise HTTPException(400, "Project name and files are required")
    # Simple fallback until Claude SDK is wired up
    n = len(body.files)
    return {"message": f"chore: update {n} file{'s' if n != 1 else ''}"}


@router.get("/remote-status")
async def remote_status(project: str = "", _=Depends(authenticate_token)):
    if not project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        has_commits = await _has_commits(pp)

        remote_out = await _git("remote", cwd=pp)
        remotes = [r for r in remote_out.strip().split("\n") if r.strip()]
        has_remote = len(remotes) > 0
        fallback = ("origin" if "origin" in remotes else remotes[0]) if has_remote else None

        if not has_commits:
            return {"hasRemote": has_remote, "hasUpstream": False, "branch": branch, "remoteName": fallback, "ahead": 0, "behind": 0, "isUpToDate": False, "message": "Repository has no commits yet"}

        try:
            tracking_out = await _git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=pp)
            tracking = tracking_out.strip()
            remote_name = tracking.split("/")[0]
        except Exception:
            return {"hasRemote": has_remote, "hasUpstream": False, "branch": branch, "remoteName": fallback, "message": "No remote tracking branch configured"}

        count_out = await _git("rev-list", "--count", "--left-right", f"{tracking}...HEAD", cwd=pp)
        parts = count_out.strip().split("\t")
        behind = int(parts[0]) if parts[0] else 0
        ahead = int(parts[1]) if len(parts) > 1 and parts[1] else 0

        return {"hasRemote": True, "hasUpstream": True, "branch": branch, "remoteBranch": tracking, "remoteName": remote_name, "ahead": ahead, "behind": behind, "isUpToDate": ahead == 0 and behind == 0}
    except Exception as e:
        return {"error": str(e)}


@router.post("/fetch")
async def fetch(body: ProjectBody, _=Depends(authenticate_token)):
    if not body.project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name = "origin"
        try:
            out = await _git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=pp)
            remote_name = out.strip().split("/")[0]
        except Exception:
            pass
        _validate_remote_name(remote_name)
        out = await _git("fetch", remote_name, cwd=pp)
        return {"success": True, "output": out or "Fetch completed successfully", "remoteName": remote_name}
    except Exception as e:
        msg = str(e)
        if "Could not resolve hostname" in msg:
            detail = "Unable to connect to remote repository. Check your internet connection."
        elif "does not appear to be a git repository" in msg:
            detail = "No remote repository configured."
        else:
            detail = msg
        raise HTTPException(500, detail)


@router.post("/pull")
async def pull(body: ProjectBody, _=Depends(authenticate_token)):
    if not body.project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name, remote_branch = "origin", branch
        try:
            out = await _git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=pp)
            tracking = out.strip()
            remote_name = tracking.split("/")[0]
            remote_branch = "/".join(tracking.split("/")[1:])
        except Exception:
            pass
        _validate_remote_name(remote_name)
        _validate_branch_name(remote_branch)
        out = await _git("pull", remote_name, remote_branch, cwd=pp)
        return {"success": True, "output": out or "Pull completed successfully", "remoteName": remote_name, "remoteBranch": remote_branch}
    except Exception as e:
        msg = str(e)
        if "CONFLICT" in msg:
            raise HTTPException(500, "Merge conflicts detected. Please resolve conflicts manually.")
        elif "Please commit your changes" in msg:
            raise HTTPException(500, "Uncommitted changes detected. Commit or stash first.")
        raise HTTPException(500, msg)


@router.post("/push")
async def push(body: ProjectBody, _=Depends(authenticate_token)):
    if not body.project:
        raise HTTPException(400, "Project name is required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        branch = await _current_branch(pp)
        remote_name, remote_branch = "origin", branch
        try:
            out = await _git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=pp)
            tracking = out.strip()
            remote_name = tracking.split("/")[0]
            remote_branch = "/".join(tracking.split("/")[1:])
        except Exception:
            pass
        _validate_remote_name(remote_name)
        _validate_branch_name(remote_branch)
        out = await _git("push", remote_name, remote_branch, cwd=pp)
        return {"success": True, "output": out or "Push completed successfully", "remoteName": remote_name, "remoteBranch": remote_branch}
    except Exception as e:
        msg = str(e)
        if "rejected" in msg:
            raise HTTPException(500, "Push rejected. Pull first to merge changes.")
        elif "Permission denied" in msg:
            raise HTTPException(500, "Authentication failed. Check your credentials.")
        raise HTTPException(500, msg)


@router.post("/publish")
async def publish(body: BranchBody, _=Depends(authenticate_token)):
    if not body.project or not body.branch:
        raise HTTPException(400, "Project name and branch are required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        _validate_branch_name(body.branch)
        current = await _current_branch(pp)
        if current != body.branch:
            raise HTTPException(400, f"Branch mismatch. Current branch is {current}, but trying to publish {body.branch}")

        remote_out = await _git("remote", cwd=pp)
        remotes = [r for r in remote_out.strip().split("\n") if r.strip()]
        if not remotes:
            raise HTTPException(400, "No remote repository configured.")
        remote_name = "origin" if "origin" in remotes else remotes[0]
        _validate_remote_name(remote_name)
        out = await _git("push", "--set-upstream", remote_name, body.branch, cwd=pp)
        return {"success": True, "output": out or "Branch published successfully", "remoteName": remote_name, "branch": body.branch}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/discard")
async def discard(body: FileBody, _=Depends(authenticate_token)):
    if not body.project or not body.file:
        raise HTTPException(400, "Project name and file path are required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        root, rel = await _resolve_repo_file(pp, body.file)

        status_out = await _git("status", "--porcelain", "--", rel, cwd=root)
        if not status_out.strip():
            raise HTTPException(400, "No changes to discard for this file")

        st = status_out[:2]
        if st == "??":
            fp = os.path.join(root, rel)
            if os.path.isdir(fp):
                import shutil
                shutil.rmtree(fp, ignore_errors=True)
            else:
                os.unlink(fp)
        elif "M" in st or "D" in st:
            await _git("restore", "--", rel, cwd=root)
        elif "A" in st:
            await _git("reset", "HEAD", "--", rel, cwd=root)

        return {"success": True, "message": f"Changes discarded for {rel}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/delete-untracked")
async def delete_untracked(body: FileBody, _=Depends(authenticate_token)):
    if not body.project or not body.file:
        raise HTTPException(400, "Project name and file path are required")
    try:
        pp = await _get_project_path(body.project)
        await _validate_git_repo(pp)
        root, rel = await _resolve_repo_file(pp, body.file)

        status_out = await _git("status", "--porcelain", "--", rel, cwd=root)
        if not status_out.strip():
            raise HTTPException(400, "File is not untracked or does not exist")
        if status_out[:2] != "??":
            raise HTTPException(400, "File is not untracked. Use discard for tracked files.")

        fp = os.path.join(root, rel)
        if os.path.isdir(fp):
            import shutil
            shutil.rmtree(fp, ignore_errors=True)
            return {"success": True, "message": f"Untracked directory {rel} deleted successfully"}
        else:
            os.unlink(fp)
            return {"success": True, "message": f"Untracked file {rel} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
