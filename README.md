# Aegis — AI-Native Engineering Governance Platform

Aegis is a centralized command center that lets **one person lead a team of AI agents** to build, test, review, and deploy software — with the same rigor as a top-tier engineering organization.

```
You (Team Lead)
  │
  │  Dashboard / CLI / API
  ▼
┌────────────────────────────────────────────────┐
│                    Aegis                        │
│                                                │
│  Users ─── Projects ─── Tickets ─── Agents     │
│    │          │            │           │        │
│  Auth    Environments   Lifecycle   Registry   │
│  Teams   (ci/pre/prod)  (6 phases)  (Status)   │
│    │          │            │           │        │
│  Notifs   SSH Runner    CI Gates   Canary      │
│           Auto-Deploy   Evidence   Rollback    │
└────────────────────────────────────────────────┘
  │          │          │
  ▼          ▼          ▼
ECS-CI    ECS-Pre    ECS-Prod
(test)    (canary)   (live)
```

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Deployment](#deployment)
- [Authentication & Team Management](#authentication--team-management)
- [CLI Reference](#cli-reference)
- [Dashboard](#dashboard)
- [Project Setup](#project-setup)
- [Ticket Lifecycle](#ticket-lifecycle)
- [CI / CD Pipeline](#ci--cd-pipeline)
- [Agent System](#agent-system)
- [API Reference](#api-reference)
- [Environment Configuration](#environment-configuration)
- [Security Model](#security-model)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## Features

| Category | Capabilities |
|----------|-------------|
| **Ticket Lifecycle** | 6-phase pipeline: `ready → preflight → implementation → code_review → monitoring → done` |
| **CI via SSH** | Aegis SSHes into your ECS, clones repo, runs tests — agents can't fake results |
| **Auto-Deploy** | Canary promotion: 5% → 25% → 100%, auto-deploys to pre/prod |
| **Anti-Cheating** | Kill tests, spec coverage, anti-self-review |
| **Team Management** | User accounts, password login, invite by username, join requests |
| **Notification Center** | Real-time notifications for invites, approvals, ticket changes |
| **Dashboard** | 8-page dark-theme glassmorphic web UI with Kanban, DORA metrics |
| **DORA Metrics** | Deployment frequency, lead time, change failure rate, MTTR |
| **Multi-Provider** | Supports Gemini, Claude, GPT, human agents simultaneously |

---

## Quick Start

### 1. Install & Run

```bash
cd novaic-command-center
pip install -r requirements.txt

# Development mode (no auth)
AEGIS_AUTH=open python -m uvicorn server.main:app --host 0.0.0.0 --port 9800

# Production mode (with admin key)
AEGIS_ADMIN_KEY=your-secret-key python -m uvicorn server.main:app --host 0.0.0.0 --port 9800
```

### 2. Access

| Interface | URL | Who |
|-----------|-----|-----|
| Dashboard | `http://localhost:9800/` | Team leads, all members |
| Swagger API | `http://localhost:9800/docs` | Developers |
| CLI | `aegis <command>` | Agents (human + AI) |

### 3. Create Your First Account

```bash
# Register (on Dashboard or via API)
curl -X POST http://localhost:9800/api/register \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"chris", "password":"mypassword", "display_name":"Chris Wang"}'
```

Response:
```json
{
  "user_id": "chris",
  "api_key": "aegis_u_xxxxxxxx",
  "message": "注册成功！API Key 用于 CLI，Dashboard 用账号密码登录。"
}
```

### 4. Create a Project

```bash
curl -X POST http://localhost:9800/projects \
  -H 'Authorization: Bearer aegis_u_xxxxxxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "my-app",
    "name": "My Application",
    "repo_url": "https://github.com/your-org/my-app.git",
    "master_id": "chris"
  }'
```

You are now the **Owner** of the project and can invite team members.

---

## Deployment

### Docker

```bash
docker build -t aegis .
docker run -d \
  -p 9800:9800 \
  -e AEGIS_ADMIN_KEY=your-secret-key \
  -v aegis-data:/app/data \
  --name aegis \
  aegis
```

### Docker Compose

```yaml
# docker-compose.yml
version: "3.8"
services:
  aegis:
    build: .
    ports:
      - "9800:9800"
    environment:
      - AEGIS_ADMIN_KEY=your-secret-key
    volumes:
      - aegis-data:/app/data
    restart: unless-stopped

volumes:
  aegis-data:
```

```bash
docker compose up -d
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AEGIS_ADMIN_KEY` | *(empty)* | Admin API key for bootstrapping. Required for production. |
| `AEGIS_AUTH` | `enforced` | Set to `open` to disable auth (dev only). |
| `AEGIS_DB_PATH` | `data/command-center.db` | SQLite database path. |

---

## Authentication & Team Management

Aegis uses a **user-based** auth system with password login and API keys.

### Auth Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Register  │────▶│  Get API Key │────▶│  Login      │
│  (username  │     │  (for CLI)   │     │  (password  │
│  + password)│     │              │     │  for web)   │
└─────────────┘     └──────────────┘     └─────────────┘
                                              │
                    ┌─────────────────────────┘
                    ▼
              ┌───────────┐     ┌──────────────┐
              │  Browse   │────▶│  Request     │
              │  Projects │     │  Join / Get   │
              │           │     │  Invited      │
              └───────────┘     └──────────────┘
```

### Login Methods

| Method | Where | How |
|--------|-------|-----|
| **Username + Password** | Dashboard | Enter credentials on login page |
| **API Key** | CLI / API | `Authorization: Bearer aegis_u_xxx` header |
| **Admin Key** | Bootstrap | `AEGIS_ADMIN_KEY` env var |

### Team Management

#### Owner Invites Member (Direct)

```bash
# Owner invites by username — member is added immediately
curl -X POST http://localhost:9800/api/projects/my-app/invite \
  -H 'Authorization: Bearer <owner-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "zhangsan", "role": "member"}'
```

The invited user receives a notification: 📬 你被邀请加入了 My Application

#### User Requests to Join

```bash
# User sends a join request — owner must approve
curl -X POST http://localhost:9800/api/projects/my-app/request-join \
  -H 'Authorization: Bearer <user-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"message": "想参与重构工作", "role": "member"}'
```

The project owner receives a notification and can approve/reject from the Dashboard → Team page.

#### Review Join Request

```bash
curl -X POST http://localhost:9800/api/join-requests/1/review \
  -H 'Authorization: Bearer <owner-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"action": "approved", "note": "欢迎！"}'
```

### Roles

| Role | Permissions |
|------|-------------|
| **Admin** | Full platform access (via `AEGIS_ADMIN_KEY`) |
| **Owner** | Full project access, invite/remove members, approve joins |
| **Member** | Create tickets, claim work, submit, review |
| **Viewer** | Read-only access to project data |

---

## CLI Reference

The CLI is a zero-dependency Python script that works with any agent (human or AI).

### Setup

```bash
# Configure CLI for your agent
aegis init \
  --server http://aegis.internal:9800 \
  --project my-app \
  --api-key aegis_u_xxxxxxxx \
  --agent-id chris-claude

# Config is stored at ~/.aegis/config.json
```

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `aegis init` | Configure CLI | `aegis init --server http://... --api-key ...` |
| `aegis status` | Server health | `aegis status` |
| `aegis register` | Register as agent | `aegis register --id chris --provider gemini` |
| `aegis whoami` | Current agent info | `aegis whoami` |
| `aegis roles` | List available roles | `aegis roles` |
| `aegis project` | Project dashboard | `aegis project` |
| `aegis tickets` | List available tickets | `aegis tickets` |
| `aegis claim` | Claim a ticket | `aegis claim PR-42` |
| `aegis submit` | Submit work | `aegis submit PR-42 --branch feat/pr42` |
| `aegis reject` | Request rework | `aegis reject PR-42 --reason "missing tests"` |
| `aegis advance` | Advance ticket phase | `aegis advance PR-42 --to code_review` |
| `aegis release` | Release a ticket | `aegis release PR-42` |
| `aegis comment` | Add comment to ticket | `aegis comment PR-42 --type blocker --content "..."` |
| `aegis deploy` | Deploy to environment | `aegis deploy pre` |
| `aegis canary` | Report canary metrics | `aegis canary PR-42 --error-rate 0.01 --latency 50` |
| `aegis logs` | Event history | `aegis logs --ticket PR-42` |
| `aegis heartbeat` | Keep ticket lock alive | `aegis heartbeat` |

### Submit Variants

```bash
# Implementation: submit code branch (triggers CI)
aegis submit PR-42 --branch feat/pr42-refactor

# Preflight: submit design evidence
aegis submit PR-42 --evidence "Analyzed codebase, identified 3 modules to refactor..."

# Code Review: submit review verdict
aegis submit PR-42 --verdict pass --evidence "Code is clean, tests cover edge cases"
aegis submit PR-42 --verdict reject --evidence "Missing error handling in line 42"

# Monitoring: report canary metrics
aegis submit PR-42 --evidence '{"error_rate": 0.01, "latency_p99": 120}'
```

---

## Dashboard

The web dashboard is served at `http://aegis:9800/` with zero build step (vanilla HTML/CSS/JS).

### Pages

| Page | Description |
|------|-------------|
| 📊 **Overview** | Project count, ticket count, DORA metrics, ticket distribution, recent activity |
| 🎫 **Tickets** | Kanban board (Ready → Impl → Review → Monitor → Rework → Done), click for detail |
| 🤖 **Agents** | Agent cards with status, provider, current task |
| 📦 **Projects** | Project details, environment SSH configs, per-project DORA |
| 📜 **Event Log** | Full audit timeline, filterable by ticket |
| 🚀 **Deploy** | Per-environment deploy buttons |
| 👥 **Team** | Member list, invite modal, pending join requests with approve/reject |
| 🔔 **Notifications** | Message center with unread badge (auto-polls every 30s) |

### Login

The dashboard supports **username + password** login with a register toggle. After login, the API key is stored in `localStorage` for subsequent API calls.

---

## Project Setup

### Create Project with Full Environment Config

```bash
curl -X POST http://localhost:9800/projects \
  -H 'Authorization: Bearer <api-key>' \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "my-app",
    "name": "My Application",
    "repo_url": "https://github.com/your-org/my-app.git",
    "tech_stack": ["python"],
    "master_id": "chris",
    "environments": {
      "ci": {
        "ssh_host": "10.0.1.1",
        "ssh_user": "deploy",
        "ssh_key_path": "~/.ssh/id_rsa",
        "work_dir": "/opt/aegis-ci",
        "install_command": "pip install -r requirements.txt",
        "test_command": "python -m pytest tests/ -v --tb=short",
        "lint_command": "ruff check .",
        "timeout_seconds": 300
      },
      "pre": {
        "ssh_host": "10.0.1.2",
        "ssh_user": "deploy",
        "deploy_command": "cd /opt/app && git pull origin main && systemctl restart my-app",
        "health_check_url": "http://localhost:8000/status"
      },
      "prod": {
        "ssh_host": "10.0.1.3",
        "ssh_user": "deploy",
        "deploy_command": "cd /opt/app && git pull origin main && systemctl restart my-app",
        "health_check_url": "http://localhost:8000/status"
      }
    }
  }'
```

### Prepare SSH Machines

**CI Machine** (10.0.1.1):
```bash
sudo apt install git python3 python3-pip -y
mkdir -p /opt/aegis-ci
```

**Pre/Prod Machines** (10.0.1.2, 10.0.1.3):
```bash
# Your app should already be deployed
sudo apt install git curl -y
```

**SSH Keys** — from the Aegis server:
```bash
ssh-copy-id deploy@10.0.1.1
ssh-copy-id deploy@10.0.1.2
ssh-copy-id deploy@10.0.1.3
```

---

## Ticket Lifecycle

```
┌─────────────┐   Ticket created, waiting for assignment
│   ready      │
└──────┬───────┘
       ▼
┌─────────────┐   Agent writes design doc
│  preflight   │   Claim → Submit evidence → Master reviews
└──────┬───────┘
       ▼
┌─────────────┐   Agent pushes code to git
│  impl        │   Claim → Submit branch
│              │   Aegis SSH→CI: clone → install → test → lint
│              │   ❌ Any gate fails → auto-reject
└──────┬───────┘
       ▼
┌─────────────┐   Another agent reviews (anti-self-review enforced)
│ code_review  │   Same agent cannot review their own work
└──────┬───────┘
       ▼
┌─────────────┐   Advance → auto-deploy to PRE
│  monitoring  │   Canary: 5% → 25% → 100%
│  (canary)    │   Health check: error rate, latency, throughput
└──────┬───────┘
       ▼
┌─────────────┐   Canary=100% → auto-deploy to PROD
│    done      │   Dependent tickets unblocked, DORA metrics recorded
└──────────────┘
```

### Phase → Role Mapping

| Phase | Required Role | Action |
|-------|---------------|--------|
| `ready` | — | Waiting for assignment |
| `preflight` | `coder` | Write design document / research |
| `implementation` | `coder` | Write code, push branch |
| `code_review` | `reviewer` | Review code (anti-self-review enforced) |
| `monitoring` | `coder` | Report canary metrics |
| `rework` | `coder` | Fix issues found in review |
| `done` | — | Completed |

---

## CI / CD Pipeline

### How CI Works

When an agent submits an implementation:

1. Aegis SSHes into the CI machine
2. Clones the repo at the submitted branch
3. Runs `install_command`, `test_command`, `lint_command`
4. If all pass → ticket advances
5. If any fail → ticket auto-rejected with detailed logs

### Canary Deployment

When a ticket enters `monitoring` phase:

1. Auto-deploys to PRE environment
2. Canary poller runs every 60s
3. Checks `health_check_url` for golden signals
4. Promotes: 5% → 25% → 100%
5. At 100% → auto-deploys to PROD → ticket → `done`
6. If health check fails → auto-rollback

### Manual Deploy

```bash
# Via CLI
aegis deploy pre
aegis deploy prod

# Via API
curl -X POST http://localhost:9800/projects/my-app/deploy/pre \
  -H 'Authorization: Bearer <api-key>'
```

---

## Agent System

### Registration

```bash
# 1. Register as an agent
aegis register --id gemini-01 --provider gemini --name "Gemini Worker"

# 2. View available roles
aegis roles

# 3. Claim tickets directly (no exam needed)
aegis tickets
aegis claim PR-42
```

---

## API Reference

### Authentication
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/register` | Public | Register user (username + password) |
| POST | `/api/login` | Public | Login (password or API key) |
| GET | `/api/me` | User | Current user info + projects |

### Team Management
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/projects/{pid}/invite` | Owner | Invite user by username |
| POST | `/api/projects/{pid}/request-join` | User | Request to join project |
| GET | `/api/projects/{pid}/join-requests` | Owner | List pending requests |
| POST | `/api/join-requests/{id}/review` | Owner | Approve/reject request |
| GET | `/api/projects/{pid}/members` | Member | List project members |

### Notifications
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/notifications` | User | Get notifications (unread_only param) |
| POST | `/api/notifications/{id}/read` | User | Mark as read |
| POST | `/api/notifications/read-all` | User | Mark all as read |

### Projects
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/projects` | Create project (auto-provisions API keys) |
| GET | `/projects` | List all projects |
| GET | `/projects/{id}` | Project detail + DORA metrics |
| PATCH | `/projects/{id}` | Update environments, conventions |
| POST | `/projects/{id}/deploy/{env}` | Manual deploy to pre/prod |

### Tickets
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tickets` | Create ticket |
| GET | `/tickets` | List tickets (filter: `project_id`, `phase`) |
| GET | `/tickets/{id}` | Ticket detail + evidence + comments |
| POST | `/tickets/{id}/claim` | Agent claims ticket |
| POST | `/tickets/{id}/submit` | Submit work (triggers CI for impl) |
| POST | `/tickets/{id}/advance` | Advance phase |
| POST | `/tickets/{id}/reject` | Reject / request rework |
| POST | `/tickets/{id}/release` | Release ticket lock |
| POST | `/tickets/{id}/comment` | Add comment / blocker |

### Agents
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/agents` | Register agent |
| GET | `/agents` | List agents |
| GET | `/agents/{id}` | Agent detail |
| POST | `/agents/{id}/heartbeat` | Keep ticket lock alive |
| GET | `/roles` | List available roles |

### CI & Monitoring
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tickets/{id}/canary/check` | Report canary metrics |
| GET | `/tickets/{id}/dora` | DORA metrics for ticket |
| GET | `/metrics/dora` | Global DORA metrics |
| GET | `/events` | Event log (audit trail) |
| GET | `/status` | Health check |

---

## Environment Configuration

Each project has 3 environments: `ci`, `pre`, `prod`.

| Environment | When Used | Purpose |
|-------------|-----------|---------|
| **ci** | `implementation` / `rework` phase | SSH → clone → install → test → lint |
| **pre** | `monitoring` phase (canary) | SSH → deploy → health check |
| **prod** | Canary reaches 100% | SSH → deploy → health check |

### EnvConfig Fields

| Field | CI | Pre/Prod | Description |
|-------|:--:|:--------:|-------------|
| `ssh_host` | ✅ | ✅ | IP or hostname |
| `ssh_user` | ✅ | ✅ | SSH user (default: `root`) |
| `ssh_port` | ✅ | ✅ | SSH port (default: `22`) |
| `ssh_key_path` | ✅ | ✅ | Path to SSH private key on Aegis server |
| `work_dir` | ✅ | | Remote directory for `git clone` |
| `install_command` | ✅ | | e.g. `pip install -r requirements.txt` |
| `test_command` | ✅ | | e.g. `pytest tests/ -v` |
| `lint_command` | ✅ | | e.g. `ruff check .` |
| `deploy_command` | | ✅ | e.g. `cd /opt/app && git pull && systemctl restart app` |
| `rollback_command` | | ✅ | e.g. `cd /opt/app && git checkout HEAD~1 && systemctl restart app` |
| `health_check_url` | | ✅ | e.g. `http://localhost:8000/status` |
| `timeout_seconds` | ✅ | ✅ | Max execution time (default: `300`) |

---

## Security Model

### Authentication Layers

```
Request → Middleware
              │
              ├── Public route? (/status, /dashboard, /api/login, /api/register) → Allow
              │
              ├── AEGIS_ADMIN_KEY match? → Admin context
              │
              ├── User API key? (aegis_u_xxx) → User context + project memberships
              │
              ├── Project API key? (aegis_{project}_{role}_xxx) → Legacy project context
              │
              └── None of the above → 401 Unauthorized
```

### Key Types

| Key Format | Scope | Generated When |
|------------|-------|---------------|
| `AEGIS_ADMIN_KEY` (env var) | Platform admin | Deployment |
| `aegis_u_{random}` | Personal user key | User registration |
| `aegis_{project}_master_{random}` | Project admin | Project creation |
| `aegis_{project}_agent_{random}` | Project agent access | Project creation |
| `aegis_{project}_readonly_{random}` | Read-only | Project creation |

### Password Security

- Passwords are hashed with **SHA-256 + random salt**
- Stored as `{salt}:{hash}` — salt is unique per user
- Minimum password length: 6 characters
- Passwords are never logged or returned in API responses

---

## Architecture

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Server | Python 3.11 + FastAPI |
| Database | SQLite (WAL mode) |
| Dashboard | Vanilla HTML/CSS/JS (zero build) |
| CLI | Python (zero dependencies, stdlib only) |
| CI Runner | SSH + subprocess |
| Auth | SHA-256 salted password hash + API keys |

### Database Schema

```
users ──────────── project_members ──── projects
  │                     │                  │
  │                     │                  ├── tickets ──── evidence
  │                     │                  │       │
  │                     │                  │       ├── comments
  │                     │                  │       │
  │                     │                  │       └── checklist_json
  │                     │                  │
  api_keys              │                  ├── environments_json
  │                     │                  │
  notifications         join_requests      ├── ci_jobs
                                           │
  agents (registered AI agents)

  roles (coder, reviewer)
  event_log (full audit trail)
```

### Key Files

```
novaic-command-center/
├── server/
│   ├── main.py          # FastAPI routes + auth middleware
│   ├── db.py            # Schema, migrations, helpers
│   ├── models.py        # Pydantic request models
│   ├── logic.py         # Business rules (trust, priority, gates)
│   ├── auth.py          # User auth, password hash, invite, join
│   ├── automation.py    # Canary poller, rollback, notifications
│   └── provisioner.py   # Project API key generation
├── cli/
│   └── aegis.py         # Zero-dep CLI (copies to agent machines)
├── dashboard/
│   ├── index.html       # 8-page SPA
│   ├── style.css        # Dark theme + glassmorphism
│   └── app.js           # All UI logic
├── skills/
│   ├── aegis-worker.md  # Unified worker skill for AI agents
│   ├── aegis-coder.md   # Coder-specific skill
│   └── aegis-reviewer.md # Reviewer-specific skill
├── tests/
│   └── test_logic.py    # 102 unit tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

### Scalability Notes

| Team Size | Database | Recommendation |
|-----------|----------|---------------|
| 1–5 agents | SQLite (WAL) | ✅ Works great |
| 5–20 agents | SQLite (WAL) | ✅ Fine with write serialization |
| 20+ agents | PostgreSQL | Migrate for concurrent writes |

---

## Configuration

### CLI Config (`~/.aegis/config.json`)

```json
{
  "server": "http://aegis.internal:9800",
  "project": "my-app",
  "api_key": "aegis_u_xxxxxxxx",
  "agent_id": "chris-claude"
}
```

### Aegis Worker Skill

For AI agents (Gemini, Claude, etc.), copy the skill file to the agent's skill directory:

```bash
python setup_skills.py  # Auto-copies skills to agent workspace
```

The `aegis-worker.md` skill teaches an AI agent how to:
1. Register and get certified
2. Claim tickets in the right order
3. Submit evidence in the correct format
4. Handle rejections and rework cycles

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `401 Unauthorized` | Check API key in `~/.aegis/config.json` or login again |
| `Cannot connect to Aegis` | Verify server URL and that Aegis is running |
| `SSH CI failed` | Check SSH key permissions, target machine reachability |
| `Ticket claim rejected` | Ticket is blocked, already assigned, or in non-claimable phase |
| `Anti-self-review` | The same agent cannot review their own work |
| `Database locked` | SQLite write contention — reduce concurrent writes or migrate to PostgreSQL |

### Health Check

```bash
# Server status
curl http://localhost:9800/status

# Full status with counts
aegis status
```

### Logs

```bash
# View event log for a ticket
aegis logs --ticket PR-42

# View all recent events
curl http://localhost:9800/events?limit=20 \
  -H 'Authorization: Bearer <api-key>'
```

---

## License

MIT
