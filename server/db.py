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

# Which phases are claimable and by which role
CLAIMABLE = {
    "ready":            "coder",
    "preflight_rework": "coder",
    "rework":           "coder",
    "preflight_review": "cr",
    "code_review":      "cr",
    "qa":               "qa",
    "deploy_prep":      "deploy",
}

# Where submit advances to
SUBMIT_NEXT = {
    "preflight":       "preflight_review",
    "implementation":  "self_test",
    "self_test":       "code_review",
    "preflight_rework":"preflight_review",
    "rework":          "self_test",
    "code_review":     "qa",
    "qa":              "merge_ready",
}

PHASE_TIMEOUTS = {
    "preflight": 4*3600*1000, "preflight_review": 2*3600*1000,
    "preflight_rework": 2*3600*1000, "implementation": 8*3600*1000,
    "self_test": 1*3600*1000, "code_review": 4*3600*1000,
    "rework": 4*3600*1000, "qa": 2*3600*1000,
    "merge_ready": 1*3600*1000, "merging": 15*60*1000,
    "deploy_prep": 2*3600*1000, "canary": 48*3600*1000,
    "rollback": 1*3600*1000,
}


def now_ms() -> int:
    return int(time.time() * 1000)


def get_db(path: Path | None = None) -> sqlite3.Connection:
    p = path or DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tickets (
        id             TEXT PRIMARY KEY,
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
        branch         TEXT,
        priority       INTEGER DEFAULT 0,
        risk_level     TEXT DEFAULT 'normal',
        review_rounds  INTEGER DEFAULT 0,
        created_by     TEXT,
        created_at     INTEGER,
        updated_at     INTEGER
    );

    CREATE TABLE IF NOT EXISTS agents (
        id             TEXT PRIMARY KEY,
        display_name   TEXT,
        role           TEXT NOT NULL,
        provider       TEXT,
        status         TEXT DEFAULT 'idle',
        current_ticket TEXT,
        trust_json     TEXT DEFAULT '{}',
        capabilities   TEXT DEFAULT '[]',
        failure_count  INTEGER DEFAULT 0,
        success_count  INTEGER DEFAULT 0,
        last_active_at INTEGER,
        created_at     INTEGER,
        updated_at     INTEGER
    );

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

    CREATE TABLE IF NOT EXISTS trust_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id      TEXT NOT NULL REFERENCES agents(id),
        ticket_id     TEXT,
        dimension     TEXT NOT NULL,
        delta         REAL NOT NULL,
        reason        TEXT,
        created_at    INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_tickets_phase ON tickets(phase);
    CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
    CREATE INDEX IF NOT EXISTS idx_evidence_ticket ON evidence(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
    CREATE INDEX IF NOT EXISTS idx_event_log_ticket ON event_log(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_trust_events_agent ON trust_events(agent_id);
    """)
    conn.commit()


def log_event(conn: sqlite3.Connection, event_type: str, ticket_id: str = None,
              agent_id: str = None, old_value: str = None, new_value: str = None,
              metadata: str = "{}"):
    conn.execute(
        "INSERT INTO event_log (event_type, ticket_id, agent_id, old_value, new_value, metadata, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, ticket_id, agent_id, old_value, new_value, metadata, now_ms())
    )
