"""API Key authentication for Aegis.

Two auth models supported:
  1. User-based: each person registers → gets personal API key
  2. Project-based: legacy shared keys (master/agent/readonly)

User keys format: aegis_u_{random}
Project keys format: aegis_{project}_{role}_{random}
"""

import secrets
import time
import json
import hashlib
from dataclasses import dataclass


@dataclass
class AuthContext:
    """Result of successful authentication."""
    user_id: str = ""
    project_id: str = ""     # "*" = all projects (admin)
    agent_id: str = ""
    role: str = "member"     # admin / owner / member / viewer / agent / readonly
    key_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_owner(self) -> bool:
        return self.role in ("admin", "owner", "master")

    def can_write(self, project_id: str = "") -> bool:
        if self.is_admin:
            return True
        if self.role in ("owner", "master", "member", "agent"):
            return not project_id or self.project_id == "*" or self.project_id == project_id
        return False

    def can_read(self, project_id: str = "") -> bool:
        if self.is_admin:
            return True
        return not project_id or self.project_id == "*" or self.project_id == project_id


def generate_user_key() -> str:
    """Generate a personal API key for a user."""
    return f"aegis_u_{secrets.token_hex(20)}"


def generate_api_key(project_id: str, role: str = "agent") -> str:
    """Generate a project-scoped API key."""
    random_part = secrets.token_hex(16)
    return f"aegis_{project_id}_{role}_{random_part}"


def generate_invite_code() -> str:
    """Generate a short invite code."""
    return f"inv_{secrets.token_hex(8)}"


