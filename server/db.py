"""Database layer — standalone SQLite, zero external deps."""

import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "command-center.db"

VALID_PHASES = [
    "planning", "ready",
    "preflight", "preflight_review", "preflight_rework",
    "implementation", "self_test",
    "code_review", "rework",
    "qa", "merge_ready", "merging",
    "deploy_prep", "canary", "rollback",
    "done",
]

# Which phases require which role
PHASE_ROLE = {
    "ready":            "coder",
    "implementation":   "coder",
    "preflight_rework": "coder",
    "rework":           "coder",
    "preflight_review": "reviewer",
    "design_review":    "reviewer",     # Gap 3: RFC review for high-risk tickets
    "code_review":      "reviewer",
    "qa":               "qa",
    "deploy_prep":      "deployer",
    "monitoring":       "deployer",     # Gap 1: post-deploy health check
}

# Where submit advances to
SUBMIT_NEXT = {
    "preflight":       "preflight_review",
    "implementation":  "code_review",   # test evidence required at submit
    "preflight_rework":"preflight_review",
    "rework":          "code_review",    # rework goes straight to CR too
    "design_review":   "implementation", # after RFC approved → start coding
    "code_review":     "qa",
    "qa":              "merge_ready",
    "deploy_prep":     "monitoring",     # Gap 1: deploy → monitoring
    "monitoring":      "done",           # Gap 1: monitoring pass → done
}

PHASE_TIMEOUTS = {
    "preflight": 4*3600*1000, "preflight_review": 2*3600*1000,
    "design_review": 4*3600*1000,
    "preflight_rework": 2*3600*1000, "implementation": 8*3600*1000,
    "self_test": 1*3600*1000, "code_review": 4*3600*1000,
    "rework": 4*3600*1000, "qa": 2*3600*1000,
    "merge_ready": 1*3600*1000, "merging": 15*60*1000,
    "deploy_prep": 2*3600*1000, "monitoring": 30*60*1000,  # 30 min health check
    "canary": 48*3600*1000, "rollback": 1*3600*1000,
}


def now_ms() -> int:
    return int(time.time() * 1000)


