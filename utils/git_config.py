"""Git configuration utilities — port of server/utils/gitConfig.js."""
import asyncio
from typing import Optional


async def _run(command: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        command, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{command} exited with {proc.returncode}")
    return stdout.decode().strip()


async def get_system_git_config() -> dict:
    """Read git name/email from system's global git config."""
    try:
        name_task = _run("git", "config", "--global", "user.name")
        email_task = _run("git", "config", "--global", "user.email")
        results = await asyncio.gather(name_task, email_task, return_exceptions=True)
        return {
            "git_name": results[0] if isinstance(results[0], str) and results[0] else None,
            "git_email": results[1] if isinstance(results[1], str) and results[1] else None,
        }
    except Exception:
        return {"git_name": None, "git_email": None}
