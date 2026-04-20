# Aegis API 详细文档

本文档详细说明每个 API 端点的请求格式、响应格式和使用示例。

> **Base URL**: `http://aegis:9800`  
> **Auth**: 除 Public 路由外，所有请求需要 `Authorization: Bearer <api-key>` header。

---

## 目录

- [认证](#认证)
- [用户管理](#用户管理)
- [团队管理](#团队管理)
- [通知中心](#通知中心)
- [项目管理](#项目管理)
- [工单管理](#工单管理)
- [Agent](#agent)
- [CI 与部署](#ci-与部署)
- [监控与指标](#监控与指标)

---

## 认证

### POST /api/register

注册新用户。**Public 路由，无需认证。**

**Request:**
```json
{
  "user_id": "chris",
  "password": "mypassword",
  "display_name": "Chris Wang",
  "email": "chris@novaic.com"
}
```

**Response (200):**
```json
{
  "user_id": "chris",
  "api_key": "aegis_u_2d2b1269d703fb1164bbba6eca83c3ea756e15f8",
  "message": "注册成功！API Key 用于 CLI，Dashboard 用账号密码登录。"
}
```

**Error (409):**
```json
{"detail": "用户名已存在"}
```

**Rules:**
- `user_id`: 必填，唯一
- `password`: 必填，最少 6 位
- `display_name`: 选填
- `email`: 选填

---

### POST /api/login

登录。支持两种方式。**Public 路由。**

**方式 1: 用户名 + 密码**
```json
{
  "user_id": "chris",
  "password": "mypassword"
}
```

**方式 2: API Key**
```json
{
  "api_key": "aegis_u_xxxxxxxx"
}
```

**Response (200):**
```json
{
  "role": "member",
  "project_id": "*",
  "user_id": "chris",
  "display_name": "Chris Wang",
  "api_key": "aegis_u_xxxxxxxx"
}
```

**Error (401):**
```json
{"detail": "用户名或密码错误"}
```

---

### GET /api/me

获取当前登录用户信息和项目成员列表。

**Response (200):**
```json
{
  "user": {
    "id": "chris",
    "display_name": "Chris Wang",
    "email": "chris@novaic.com",
    "role": "member",
    "created_at": 1776676589000
  },
  "projects": [
    {"project_id": "my-app", "role": "owner", "project_name": "My Application"},
    {"project_id": "backend", "role": "member", "project_name": "Backend Service"}
  ]
}
```

---

## 用户管理

### GET /api/notifications

获取当前用户的通知列表。

**Query Parameters:**
- `unread_only` (bool, default: false): 只返回未读
- `limit` (int, default: 50): 最大条数

**Response (200):**
```json
{
  "notifications": [
    {
      "id": 1,
      "user_id": "chris",
      "type": "join_request",
      "title": "张三 申请加入项目",
      "body": "张三 想以 member 身份加入 my-app。留言: 想参与重构工作",
      "ref_type": "join_request",
      "ref_id": "1",
      "is_read": 0,
      "created_at": 1776676589639
    }
  ],
  "unread_count": 1
}
```

### POST /api/notifications/{id}/read

标记单条通知为已读。

**Response:** `{"ok": true}`

### POST /api/notifications/read-all

标记所有通知为已读。

**Response:** `{"ok": true}`

---

## 团队管理

### POST /api/projects/{pid}/invite

Owner 通过用户名直接邀请用户加入项目。被邀请人立即加入，并收到通知。

**Request:**
```json
{
  "user_id": "zhangsan",
  "role": "member"
}
```

**Response (200):**
```json
{"message": "已邀请 zhangsan 加入 my-app", "status": "added"}
```

**Errors:**
- `404`: 用户不存在
- `400`: 用户已是成员

**Role 可选值:** `member`, `viewer`

---

### POST /api/projects/{pid}/request-join

用户申请加入项目。项目 Owner 会收到通知，在 Dashboard 或 API 上审核。

**Request:**
```json
{
  "role": "member",
  "message": "想参与网关重构"
}
```

**Response (200):**
```json
{
  "message": "申请已提交，等待项目 Owner 审核",
  "request_id": 1,
  "status": "pending"
}
```

**已是成员时:**
```json
{"message": "你已经是项目成员了", "status": "already_member"}
```

---

### GET /api/projects/{pid}/join-requests

查看项目的加入申请列表。

**Query Parameters:**
- `status` (string, default: "pending"): `pending` / `approved` / `rejected`

**Response (200):**
```json
{
  "requests": [
    {
      "id": 1,
      "project_id": "my-app",
      "user_id": "zhangsan",
      "role": "member",
      "message": "想参与网关重构",
      "status": "pending",
      "display_name": "张三",
      "email": "zs@company.com",
      "created_at": 1776676589639
    }
  ]
}
```

---

### POST /api/join-requests/{id}/review

Owner 审核加入申请。通过后自动添加为项目成员，申请人收到通知。

**Request (同意):**
```json
{"action": "approved", "note": "欢迎！"}
```

**Request (拒绝):**
```json
{"action": "rejected", "note": "项目暂不招人"}
```

**Response (200):**
```json
{"message": "✅ 已同意", "status": "approved"}
```

---

### GET /api/projects/{pid}/members

查看项目成员列表。

**Response (200):**
```json
{
  "members": [
    {
      "user_id": "chris",
      "role": "owner",
      "joined_at": 1776676589544,
      "display_name": "Chris Wang",
      "email": "chris@novaic.com"
    },
    {
      "user_id": "zhangsan",
      "role": "member",
      "joined_at": 1776676589846,
      "display_name": "张三",
      "email": "zs@company.com"
    }
  ]
}
```

---

## 项目管理

### POST /projects

创建项目。自动生成 API keys + 将 `master_id` 设为 Owner。

**Request:**
```json
{
  "id": "my-app",
  "name": "My Application",
  "repo_url": "https://github.com/org/repo.git",
  "tech_stack": ["python", "fastapi"],
  "master_id": "chris",
  "default_domain": "backend",
  "environments": {
    "ci": {
      "ssh_host": "10.0.1.1",
      "ssh_user": "deploy",
      "ssh_key_path": "~/.ssh/id_rsa",
      "work_dir": "/opt/aegis-ci",
      "install_command": "pip install -r requirements.txt",
      "test_command": "python -m pytest tests/ -v",
      "lint_command": "ruff check .",
      "timeout_seconds": 300
    },
    "pre": {
      "ssh_host": "10.0.1.2",
      "deploy_command": "cd /opt/app && git pull && systemctl restart app",
      "health_check_url": "http://localhost:8000/status"
    },
    "prod": {
      "ssh_host": "10.0.1.3",
      "deploy_command": "cd /opt/app && git pull && systemctl restart app",
      "health_check_url": "http://localhost:8000/status"
    }
  }
}
```

**Response (200):**
```json
{
  "id": "my-app",
  "name": "My Application",
  "master_id": "chris",
  "repo_url": "https://github.com/org/repo.git",
  "api_keys": {
    "master": "aegis_my-app_master_xxx",
    "agent": "aegis_my-app_agent_yyy",
    "readonly": "aegis_my-app_readonly_zzz"
  },
  "environments": {"ci": {...}, "pre": {...}, "prod": {...}}
}
```

---

### GET /projects

列出所有项目。

**Query:** `?status=active`

**Response:** `{"projects": [...]}`

---

### GET /projects/{id}

获取项目详情，包含工单摘要和 DORA 指标。

---

### PATCH /projects/{id}

更新项目配置（环境、约定等）。

---

### POST /projects/{id}/deploy/{env}

手动触发部署到指定环境。

**Path:** `env` = `pre` | `prod`

---

## 工单管理

### POST /tickets

创建工单。

**Request:**
```json
{
  "id": "PR-42",
  "project_id": "my-app",
  "title": "Refactor message handler",
  "description": "Split message_actions.py into smaller modules",
  "priority": 3,
  "domain": "backend",
  "requires_roles": ["coder"],
  "depends_on": ["PR-40", "PR-41"],
  "test_specs": ["test_message_create", "test_message_error_handling"],
  "checklist": [
    "Split into 3 modules",
    "No breaking changes to public API",
    "100% test coverage for new modules"
  ]
}
```

---

### GET /tickets

列出工单。

**Query Parameters:**
- `project_id`: 按项目过滤
- `phase`: 按阶段过滤
- `assigned_to`: 按 agent 过滤
- `domain`: 按领域过滤

---

### GET /tickets/{id}

获取工单详情，包含 evidence、comments、DORA 指标。

---

### POST /tickets/{id}/claim

Agent 认领工单。

**Request:**
```json
{"agent_id": "gemini-01"}
```

**Rules:**
- Agent 不能同时认领多个工单（configurable）
- 工单依赖必须已完成
- 不能自审（同一 agent 不能 review 自己的代码）

---

### POST /tickets/{id}/submit

提交工作成果。

**Implementation 阶段:**
```json
{
  "agent_id": "gemini-01",
  "branch": "feat/pr42-refactor",
  "evidence": "Refactored into 3 modules with full test coverage"
}
```
> 自动触发 SSH CI: clone → install → test → lint

**Preflight 阶段:**
```json
{
  "agent_id": "gemini-01",
  "evidence": "Analyzed codebase: 3 modules identified..."
}
```

**Code Review 阶段:**
```json
{
  "agent_id": "claude-reviewer",
  "verdict": "pass",
  "evidence": "Code quality excellent, tests comprehensive"
}
```

---

### POST /tickets/{id}/advance

推进工单到下一阶段。

**Request:**
```json
{"target_phase": "code_review", "reviewer_id": "master"}
```

---

### POST /tickets/{id}/reject

拒绝/退回工单。

**Request:**
```json
{"reason": "Tests don't cover edge case X", "reviewer_id": "master"}
```

---

## Agent

### POST /agents

注册 Agent。

**Request:**
```json
{
  "id": "gemini-01",
  "name": "Gemini Worker Alpha",
  "provider": "gemini",
  "model": "gemini-2.5-pro",
  "project_id": "my-app",
  "webhook_url": "http://agent:3000/hook"
}
```

---

### GET /roles

列出所有可认证的角色。

**Response:**
```json
{
  "roles": [
    {
      "id": "coder",
      "display_name": "Coder",
      "description": "写代码、写测试、调试"
    },
    {
      "id": "reviewer",
      "display_name": "Reviewer",
      "description": "代码审查、质量把关"
    }
  ]
}
```


---

## CI 与部署

### CI 自动触发

当 Agent 在 `implementation` 或 `rework` 阶段提交 `branch` 时，Aegis 自动：

1. SSH 到 CI 机器
2. `git clone <repo> -b <branch>`
3. 执行 `install_command`
4. 执行 `test_command`
5. 执行 `lint_command`
6. 全部通过 → evidence 记录为 `system_executed`
7. 任何失败 → 自动 reject，日志写入 evidence

### Canary

当工单进入 `monitoring` 阶段，自动部署到 PRE 环境。

**POST /tickets/{id}/canary/check**

报告 canary 指标：
```json
{
  "error_rate": 0.01,
  "latency_p99": 120,
  "throughput": 500
}
```

自动决策：
- `error_rate < 0.05` → 提升流量百分比
- `error_rate >= 0.10` → 自动回滚
- 流量到 100% → 自动部署 PROD → 工单 → `done`

---

## 监控与指标

### GET /status

健康检查。**Public 路由。**

```json
{"projects": 3, "tickets": 47, "agents": 8, "roles": 5}
```

### GET /metrics/dora

全局 DORA 指标。

### GET /events

事件日志（审计链）。

**Query:** `?project_id=my-app&limit=50&offset=0`

```json
{
  "events": [
    {
      "id": "evt_xxx",
      "event_type": "ticket_claimed",
      "project_id": "my-app",
      "ticket_id": "PR-42",
      "agent_id": "gemini-01",
      "detail": "",
      "created_at": 1776676589000
    }
  ]
}
```