def get_db(path: Path | None = None) -> sqlite3.Connection:
    import os
    env_path = os.environ.get("AEGIS_DB_PATH")
    p = path or (Path(env_path) if env_path else DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
    -- ── Projects ─────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS projects (
        id             TEXT PRIMARY KEY,
        name           TEXT NOT NULL,
        description    TEXT DEFAULT '',
        repo_url       TEXT NOT NULL,
        tech_stack     TEXT DEFAULT '[]',
        conventions    TEXT DEFAULT '{}',
        environments_json TEXT DEFAULT '{}',     -- {ci: {...}, pre: {...}, prod: {...}}
        default_domain TEXT DEFAULT '',
        master_id      TEXT,
        status         TEXT DEFAULT 'active',
        metrics_url    TEXT DEFAULT '',
        webhook_url    TEXT DEFAULT '',
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Tickets ──────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS tickets (
        id             TEXT PRIMARY KEY,
        project_id     TEXT REFERENCES projects(id), -- belongs to a project
        title          TEXT NOT NULL,
        description    TEXT DEFAULT '',
        phase          TEXT NOT NULL DEFAULT 'planning',
        assigned_to    TEXT,
        assigned_role  TEXT,
        locked_at      INTEGER,
        lock_ttl_ms    INTEGER DEFAULT 300000,
        depends_on     TEXT DEFAULT '[]',
        blocked_by     TEXT,
        scope_json     TEXT DEFAULT '{}',
        checklist_json TEXT DEFAULT '[]',
        test_specs_json TEXT DEFAULT '[]',
        branch         TEXT,
        priority       INTEGER DEFAULT 0,
        risk_level     TEXT DEFAULT 'normal',
        domain         TEXT DEFAULT '',
        review_rounds  INTEGER DEFAULT 0,
        canary_stage   INTEGER DEFAULT 0,            -- current canary %, 0=not deployed
        canary_plan    TEXT DEFAULT '[]',             -- [1,5,25,100]
        created_by     TEXT,
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Agents (identity only, no fixed role) ───────────────
    CREATE TABLE IF NOT EXISTS agents (
        id             TEXT PRIMARY KEY,
        display_name   TEXT,
        provider       TEXT,
        webhook_url    TEXT DEFAULT '',      -- Aegis notifies agent here
        status         TEXT DEFAULT 'idle',
        current_ticket TEXT,
        current_role   TEXT,
        last_active_at INTEGER,
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Roles (team positions) ──────────────────────────────
    CREATE TABLE IF NOT EXISTS roles (
        id             TEXT PRIMARY KEY,  -- coder / reviewer
        display_name   TEXT NOT NULL,
        description    TEXT,
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Post-Mortems (learn from failures) ──────────────────
    CREATE TABLE IF NOT EXISTS post_mortems (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id     TEXT NOT NULL REFERENCES tickets(id),
        trigger_reason TEXT,          -- e.g. 'review_rounds >= 2'
        pattern       TEXT,           -- detected error pattern
        action_items  TEXT DEFAULT '[]',
        knowledge_id  TEXT,           -- link to knowledge entry created
        created_at    INTEGER
    );

    -- ── Evidence ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS evidence (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id     TEXT NOT NULL REFERENCES tickets(id),
        phase         TEXT,
        agent_id      TEXT,
        evidence_type TEXT NOT NULL,
        content       TEXT,
        verdict       TEXT,
        timestamp     INTEGER
    );

    -- ── Comments ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS comments (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id     TEXT NOT NULL REFERENCES tickets(id),
        author_id     TEXT NOT NULL,
        author_role   TEXT,
        content       TEXT NOT NULL,
        comment_type  TEXT DEFAULT 'discussion',
        status        TEXT DEFAULT 'open',
        refs          TEXT DEFAULT '[]',
        parent_id     INTEGER,
        created_at    INTEGER,
        updated_at    INTEGER
    );

    -- ── Knowledge Base ──────────────────────────────────────
    CREATE TABLE IF NOT EXISTS knowledge (
        id            TEXT PRIMARY KEY,
        category      TEXT NOT NULL,
        title         TEXT NOT NULL,
        content       TEXT NOT NULL,
        tags          TEXT DEFAULT '[]',
        source_tickets TEXT DEFAULT '[]',
        created_by    TEXT,
        created_at    INTEGER,
        updated_at    INTEGER
    );

    -- ── Event Log ───────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS event_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type    TEXT NOT NULL,
        ticket_id     TEXT,
        agent_id      TEXT,
        old_value     TEXT,
        new_value     TEXT,
        metadata      TEXT DEFAULT '{}',
        timestamp     INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_evidence_ticket ON evidence(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
    CREATE INDEX IF NOT EXISTS idx_event_log_ticket ON event_log(ticket_id);

    -- ── API Keys ─────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS api_keys (
        id             TEXT PRIMARY KEY,
        project_id     TEXT REFERENCES projects(id),
        agent_id       TEXT DEFAULT '',
        user_id        TEXT DEFAULT '',
        role           TEXT DEFAULT 'agent',
        created_at     INTEGER,
        revoked_at     INTEGER
    );

    -- ── Users (individual accounts) ─────────────────────
    CREATE TABLE IF NOT EXISTS users (
        id             TEXT PRIMARY KEY,
        display_name   TEXT DEFAULT '',
        email          TEXT DEFAULT '',
        password_hash  TEXT DEFAULT '',
        api_key        TEXT UNIQUE NOT NULL,
        role           TEXT DEFAULT 'member',
        created_at     INTEGER,
        last_login_at  INTEGER
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

    -- ── Project Members (who has access to what) ────────
    CREATE TABLE IF NOT EXISTS project_members (
        project_id     TEXT REFERENCES projects(id),
        user_id        TEXT REFERENCES users(id),
        role           TEXT DEFAULT 'member',
        invited_by     TEXT DEFAULT '',
        joined_at      INTEGER,
        PRIMARY KEY (project_id, user_id)
    );

    -- ── Join Requests (approval-based) ────────────────
    CREATE TABLE IF NOT EXISTS join_requests (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id     TEXT REFERENCES projects(id),
        user_id        TEXT REFERENCES users(id),
        role           TEXT DEFAULT 'member',
        message        TEXT DEFAULT '',
        status         TEXT DEFAULT 'pending',
        reviewed_by    TEXT DEFAULT '',
        review_note    TEXT DEFAULT '',
        created_at     INTEGER,
        reviewed_at    INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_join_requests_project ON join_requests(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_join_requests_user ON join_requests(user_id, status);

    -- ── Notifications (message center) ──────────────
    CREATE TABLE IF NOT EXISTS notifications (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        TEXT NOT NULL,
        type           TEXT NOT NULL,
        title          TEXT NOT NULL,
        body           TEXT DEFAULT '',
        ref_type       TEXT DEFAULT '',
        ref_id         TEXT DEFAULT '',
        is_read        INTEGER DEFAULT 0,
        created_at     INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

    -- ── CI Jobs ──────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS ci_jobs (
        id             TEXT PRIMARY KEY,
        ticket_id      TEXT REFERENCES tickets(id),
        project_id     TEXT REFERENCES projects(id),
        branch         TEXT,
        status         TEXT DEFAULT 'queued',
        worker_id      TEXT,
        started_at     INTEGER,
        finished_at    INTEGER,
        results_json   TEXT DEFAULT '[]',
        container_id   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ci_jobs_status ON ci_jobs(status);
    CREATE INDEX IF NOT EXISTS idx_api_keys_project ON api_keys(project_id);
    """)
    conn.commit()


def seed_roles(conn: sqlite3.Connection):
    """Seed default roles."""
    now = now_ms()
    roles = [
        {"id": "coder", "display_name": "Coder",
         "description": "写代码、写测试、调试。负责 preflight 调研和 implementation。"},
        {"id": "reviewer", "display_name": "Code Reviewer",
         "description": "审查代码质量、测试真实性、scope 边界。独立于 Coder。"},
    ]

    for role in roles:
        conn.execute(
            "INSERT OR REPLACE INTO roles (id, display_name, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (role["id"], role["display_name"], role["description"], now, now)
        )
    conn.commit()


def log_event(conn: sqlite3.Connection, event_type: str, ticket_id: str = None,
              agent_id: str = None, old_value: str = None, new_value: str = None,
              metadata: str = "{}"):
    conn.execute(
        "INSERT INTO event_log (event_type, ticket_id, agent_id, old_value, new_value, metadata, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, ticket_id, agent_id, old_value, new_value, metadata, now_ms())
    )