def validate_api_key(key: str, db_conn) -> AuthContext | None:
    """Validate an API key — check user keys first, then project keys.

    Returns AuthContext if valid, None if invalid/revoked.
    """
    if not key:
        return None

    # 1. Check user keys
    user = db_conn.execute(
        "SELECT id, display_name, role FROM users WHERE api_key=?", (key,)
    ).fetchone()
    if user:
        # User found — check which projects they belong to
        memberships = db_conn.execute(
            "SELECT project_id, role FROM project_members WHERE user_id=?",
            (user["id"],)
        ).fetchall()
        # For now, return first project or "*" if multi-project
        project_id = "*"
        role = user["role"]
        if len(memberships) == 1:
            project_id = memberships[0]["project_id"]
            role = memberships[0]["role"]
        elif len(memberships) > 1:
            project_id = "*"  # multi-project user

        # Update last login
        db_conn.execute("UPDATE users SET last_login_at=? WHERE id=?",
                       (int(time.time() * 1000), user["id"]))
        db_conn.commit()

        return AuthContext(
            user_id=user["id"],
            project_id=project_id,
            role=role,
            key_id=key[:20] + "..."
        )

    # 2. Check project API keys (legacy)
    row = db_conn.execute(
        "SELECT id, project_id, agent_id, user_id, role, revoked_at FROM api_keys WHERE id=?",
        (key,)
    ).fetchone()
    if not row or row["revoked_at"]:
        return None

    return AuthContext(
        project_id=row["project_id"],
        agent_id=row["agent_id"] or "",
        user_id=row["user_id"] or "",
        role=row["role"],
        key_id=row["id"][:20] + "...",
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


def _hash_password(password: str) -> str:
    """Hash password with salt using SHA-256."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
    if not stored or ':' not in stored:
        return False
    salt, h = stored.split(':', 1)
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == h


def register_user(user_id: str, display_name: str, email: str,
                  password: str = "", db_conn=None) -> dict:
    """Register a new user. Returns API key."""
    now = int(time.time() * 1000)
    api_key = generate_user_key()
    pw_hash = _hash_password(password) if password else ""

    if db_conn:
        existing = db_conn.execute("SELECT api_key FROM users WHERE id=?", (user_id,)).fetchone()
        if existing:
            return {"user_id": user_id, "api_key": existing["api_key"], "existing": True}

        db_conn.execute(
            "INSERT INTO users (id,display_name,email,password_hash,api_key,role,created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, display_name or user_id, email, pw_hash, api_key, "member", now))
        db_conn.commit()

    return {"user_id": user_id, "api_key": api_key, "existing": False}


def login_with_password(user_id: str, password: str, db_conn=None) -> dict | None:
    """Login with username + password. Returns API key on success."""
    if not db_conn:
        return None
    user = db_conn.execute(
        "SELECT id, display_name, password_hash, api_key, role FROM users WHERE id=?",
        (user_id,)).fetchone()
    if not user:
        return None
    if not user["password_hash"]:
        return None  # user has no password set (API key only)
    if not _verify_password(password, user["password_hash"]):
        return None
    # Update last login
    db_conn.execute("UPDATE users SET last_login_at=? WHERE id=?",
                   (int(time.time() * 1000), user_id))
    db_conn.commit()
    return {"user_id": user["id"], "display_name": user["display_name"],
            "api_key": user["api_key"], "role": user["role"]}


def invite_user_to_project(project_id: str, target_user_id: str,
                           role: str, inviter_id: str,
                           db_conn=None) -> dict:
    """Owner directly adds a user to a project by username."""
    if not db_conn:
        return {"error": "no db"}
    now = int(time.time() * 1000)

    # Check target user exists
    user = db_conn.execute("SELECT id, display_name FROM users WHERE id=?",
                          (target_user_id,)).fetchone()
    if not user:
        return {"error": "user_not_found"}

    # Check already member
    existing = db_conn.execute(
        "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?",
        (project_id, target_user_id)).fetchone()
    if existing:
        return {"error": "already_member"}

    # Add directly
    db_conn.execute(
        "INSERT INTO project_members (project_id,user_id,role,invited_by,joined_at) VALUES(?,?,?,?,?)",
        (project_id, target_user_id, role, inviter_id, now))

    # Get project name for notification
    proj = db_conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    proj_name = proj["name"] if proj else project_id

    inviter = db_conn.execute("SELECT display_name FROM users WHERE id=?", (inviter_id,)).fetchone()
    inviter_name = inviter["display_name"] if inviter else inviter_id

    _create_notification(db_conn, target_user_id, "project_invited",
        f"📬 你被邀请加入了 {proj_name}",
        f"{inviter_name} 邀请你以 {role} 身份加入项目 {project_id}",
        "project", project_id)

    db_conn.commit()
    return {"status": "added", "user_id": target_user_id, "project_id": project_id}


def request_join(project_id: str, user_id: str, role: str = "member",
                 message: str = "", db_conn=None) -> dict:
    """Submit a join request for a project."""
    if not db_conn:
        return {"error": "no db"}

    now = int(time.time() * 1000)

    # Check if already a member
    existing = db_conn.execute(
        "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?",
        (project_id, user_id)).fetchone()
    if existing:
        return {"status": "already_member"}

    # Check for pending request
    pending = db_conn.execute(
        "SELECT id FROM join_requests WHERE project_id=? AND user_id=? AND status='pending'",
        (project_id, user_id)).fetchone()
    if pending:
        return {"status": "already_pending", "request_id": pending["id"]}

    # Create request
    db_conn.execute(
        "INSERT INTO join_requests (project_id,user_id,role,message,status,created_at) VALUES(?,?,?,?,'pending',?)",
        (project_id, user_id, role, message, now))
    req_id = db_conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

    # Notify project owners
    owners = db_conn.execute(
        "SELECT user_id FROM project_members WHERE project_id=? AND role='owner'",
        (project_id,)).fetchall()
    # Also notify master_id
    proj = db_conn.execute("SELECT master_id FROM projects WHERE id=?", (project_id,)).fetchone()

    notify_targets = {o["user_id"] for o in owners}
    if proj and proj["master_id"]:
        notify_targets.add(proj["master_id"])

    user = db_conn.execute("SELECT display_name FROM users WHERE id=?", (user_id,)).fetchone()
    display = user["display_name"] if user else user_id

    for target in notify_targets:
        _create_notification(db_conn, target, "join_request",
            f"{display} 申请加入项目",
            f"{display} 想以 {role} 身份加入 {project_id}。{'留言: ' + message if message else ''}",
            "join_request", str(req_id))

    db_conn.commit()
    return {"status": "pending", "request_id": req_id}


def review_join(request_id: int, reviewer_id: str, action: str,
                note: str = "", db_conn=None) -> dict:
    """Approve or reject a join request."""
    if not db_conn:
        return {"error": "no db"}

    now = int(time.time() * 1000)
    req = db_conn.execute("SELECT * FROM join_requests WHERE id=?", (request_id,)).fetchone()
    if not req:
        return {"error": "not_found"}
    if req["status"] != "pending":
        return {"error": "already_reviewed", "status": req["status"]}

    if action not in ("approved", "rejected"):
        return {"error": "invalid_action"}

    db_conn.execute(
        "UPDATE join_requests SET status=?,reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
        (action, reviewer_id, note, now, request_id))

    if action == "approved":
        # Add to project members
        db_conn.execute(
            "INSERT OR IGNORE INTO project_members (project_id,user_id,role,invited_by,joined_at) VALUES(?,?,?,?,?)",
            (req["project_id"], req["user_id"], req["role"], reviewer_id, now))

    # Notify the applicant
    reviewer = db_conn.execute("SELECT display_name FROM users WHERE id=?", (reviewer_id,)).fetchone()
    reviewer_name = reviewer["display_name"] if reviewer else reviewer_id
    emoji = "✅" if action == "approved" else "❌"

    _create_notification(db_conn, req["user_id"], f"join_{action}",
        f"{emoji} 加入申请{'通过' if action == 'approved' else '被拒绝'}",
        f"{reviewer_name} {'同意' if action == 'approved' else '拒绝'}了你加入 {req['project_id']} 的申请。{note if note else ''}",
        "join_request", str(request_id))

    db_conn.commit()
    return {"status": action, "project_id": req["project_id"], "user_id": req["user_id"]}


def _create_notification(db_conn, user_id: str, ntype: str, title: str,
                         body: str, ref_type: str = "", ref_id: str = ""):
    """Insert a notification for a user."""
    now = int(time.time() * 1000)
    db_conn.execute(
        "INSERT INTO notifications (user_id,type,title,body,ref_type,ref_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (user_id, ntype, title, body, ref_type, ref_id, now))


def get_notifications(user_id: str, unread_only: bool = False,
                      limit: int = 50, db_conn=None) -> list:
    """Get notifications for a user."""
    if not db_conn:
        return []
    if unread_only:
        rows = db_conn.execute(
            "SELECT * FROM notifications WHERE user_id=? AND is_read=0 ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)).fetchall()
    else:
        rows = db_conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def mark_read(notification_id: int, db_conn=None):
    """Mark a notification as read."""
    if db_conn:
        db_conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
        db_conn.commit()

