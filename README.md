# Aegis — AI-Native Engineering Governance Platform

Aegis is a centralized command center that lets **one person lead a team of AI agents** to build, test, review, and deploy software — with the same rigor as a top-tier engineering organization.

## What Aegis Does

```
You (Master) → Aegis → Agent Team → Code → CI → Deploy
                 ↓
          Everything audited, enforced, automated
```

- **Ticket Lifecycle**: `ready → preflight → implementation → code_review → monitoring → done`
- **CI via SSH**: Aegis SSHes into your ECS, clones the repo, runs tests — agents can't fake results
- **Auto-Deploy**: Canary promotion auto-deploys to pre/prod
- **Trust System**: Agents earn trust through certifications and successful deliveries
- **Anti-Cheating**: Kill tests, spec coverage, cross-provider code review

## Quick Start

```bash
# 1. Start Aegis
cd novaic-command-center
pip install -r requirements.txt
python -m server.main --host 0.0.0.0 --port 9800

# 2. Check health
curl http://localhost:9800/status
```

## Setup a Project

### Step 1: Create Project with Environments

```bash
curl -X POST http://localhost:9800/projects \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "my-app",
    "name": "My Application",
    "repo_url": "https://github.com/your-org/my-app.git",
    "tech_stack": ["python"],
    "master_id": "master-agent",
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

Response:
```json
{
  "id": "my-app",
  "api_keys": {
    "master": "aegis_my-app_master_xxx",
    "agent": "aegis_my-app_agent_yyy",
    "readonly": "aegis_my-app_readonly_zzz"
  },
  "environments": { "ci": {...}, "pre": {...}, "prod": {...} }
}
```

### Step 2: Update Environment Later

```bash
curl -X PATCH http://localhost:9800/projects/my-app \
  -H 'Content-Type: application/json' \
  -d '{
    "environments": {
      "ci": { "ssh_host": "new-ci-host.com", ... }
    }
  }'
```

### Step 3: Prepare Your ECS Machines

**CI Machine** (10.0.1.1):
```bash
sudo apt install git python3 python3-pip -y
mkdir -p /opt/aegis-ci
```

**Pre/Prod Machines** (10.0.1.2, 10.0.1.3):
```bash
# Your app should be deployed here already
# deploy_command will git pull + restart the service
sudo apt install git curl -y
```

**SSH Keys** — from the Aegis server:
```bash
ssh-copy-id deploy@10.0.1.1
ssh-copy-id deploy@10.0.1.2
ssh-copy-id deploy@10.0.1.3
```

## Full Ticket Lifecycle

```
┌─────────────┐   Agent takes exam
│   ready      │   ←── Ticket created, waiting for assignment
└──────┬───────┘
       ▼
┌─────────────┐   Agent writes design doc
│  preflight   │   ←── Claim → Submit evidence → Master reviews
└──────┬───────┘
       ▼
┌─────────────┐   Agent pushes code to git
│  impl        │   ←── Claim → Submit branch
│              │       Aegis SSH→CI: clone → install → test → lint
│              │       ❌ Any gate fails → reject
└──────┬───────┘
       ▼
┌─────────────┐   Different agent reviews (cross-provider enforced)
│ code_review  │   ←── Anti-self-review: gemini can't review gemini's code
└──────┬───────┘
       ▼
┌─────────────┐   Advance to monitoring → auto-deploy to PRE
│  monitoring  │   ←── Canary: 5% → 25% → 100%
│  (canary)    │       Report metrics → Aegis promotes/rollbacks
│              │       Health check: 4 golden signals
└──────┬───────┘
       ▼
┌─────────────┐   Canary=100% → auto-deploy to PROD
│    done      │   ←── Full rollout, trust updated, unblocks dependents
└──────────────┘
```

## API Reference

### Projects
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/projects` | Create project (auto-provisions API keys) |
| GET | `/projects` | List all projects |
| GET | `/projects/{id}` | Get project detail + DORA metrics |
| PATCH | `/projects/{id}` | Update environments, conventions, etc. |
| POST | `/projects/{id}/deploy/{env}` | Manual deploy to pre/prod |

### Tickets
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tickets` | Create ticket under a project |
| GET | `/tickets` | List tickets (filter by project, phase) |
| GET | `/tickets/{id}` | Get ticket detail with evidence |
| POST | `/tickets/{id}/claim` | Agent claims ticket |
| POST | `/tickets/{id}/submit` | Submit work (triggers SSH CI for impl) |
| POST | `/tickets/{id}/advance` | Master advances phase (auto-deploys to pre on monitoring) |
| POST | `/tickets/{id}/release` | Agent releases ticket |

### CI / Deploy / Monitoring
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tickets/{id}/canary/check` | Report canary metrics → promote/hold/rollback |
| POST | `/tickets/{id}/rollback/check` | Check if auto-rollback should trigger |
| POST | `/projects/{id}/deploy/{env}` | SSH deploy to pre or prod |

### Agents & Trust
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/agents` | Register agent |
| GET | `/roles` | List available roles |
| GET | `/roles/{id}/exam` | Get exam questions |
| POST | `/roles/{id}/exam` | Submit exam answers |
| POST | `/certifications/{agent}/{role}/grade` | Grade exam |

### Governance
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tickets/{id}/dora` | DORA metrics for a ticket |
| POST | `/tickets/{id}/check-deps` | Dependency pinning + CVE scan |
| POST | `/tickets/{id}/check-owners` | File-level ownership validation |
| GET | `/status` | System health check |

## Environment Configuration

Each project has 3 environments:

| Environment | When Used | Purpose |
|-------------|-----------|---------|
| **ci** | `implementation` / `rework` phase | SSH → clone → install → test → lint |
| **pre** | `monitoring` phase (canary) | SSH → deploy → health check |
| **prod** | Canary reaches 100% | SSH → deploy → health check |

### EnvConfig Fields

| Field | CI | Pre/Prod | Description |
|-------|-----|---------|-------------|
| `ssh_host` | ✅ | ✅ | IP or hostname |
| `ssh_user` | ✅ | ✅ | SSH user |
| `ssh_port` | ✅ | ✅ | Default: 22 |
| `ssh_key_path` | ✅ | ✅ | Path to SSH key on Aegis server |
| `work_dir` | ✅ | | Remote directory for git clone |
| `install_command` | ✅ | | e.g. `pip install -r requirements.txt` |
| `test_command` | ✅ | | e.g. `pytest tests/ -v` |
| `lint_command` | ✅ | | e.g. `ruff check .` |
| `deploy_command` | | ✅ | e.g. `cd /opt/app && git pull && systemctl restart app` |
| `health_check_url` | | ✅ | e.g. `http://localhost:8000/status` |
| `timeout_seconds` | ✅ | ✅ | Max execution time |

## Architecture

```
You (人类)
  │
  │  创建项目 / 配置环境 / 审批关键决策
  ▼
┌──────────────────────────────────────────────┐
│                   Aegis                       │
│                                              │
│  Projects ─── Tickets ─── Agents ─── Trust   │
│      │            │           │               │
│  Environments  CI Runner    Certs             │
│  (ci/pre/prod)  (SSH)     (Exams)            │
│      │            │                          │
│  Auto-Deploy   Canary    Rollback            │
└──────────────────────────────────────────────┘
  │          │          │
  ▼          ▼          ▼
ECS-CI    ECS-Pre    ECS-Prod
(test)    (canary)   (live)
```

## License

MIT
""", "Description": "Complete README with quick start, API reference, environment setup, and architecture diagram"
