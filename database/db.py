"""SQLite database layer — 1:1 port of server/database/db.js."""
import os
import sqlite3
import secrets
import shutil
from pathlib import Path
from typing import Optional

from config import DATABASE_PATH

_THIS_DIR = Path(__file__).parent

# Resolve database path
_DEFAULT_DB_PATH = _THIS_DIR / "auth.db"
DB_PATH = Path(DATABASE_PATH) if DATABASE_PATH else _DEFAULT_DB_PATH
INIT_SQL_PATH = _THIS_DIR / "init.sql"

# Ensure database directory exists
if DATABASE_PATH:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Legacy migration (same logic as db.js)
_LEGACY_DB_PATH = _THIS_DIR / "auth.db"
if DB_PATH != _LEGACY_DB_PATH and not DB_PATH.exists() and _LEGACY_DB_PATH.exists():
    try:
        shutil.copy2(_LEGACY_DB_PATH, DB_PATH)
        print(f"[MIGRATION] Copied database from {_LEGACY_DB_PATH} to {DB_PATH}")
        for suffix in ("-wal", "-shm"):
            src = _LEGACY_DB_PATH.with_suffix(_LEGACY_DB_PATH.suffix + suffix)
            if src.exists():
                shutil.copy2(src, DB_PATH.with_suffix(DB_PATH.suffix + suffix))
    except Exception as e:
        print(f"[MIGRATION] Could not copy legacy database: {e}")

# Create connection
db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")

# Ensure app_config exists early (auth reads JWT secret at import time)
db.execute("""CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)""")
db.commit()

