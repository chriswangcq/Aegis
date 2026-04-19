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
    "self_test":        "coder",
    "preflight_rework": "coder",
    "rework":           "coder",
    "preflight_review": "reviewer",
    "code_review":      "reviewer",
    "qa":               "qa",
    "deploy_prep":      "deployer",
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
    -- ── Tickets ──────────────────────────────────────────────
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

    -- ── Agents (identity only, no fixed role) ───────────────
    CREATE TABLE IF NOT EXISTS agents (
        id             TEXT PRIMARY KEY,
        display_name   TEXT,
        provider       TEXT,            -- gemini / claude / gpt / human
        status         TEXT DEFAULT 'idle',
        current_ticket TEXT,
        current_role   TEXT,            -- role they're currently acting as
        last_active_at INTEGER,
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Roles (team positions) ──────────────────────────────
    CREATE TABLE IF NOT EXISTS roles (
        id             TEXT PRIMARY KEY,  -- coder / reviewer / qa / deployer
        display_name   TEXT NOT NULL,
        description    TEXT,
        owner_id       TEXT,             -- agent who owns this role (designs exams, manages team)
        interviewer_id TEXT,             -- agent who conducts exams
        exam_json      TEXT DEFAULT '[]', -- list of exam questions
        min_pass_score REAL DEFAULT 0.7,  -- minimum score to certify
        created_at     INTEGER,
        updated_at     INTEGER
    );

    -- ── Certifications (agent × role) ──────────────────────
    CREATE TABLE IF NOT EXISTS certifications (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id       TEXT NOT NULL REFERENCES agents(id),
        role_id        TEXT NOT NULL REFERENCES roles(id),
        status         TEXT DEFAULT 'pending', -- pending / passed / failed / revoked
        score          REAL,
        exam_answers   TEXT DEFAULT '[]',
        trust_json     TEXT DEFAULT '{}',   -- trust is PER ROLE
        tasks_completed INTEGER DEFAULT 0,
        tasks_failed   INTEGER DEFAULT 0,
        interviewed_by TEXT,               -- who graded the exam
        certified_at   INTEGER,
        expires_at     INTEGER,            -- recertify periodically (NULL = permanent)
        created_at     INTEGER,
        updated_at     INTEGER,
        UNIQUE(agent_id, role_id)
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

    -- ── Trust Events ────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS trust_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id      TEXT NOT NULL,
        role_id       TEXT,              -- trust is per role now
        ticket_id     TEXT,
        dimension     TEXT NOT NULL,
        delta         REAL NOT NULL,
        reason        TEXT,
        created_at    INTEGER
    );

    -- ── Indexes ─────────────────────────────────────────────
    CREATE INDEX IF NOT EXISTS idx_tickets_phase ON tickets(phase);
    CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
    CREATE INDEX IF NOT EXISTS idx_certifications_agent ON certifications(agent_id);
    CREATE INDEX IF NOT EXISTS idx_certifications_role ON certifications(role_id);
    CREATE INDEX IF NOT EXISTS idx_evidence_ticket ON evidence(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
    CREATE INDEX IF NOT EXISTS idx_event_log_ticket ON event_log(ticket_id);
    CREATE INDEX IF NOT EXISTS idx_trust_events_agent ON trust_events(agent_id);
    """)
    conn.commit()


def seed_roles(conn: sqlite3.Connection):
    """Seed default roles with exam questions."""
    now = now_ms()
    import json
    roles = [
        {
            "id": "coder",
            "display_name": "Coder",
            "description": "写代码、写测试、调试。负责 preflight 调研和 implementation。",
            "exam": [
                {
                    "q": "查看 novaic-business/business/message_actions.py 的 create_message 函数，它调用了哪些外部服务？列出函数名和所在模块。",
                    "type": "open",
                    "criteria": "必须准确列出真实的函数调用链，不能猜测"
                },
                {
                    "q": "你修改了 Entangled 子模块里的一个文件并提交了 commit。在主仓你还需要做什么？写出具体的 git 命令。",
                    "type": "open",
                    "criteria": "必须包含 git add Entangled && git commit -m 'chore: bump ...'",
                },
                {
                    "q": "你写了一个测试 test_expired_lock_reclaimed，它 mock 了 HTTP 响应并断言 mock 的传参。这个测试有什么问题？",
                    "type": "open",
                    "criteria": "必须识别出这是假测试——去掉生产代码测试不会红",
                },
                {
                    "q": "PR 要求你只改 message_actions.py，但你发现 subagent.py 也需要改才能跑通。你应该怎么做？",
                    "type": "choice",
                    "options": [
                        "A. 一起改，反正都是一个 PR",
                        "B. 在 ticket comments 里提出 scope 变更请求，等 Master 批准",
                        "C. 先改了再说，review 时解释",
                    ],
                    "answer": "B",
                },
            ],
        },
        {
            "id": "reviewer",
            "display_name": "Code Reviewer",
            "description": "审查代码质量、测试真实性、scope 边界。独立于 Coder。",
            "exam": [
                {
                    "q": "以下测试通过了：\n```python\ndef test_dispatch(mock_client):\n    mock_client.post.return_value = {'status': 'ok'}\n    result = dispatch(mock_client)\n    mock_client.post.assert_called_once()\n```\n这个测试可靠吗？为什么？",
                    "type": "open",
                    "criteria": "必须指出：断言的是 mock 传参而非业务行为，删除 dispatch 函数测试仍然通过",
                },
                {
                    "q": "Worker 提交的 git diff 里包含了 scripts/deploy.sh 的修改，但 ticket scope 只包含 business/。你应该怎么做？",
                    "type": "open",
                    "criteria": "必须 reject 并在 blocker 中指出 scope 违规",
                },
                {
                    "q": "Ticket checklist 有 5 项，Worker 的 Status 标记为 [x] Done，但 body 里只有 2 项标了 [x]。这是什么问题？",
                    "type": "open",
                    "criteria": "必须识别为 F-001 fake_checkmark",
                },
            ],
        },
        {
            "id": "qa",
            "display_name": "QA Engineer",
            "description": "运行完整测试套件、回归测试、E2E 验证。",
            "exam": [
                {
                    "q": "PR-18 删除了 inline dispatch。你需要验证什么来确保没有回归？列出具体的测试命令。",
                    "type": "open",
                    "criteria": "必须包含：运行全量测试、检查 subscriber 是否正常接管、检查 error rate",
                },
                {
                    "q": "测试报告显示 '12 passed, 0 failed'，但你发现其中 3 个测试是新加的 mock-only 测试。你应该怎么做？",
                    "type": "open",
                    "criteria": "必须要求补充集成测试或标记为 coverage gap",
                },
            ],
        },
        {
            "id": "deployer",
            "display_name": "Deployer",
            "description": "准备部署脚本、执行灰度发布、监控指标、执行回滚。",
            "exam": [
                {
                    "q": "生产服务器运行的是 PR-14 的代码，PR-15~18 从未在生产跑过。你在部署 PR-18 时需要注意什么？",
                    "type": "open",
                    "criteria": "必须识别为巨型集成上线，需要延长观察窗口",
                },
                {
                    "q": "灰度观察 30 分钟后，error rate 从 0.01% 升至 0.15%。你的回滚 SLO 是 45 秒。描述你的操作步骤。",
                    "type": "open",
                    "criteria": "必须包含：立即触发回滚脚本、验证回滚完成、通知 Master",
                },
            ],
        },
    ]

    for role in roles:
        conn.execute(
            "INSERT OR REPLACE INTO roles (id, display_name, description, exam_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (role["id"], role["display_name"], role["description"],
             json.dumps(role["exam"]), now, now)
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
