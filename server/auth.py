"""API Key authentication middleware for Aegis.

Each API key is scoped to a project. Format:
    aegis_{project_id}_{role}_{random}

Usage:
    Authorization: Bearer aegis_novaic-gw_master_a1b2c3d4
"""

import secrets
import time
import json
from dataclasses import dataclass


@dataclass
class AuthContext:
    project_id: str
    agent_id: str
    role: str       # "master" | "agent" | "readonly"
    key_id: str


def generate_api_key(project_id: str, role: str = "agent") -> str:
    """Generate a new API key for a project."""
    random_part = secrets.token_hex(16)
    return f"aegis_{project_id}_{role}_{random_part}"


def parse_api_key(key: str) -> dict | None:
    """Parse an API key into its components.

    Returns None if key format is invalid.
    """
    if not key or not key.startswith("aegis_"):
        return None

    parts = key.split("_", 3)
    if len(parts) < 4:
        return None

    return {
        "project_id": parts[1],
        "role": parts[2],
        "random": parts[3],
    }


def validate_api_key(key: str, db_conn) -> AuthContext | None:
    """Validate an API key against the database.

    Returns AuthContext if valid, None if invalid/revoked.
    """
    row = db_conn.execute(
        "SELECT id, project_id, agent_id, role, revoked_at FROM api_keys WHERE id=?",
        (key,)
    ).fetchone()

    if not row:
        return None
    if row["revoked_at"]:
        return None

    return AuthContext(
        project_id=row["project_id"],
        agent_id=row["agent_id"] or "",
        role=row["role"],
        key_id=row["id"],
    )


def create_project_keys(project_id: str, master_agent_id: str = "",
                        db_conn=None) -> dict[str, str]:
    """Generate and store API keys for a new project.

    Returns dict of role → key.
    """
    now = int(time.time() * 1000)
    keys = {}

    for role in ("master", "agent", "readonly"):
        key = generate_api_key(project_id, role)
        agent_id = master_agent_id if role == "master" else ""
        if db_conn:
            db_conn.execute(
                "INSERT INTO api_keys (id,project_id,agent_id,role,created_at) VALUES(?,?,?,?,?)",
                (key, project_id, agent_id, role, now)
            )
        keys[role] = key

    if db_conn:
        db_conn.commit()

    return keys
