# NovAIC Command Center

AI-Native 研发指挥中心 — 独立 HTTP 服务，基于 SQLite。

Agent 通过 HTTP API 认领、提交工单，System (CC) 自动执行验证，Human 通过 CLI/Dashboard 操控全局。

## Quick Start

```bash
pip install -r requirements.txt
python -m server.main          # 默认 http://127.0.0.1:9800
```

```bash
# 运行测试（74 tests, zero mock, <0.3s）
python -m pytest tests/test_logic.py -v

# E2E 生命周期测试
python tests/e2e_lifecycle.py
```

---

## 组织架构

```mermaid
graph TB
    Human["🧑 Human Operator<br/>最终决策者"]
    Master["👑 Master<br/>拆票 · 定 Spec · 放行"]
    Coder["⚙️ Coder<br/>写代码 · 写测试"]
    Reviewer["🔍 Reviewer<br/>审代码 · 审方案"]
    QA["🧪 QA<br/>验行为 · 跑 E2E"]
    Deployer["🚀 Deployer<br/>部署 · 监控"]
    System["🛡️ System (CC)<br/>自动验证 · 不可覆盖"]

    Human -->|创建票 / 覆盖决策| Master
    Master -->|分配| Coder
    Master -->|分配| Reviewer
    Master -->|分配| QA
    Master -->|分配| Deployer
    Coder -->|提交 repo_path| System
    System -->|pytest · lint · kill_test · spec_cov| System
    Reviewer -.->|语义审查| Coder
    QA -.->|行为验证| Coder
    Deployer -->|健康检查| System
```

## 六个角色

### 👑 Master — 产品负责人

| 能做 | 不能做 |
|------|--------|
| 创建 ticket（拆需求） | 写代码 |
| 定义 test_specs（WHAT to test） | 写测试代码 |
| 定义 checklist + `[unit]`/`[e2e]` 标签 | claim 非 master 阶段的票 |
| advance ticket | 跳过 CI gate |
| reject ticket（任何阶段） | 给自己加 trust |
| 设定 `domain` / `risk_level` / `priority` | — |

**创建 ticket 示例：**
```json
{
  "id": "PR-20",
  "title": "提取 parse_send_payload 纯函数",
  "priority": 4,
  "risk_level": "high",
  "domain": "python",
  "checklist": [
    "提取 parse_send_logic.py [unit]",
    "改造 send_action 为 glue 层 [e2e]"
  ],
  "test_specs": [
    {"input": "空消息",            "expect": "ValueError"},
    {"input": "text='hello'",     "expect": "ParsedMessage.text == 'hello'"},
    {"input": "image/png 附件",   "expect": "modality == 'image'"}
  ]
}
```

> `test_specs` 是 Master 对 Coder 的考试大纲——Coder 必须写出覆盖这些 spec 的测试，CC 会自动验证。

---

### ⚙️ Coder — 实现者

| 能做 | 不能做 |
|------|--------|
| claim `implementation` / `rework` / `preflight` | review 自己写的代码 |
| 写代码 + 写测试（HOW to test） | 修改 test_specs |
| submit 时提交 `repo_path` | 伪造 evidence（CC 自己跑） |
| 解决 blocker comments | advance 票到下一阶段 |

**提交方式（唯一）：**
```json
POST /tickets/PR-20/submit
{
  "agent_id": "antigravity-gemini",
  "repo_path": "/path/to/repo"
}
// → CC 自己跑 pytest + lint + kill_test + spec_coverage
// → 结果写入 evidence, agent_id = "system"
// → verification_mode: "system_executed"
```

---

### 🔍 Reviewer — 审查者

| 能做 | 不能做 |
|------|--------|
| claim `code_review` / `preflight_review` / `design_review` | review 同 agent 写的代码 |
| submit approval / rejection | review 同 provider 写的代码 |
| 添加 blocker comment | advance 票 |
| 评估测试是否覆盖了 spec | 跳过 CI gate |

**防自审规则（硬性，系统强制）：**

```
antigravity-gemini 写的代码：
  ├── antigravity-gemini 审  → ❌ 同 agent
  ├── gemini-reviewer 审     → ❌ 同 provider (gemini)
  └── cursor-claude 审       → ✅ 不同 provider
```

---

### 🧪 QA — 质量验证者

| 能做 | 不能做 |
|------|--------|
| claim `qa` 阶段的票 | 看代码实现 |
| 独立跑 E2E 场景 | 修改代码 |
| 验证行为是否符合 spec | advance 票 |
| 报告 blocker | 跳过 CI gate |

---

### 🚀 Deployer — 部署者