print(f"[INFO] Database: {DB_PATH}")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def _run_migrations():
    cursor = db.execute("PRAGMA table_info(users)")
    columns = {row["name"] for row in cursor.fetchall()}

    for col, ddl in [
        ("git_name", "ALTER TABLE users ADD COLUMN git_name TEXT"),
        ("git_email", "ALTER TABLE users ADD COLUMN git_email TEXT"),
        ("has_completed_onboarding", "ALTER TABLE users ADD COLUMN has_completed_onboarding BOOLEAN DEFAULT 0"),
    ]:
        if col not in columns:
            print(f"Running migration: Adding {col} column")
            db.execute(ddl)

    db.execute("""CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS session_names (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'claude',
        custom_name TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session_id, provider)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_session_names_lookup ON session_names(session_id, provider)")
    db.commit()
    print("Database migrations completed successfully")


def initialize_database():
    init_sql = INIT_SQL_PATH.read_text()
    db.executescript(init_sql)
    print("Database initialized successfully")
    _run_migrations()


# ---------------------------------------------------------------------------
# User DB operations
# ---------------------------------------------------------------------------

class UserDb:
    @staticmethod
    def has_users() -> bool:
        row = db.execute("SELECT COUNT(*) as count FROM users").fetchone()
        return row["count"] > 0

    @staticmethod
    def create_user(username: str, password_hash: str) -> dict:
        cursor = db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        db.commit()
        return {"id": cursor.lastrowid, "username": username}

    @staticmethod
    def get_user_by_username(username: str) -> Optional[dict]:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def update_last_login(user_id: int):
        try:
            db.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
            db.commit()
        except Exception as e:
            print(f"Failed to update last login: {e}")

    @staticmethod
    def get_user_by_id(user_id: int) -> Optional[dict]:
        row = db.execute(
            "SELECT id, username, created_at, last_login FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def ensure_shadow_user(user_id: int, username: str) -> dict:
        """Ensure a user record exists locally for a Main-authenticated browser user.

        In the main/node architecture, Main owns browser auth and forwards the
        resolved user identity to each Node. Nodes still persist per-user local
        settings, so they need a lightweight local row keyed by that forwarded
        identity even though the browser did not authenticate directly against
        the Node database.
        """
        existing_by_id = db.execute(
            "SELECT id, username, created_at, last_login FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if existing_by_id:
            if existing_by_id["username"] != username:
                db.execute(
                    "UPDATE users SET username = ?, is_active = 1 WHERE id = ?",
                    (username, user_id),
                )
                db.commit()
                refreshed = UserDb.get_user_by_id(user_id)
                if refreshed:
                    return refreshed
            return dict(existing_by_id)

        existing_by_username = db.execute(
            "SELECT id, username, created_at, last_login FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing_by_username:
            db.execute(
                "UPDATE users SET is_active = 1 WHERE id = ?",
                (existing_by_username["id"],),
            )
            db.commit()
            refreshed = UserDb.get_user_by_id(existing_by_username["id"])
            if refreshed:
                return refreshed
            return dict(existing_by_username)

        db.execute(
            "INSERT INTO users (id, username, password_hash, is_active) VALUES (?, ?, ?, 1)",
            (user_id, username, "__shadow_user_from_main__"),
        )
        db.commit()
        created = UserDb.get_user_by_id(user_id)
        if created:
            return created
        return {"id": user_id, "username": username}

    @staticmethod
    def get_first_user() -> Optional[dict]:
        row = db.execute(
            "SELECT id, username, created_at, last_login FROM users WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def update_git_config(user_id: int, git_name: str, git_email: str):
        db.execute("UPDATE users SET git_name = ?, git_email = ? WHERE id = ?", (git_name, git_email, user_id))
        db.commit()

    @staticmethod
    def get_git_config(user_id: int) -> Optional[dict]:
        row = db.execute("SELECT git_name, git_email FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def complete_onboarding(user_id: int):
        db.execute("UPDATE users SET has_completed_onboarding = 1 WHERE id = ?", (user_id,))
        db.commit()

    @staticmethod
    def has_completed_onboarding(user_id: int) -> bool:
        row = db.execute("SELECT has_completed_onboarding FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row["has_completed_onboarding"]) if row else False


# ---------------------------------------------------------------------------
# API Keys DB
# ---------------------------------------------------------------------------

class ApiKeysDb:
    @staticmethod
    def generate_api_key() -> str:
        return "ck_" + secrets.token_hex(32)

    @staticmethod
    def create_api_key(user_id: int, key_name: str) -> dict:
        api_key = ApiKeysDb.generate_api_key()
        cursor = db.execute(
            "INSERT INTO api_keys (user_id, key_name, api_key) VALUES (?, ?, ?)",
            (user_id, key_name, api_key),
        )
        db.commit()
        return {"id": cursor.lastrowid, "keyName": key_name, "apiKey": api_key}

    @staticmethod
    def get_api_keys(user_id: int) -> list:
        rows = db.execute(
            "SELECT id, key_name, api_key, created_at, last_used, is_active FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def validate_api_key(api_key: str) -> Optional[dict]:
        row = db.execute("""
            SELECT u.id, u.username, ak.id as api_key_id
            FROM api_keys ak JOIN users u ON ak.user_id = u.id
            WHERE ak.api_key = ? AND ak.is_active = 1 AND u.is_active = 1
        """, (api_key,)).fetchone()
        if row:
            db.execute("UPDATE api_keys SET last_used = CURRENT_TIMESTAMP WHERE id = ?", (row["api_key_id"],))
            db.commit()
            return dict(row)
        return None

    @staticmethod
    def delete_api_key(user_id: int, api_key_id: int) -> bool:
        cursor = db.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (api_key_id, user_id))
        db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def toggle_api_key(user_id: int, api_key_id: int, is_active: bool) -> bool:
        cursor = db.execute(
            "UPDATE api_keys SET is_active = ? WHERE id = ? AND user_id = ?",
            (1 if is_active else 0, api_key_id, user_id),
        )
        db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# User Credentials DB
# ---------------------------------------------------------------------------

class CredentialsDb:
    @staticmethod
    def create_credential(user_id: int, credential_name: str, credential_type: str,
                          credential_value: str, description: Optional[str] = None) -> dict:
        cursor = db.execute(
            "INSERT INTO user_credentials (user_id, credential_name, credential_type, credential_value, description) VALUES (?, ?, ?, ?, ?)",
            (user_id, credential_name, credential_type, credential_value, description),
        )
        db.commit()
        return {"id": cursor.lastrowid, "credentialName": credential_name, "credentialType": credential_type}

    @staticmethod
    def get_credentials(user_id: int, credential_type: Optional[str] = None) -> list:
        query = "SELECT id, credential_name, credential_type, description, created_at, is_active FROM user_credentials WHERE user_id = ?"
        params: list = [user_id]
        if credential_type:
            query += " AND credential_type = ?"
            params.append(credential_type)
        query += " ORDER BY created_at DESC"
        return [dict(r) for r in db.execute(query, params).fetchall()]

    @staticmethod
    def get_active_credential(user_id: int, credential_type: str) -> Optional[str]:
        row = db.execute(
            "SELECT credential_value FROM user_credentials WHERE user_id = ? AND credential_type = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
            (user_id, credential_type),
        ).fetchone()
        return row["credential_value"] if row else None

    @staticmethod
    def delete_credential(user_id: int, credential_id: int) -> bool:
        cursor = db.execute("DELETE FROM user_credentials WHERE id = ? AND user_id = ?", (credential_id, user_id))
        db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def toggle_credential(user_id: int, credential_id: int, is_active: bool) -> bool:
        cursor = db.execute(
            "UPDATE user_credentials SET is_active = ? WHERE id = ? AND user_id = ?",
            (1 if is_active else 0, credential_id, user_id),
        )
        db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Session Names DB
# ---------------------------------------------------------------------------

class SessionNamesDb:
    @staticmethod
    def set_name(session_id: str, provider: str, custom_name: str):
        db.execute("""
            INSERT INTO session_names (session_id, provider, custom_name)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, provider)
            DO UPDATE SET custom_name = excluded.custom_name, updated_at = CURRENT_TIMESTAMP
        """, (session_id, provider, custom_name))
        db.commit()

    @staticmethod
    def get_name(session_id: str, provider: str) -> Optional[str]:
        row = db.execute(
            "SELECT custom_name FROM session_names WHERE session_id = ? AND provider = ?",
            (session_id, provider),
        ).fetchone()
        return row["custom_name"] if row else None

    @staticmethod
    def get_names(session_ids: list, provider: str) -> dict:
        if not session_ids:
            return {}
        placeholders = ",".join("?" for _ in session_ids)
        rows = db.execute(
            f"SELECT session_id, custom_name FROM session_names WHERE session_id IN ({placeholders}) AND provider = ?",
            (*session_ids, provider),
        ).fetchall()
        return {r["session_id"]: r["custom_name"] for r in rows}

    @staticmethod
    def delete_name(session_id: str, provider: str) -> bool:
        cursor = db.execute(
            "DELETE FROM session_names WHERE session_id = ? AND provider = ?",
            (session_id, provider),
        )
        db.commit()
        return cursor.rowcount > 0


def apply_custom_session_names(sessions: list, provider: str):
    if not sessions:
        return
    try:
        ids = [s["id"] for s in sessions]
        custom_names = SessionNamesDb.get_names(ids, provider)
        for session in sessions:
            custom = custom_names.get(session["id"])
            if custom:
                session["summary"] = custom
    except Exception as e:
        print(f"[DB] Failed to apply custom session names for {provider}: {e}")


# ---------------------------------------------------------------------------
# App Config DB
# ---------------------------------------------------------------------------

class AppConfigDb:
    @staticmethod
    def get(key: str) -> Optional[str]:
        try:
            row = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        except Exception:
            return None

    @staticmethod
    def set(key: str, value: str):
        db.execute(
            "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        db.commit()

    @staticmethod
    def get_or_create_jwt_secret() -> str:
        secret = AppConfigDb.get("jwt_secret")
        if not secret:
            secret = secrets.token_hex(64)
            AppConfigDb.set("jwt_secret", secret)
        return secret


# ---------------------------------------------------------------------------
# Backward compatibility alias
# ---------------------------------------------------------------------------

class GithubTokensDb:
    @staticmethod
    def create_github_token(user_id, token_name, github_token, description=None):
        return CredentialsDb.create_credential(user_id, token_name, "github_token", github_token, description)

    @staticmethod
    def get_github_tokens(user_id):
        return CredentialsDb.get_credentials(user_id, "github_token")

    @staticmethod
    def get_active_github_token(user_id):
        return CredentialsDb.get_active_credential(user_id, "github_token")

    @staticmethod
    def delete_github_token(user_id, token_id):
        return CredentialsDb.delete_credential(user_id, token_id)

    @staticmethod
    def toggle_github_token(user_id, token_id, is_active):
        return CredentialsDb.toggle_credential(user_id, token_id, is_active)


# Singleton instances
user_db = UserDb()
api_keys_db = ApiKeysDb()
credentials_db = CredentialsDb()
session_names_db = SessionNamesDb()
app_config_db = AppConfigDb()
github_tokens_db = GithubTokensDb()
