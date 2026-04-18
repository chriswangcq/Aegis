"""Database layer — standalone SQLite, zero external deps."""

import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "command-center.db"


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

    CREATE TABLE IF NOT EXISTS failure_patterns (
        id              TEXT PRIMARY KEY,
        pattern_name    TEXT NOT NULL,
        description     TEXT,
        severity        TEXT DEFAULT 'high',
        first_seen_in   TEXT,
        recurrences     TEXT DEFAULT '[]',
        detection_query TEXT,
        countermeasure  TEXT,
        created_at      INTEGER,
        updated_at      INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_tickets_phase ON tickets(phase);
    CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
    CREATE INDEX IF NOT EXISTS idx_evidence_ticket ON evidence(ticket_id);
    """)
    conn.commit()