| 能做 | 不能做 |
|------|--------|
| claim `deploy_prep` 和 `monitoring` | 修改代码 |
| 执行部署 | 跳过 monitoring |
| 提交健康检查证据 | advance 票 |
| 触发回滚 | — |

**monitoring 必须提交的证据：**
```
health_check  — 服务响应 200
error_rate    — 部署前后 error rate 对比
```

---

### 🛡️ System (CC) — 自动验证引擎

| 能做 | 不能做 |
|------|--------|
| `subprocess.run(pytest)` | 判断代码"好不好" |
| `subprocess.run(lint_purity)` | 判断架构是否合理 |
| 自动 kill_test（变异测试） | 理解业务语义 |
| 验证 spec_coverage | 被任何角色 override |
| 自动触发 post-mortem | — |

> ⚠️ System 的 gate 没有 override 接口。没有人类、没有 Master、没有任何 agent 可以绕过。

---

## Ticket 生命周期

```
                                  ┌─────────────────────────────┐
                                  │  Master 创建 ticket          │
                                  │  test_specs + checklist      │
                                  │  domain + risk_level         │
                                  └──────────┬──────────────────┘
                                             │
                                             ▼
                                          ready
                                             │
                                    Coder claim
                                             │
                         ┌───────────────────┴───────────────────┐
                         │ skip_preflight?                        │
                         │  YES                          NO       │
                         ▼                               ▼
                   implementation                    preflight
                                                         │
                                                Coder submit
                                                         │
                                                         ▼
                                                preflight_review
                                                         │
                                                Master advance
                                                         │
                                        ┌────────────────┴──────┐
                                        │ design_review needed?  │
                                        │  risk=high/critical    │
                                        │  OR priority≥4         │
                                        │  OR scope≥3 modules    │
                                        │  YES           NO      │
                                        ▼                ▼
                                  design_review    implementation
                                        │                │
                                  Reviewer submit        │
                                        │                │
                                        ▼                │
                                  implementation ◄───────┘
                                        │
                                   Coder claim
                               Coder submit (repo_path)
                                        │
                           ┌────────────┤
                           │   SYSTEM   │
                           │ ┌────────┐ │
                           │ │ pytest │ │
                           │ │  lint  │ │
                           │ │ kill   │ │
                           │ │ spec   │ │
                           │ └────────┘ │
                           │ fail → 400 │
                           └─────┬──────┘
                                 │ 全部 pass
                                 ▼
                            code_review
                                 │
                       Reviewer claim (防自审)
                       Reviewer submit
                                 │
                         ┌───────┴───────┐
                         │ approve?       │
                         │ YES      NO    │
                         ▼         ▼
                        qa      rework
                         │    (review_rounds++)
                         │    ≥2次 → 自动 post-mortem
                    QA claim
                    QA submit
                         │
                         ▼
                    merge_ready
                         │
                   Master advance
                         │
                         ▼
                    deploy_prep
                         │
                   Deployer claim
                   Deployer submit
                         │
                         ▼
                     monitoring ← 30 分钟健康检查窗口
                         │
                   Deployer submit
                   (health_check + error_rate)
                         │
                         ▼
                       done
```

## 11 层防线

| # | 层 | 防什么 | 谁执行 | 可绕过？ |
|---|-----|--------|--------|---------|
| 1 | 认证考试 | 不合格 agent 上岗 | System | ❌ |
| 2 | 防自审 | 自己 review 自己 | System | ❌ |
| 3 | 防同源 | 同 provider 互审 | System | ❌ |
| 4 | lint_purity | `_logic.py` 引入 I/O | CC subprocess | ❌ |
| 5 | pytest | 测试不通过 | CC subprocess | ❌ |
| 6 | kill_test | 假测试（删函数不变红） | CC subprocess | ❌ |
| 7 | spec_coverage | 测试不覆盖 Master 的 spec | CC subprocess | ❌ |
| 8 | Design Review | 方案方向性错误 | Reviewer | ❌（高风险票必须走） |
| 9 | Code Review | 代码质量 / 架构合理性 | Reviewer（人类判断） | ❌ |
| 10 | Monitoring | 线上健康 | Deployer + evidence | ❌ |
| 11 | Post-mortem | 同类错误反复出现 | System 自动触发 | ❌ |

## 信任机制

### Trust 计算

```python
# 成功提交：base_delta × (priority / 5)
submit  →  +0.02 × (priority / 5)   # priority 1 → +0.004, priority 5 → +0.02

# 被 reject：
reject  →  -0.03 × (priority / 5)   # 惩罚同样按 priority 加权

# 假测试被发现：
fake_test → -0.10 (test_quality)

# 刷分防护：
# 刷 100 个 priority=1 的票 = 做 20 个正常票的 trust
```

### Domain Trust（技能匹配）

```json
{
  "antigravity-gemini": {
    "trust": {"code_quality": 0.85, "commit_discipline": 0.90},
    "domain_trust": {"python": 0.85, "typescript": 0.42, "infra": 0.60}
  }
}
```

| 规则 | 值 |
|------|-----|
| 新 domain 默认 trust | 0.50 |
| claim 最低 domain trust | 0.30 |
| trust < 0.30 时 | 系统拒绝 claim |

### Post-Mortem 自动触发

```
reject 2 次 → 自动分析 blocker_comments 的模式
  ├── 假测试 pattern     → action: 更新考试题
  ├── 架构问题 pattern   → action: 强制 design_review
  ├── 范围蔓延 pattern   → action: 要求 scope 审批
  ├── 可测性违规 pattern → action: 更新 lint 规则
  └── 未分类             → action: 人工 post-mortem
```

## DORA 指标

```
GET /metrics/dora?window_days=30

{
  "deployment_frequency": "0.033 per day",
  "lead_time":            "2.3 hours",
  "change_failure_rate":  "12.5%",
  "mttr":                 "0.8 hours"
}
```

| DORA 等级 | 部署频率 | 前置时间 | 失败率 | 恢复时间 |
|----------|---------|---------|--------|---------|
| 🏆 Elite | 按需，每天多次 | < 1 天 | 0-15% | < 1 小时 |
| ✅ High | 每天~每周 | 1天~1周 | 16-30% | < 1 天 |
| ⚠️ Medium | 每周~每月 | 1周~1月 | 16-30% | < 1 周 |
| ❌ Low | 每月~半年 | 1月~半年 | 46-60% | > 半年 |

## Agent ID 命名规范

```
{tool}-{model}

  antigravity-gemini    ← Antigravity 工具 + Gemini 模型
  cursor-claude         ← Cursor 工具 + Claude 模型
  cline-gpt             ← Cline 工具 + GPT 模型
  human-operator        ← 人类
```

Provider 从 agent_id 后半部分提取，用于防自审。

## API 端点总览

| 端点 | 方法 | 用途 |
|------|------|------|
| `/roles` | GET | 查看所有角色 |
| `/roles/{role}/exam` | POST | 参加考试 |
| `/certifications/{agent}/{role}/grade` | POST | 阅卷 |
| `/agents` | POST/GET | 注册/查看 agent |
| `/tickets` | POST/GET | 创建/查看 ticket |
| `/tickets/{id}/claim` | POST | 认领 ticket |
| `/tickets/{id}/submit` | POST | 提交（带 repo_path = CC 自动验证） |
| `/tickets/{id}/reject` | POST | 驳回（自动触发 post-mortem） |
| `/tickets/{id}/advance` | POST | 推进阶段（仅 master） |
| `/metrics/dora` | GET | DORA 四项指标 |
| `/post-mortems` | GET | 查看所有 post-mortem |
| `/post-mortems/{ticket_id}` | GET | 分析指定 ticket |
| `/attention` | GET | 需要关注的事项 |
| `/status` | GET | 系统状态总览 |
| `/events` | GET | 事件日志 |

## 架构原则

| 原则 | 实现 |
|------|------|
| **机械检查自动化** | CI Runner (pytest/lint/kill_test/spec) |
| **语义判断留给人** | Reviewer 审架构 / QA 验行为 |
| **不可覆盖的底线** | System gate 没有 override 接口 |
| **信任要赢得** | 考试 → 做票 → trust 缓慢上升 |
| **作弊有代价** | 假测试 -0.10 / reject -0.03 |
| **WHAT 和 HOW 分离** | Master 定 spec / Coder 写实现 |
| **三权分立** | 写的人不审 / 审的人不放行 / 系统不可跳过 |
| **从失败中学习** | Post-mortem 自动分析 → 更新规则 |
| **度量驱动改进** | DORA 指标实时可查 |
| **技能匹配** | Domain trust 按领域积累 |

## 项目结构

```
novaic-command-center/
├── server/
│   ├── main.py           # FastAPI 路由层（glue）
│   ├── logic.py           # 纯业务逻辑（零 I/O，100% 单测）
│   ├── ci_runner.py       # CI Runner — CC 自己执行验证
│   ├── models.py          # Pydantic 模型
│   └── db.py              # SQLite schema + 配置常量
├── tests/
│   ├── test_logic.py      # 74 unit tests (zero mock, <0.3s)
│   └── e2e_lifecycle.py   # 全流程 E2E 测试
├── data/
│   └── command-center.db  # SQLite 数据库（自动创建）
└── README.md
```
