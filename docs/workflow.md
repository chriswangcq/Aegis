# Command Center 工作流手册 v2

> 团队制 + 认证考试 + 三权分立的 AI-Native 组织。

---

## 组织架构

```
Human (CEO) ──── 战略方向、最终否决、风险承担
  │
  └── Master Agent (CTO) ──── 拆票、全局调度、质量监督
        │
        ├── Coder Team
        │     ├── Owner ── 出题、定标准、管团队质量
        │     ├── Interviewer ── 判卷、追问
        │     └── Workers ── 通过考试后接单写代码
        │
        ├── Reviewer Team
        │     ├── Owner ── 出 review 考题
        │     ├── Interviewer ── 评估 review 能力
        │     └── Workers ── 通过考试后做 Code Review
        │
        ├── QA Team
        │     └── ...
        │
        └── Deploy Team
              └── ...
```

---

## 一个 Worker 的完整生命周期

### Step 1: 注册（入职）

```bash
curl -X POST http://127.0.0.1:9800/agents \
  -H 'Content-Type: application/json' \
  -d '{"id":"worker-5","display_name":"Claude","provider":"claude"}'
# → 返回 next_step: "GET /roles to see available roles"
```

### Step 2: 浏览角色 → 选择方向

```bash
curl http://127.0.0.1:9800/roles
# → coder / reviewer / qa / deployer 各有描述
```

### Step 3: 读考题 → 考试

```bash
# 读题（不含答案/评分标准）
curl http://127.0.0.1:9800/roles/coder/exam
# → 4 道题：3 道开放题 + 1 道选择题

# 提交答案
curl -X POST http://127.0.0.1:9800/roles/coder/exam \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"worker-5","answers":["...","...","...","B"]}'
# → status: "pending_review"（有开放题需要 Interviewer/Master 判卷）
```

### Step 4: 等待 Interviewer 判卷

```bash
# Interviewer/Master 看到 /attention 里的 pending_exams
# Master 判卷：
curl -X POST "http://127.0.0.1:9800/certifications/worker-5/coder/grade?score=0.85&verdict=passed"
# → 认证通过！
```

### Step 5: 接单干活

```bash
# 查看收件箱（只显示你有认证的角色对应的票）
curl http://127.0.0.1:9800/inbox/worker-5
# → available: [{id:"PR-18", phase:"ready", ...}]

# 认领
curl -X POST http://127.0.0.1:9800/tickets/PR-18/claim \
  -d '{"agent_id":"worker-5"}'
# → 如果认证过期会被拒绝并提示重考

# 干活 → 提交
curl -X POST http://127.0.0.1:9800/tickets/PR-18/submit \
  -d '{"agent_id":"worker-5","evidence":[...]}'
```

### Step 6: 信任积累

每次 submit 成功 → 该角色的信任分 +0.02
每次被 reject → 信任分 -0.03
信任分低于 0.4 → 认证自动吊销

---

## Master 的一天

```bash
# 1. 检查需要关注什么
curl http://127.0.0.1:9800/attention
# → needs_review: 待审 review 的票
# → pending_exams: 待判卷的考试
# → expired_locks: 超时的 agent
# → stuck_in_rework: 打回 3 次以上的票

# 2. 判考卷
curl -X POST "http://127.0.0.1:9800/certifications/worker-5/coder/grade?score=0.85&verdict=passed"

# 3. 审 preflight / code review
curl http://127.0.0.1:9800/tickets/PR-18
# → 读 evidence + comments + open_blockers
curl -X POST http://127.0.0.1:9800/tickets/PR-18/advance \
  -d '{"target_phase":"implementation","reason":"preflight OK"}'

# 4. 创建新票
curl -X POST http://127.0.0.1:9800/tickets -d '{...}'

# 5. 沉淀知识
curl -X POST http://127.0.0.1:9800/knowledge -d '{...}'
```

---

## Human 的关注点

```bash
# 全局看板
curl http://127.0.0.1:9800/status
# → phases（各阶段票数）、agents（人员状态）、certified_per_role（各角色认证人数）

# 审计日志
curl http://127.0.0.1:9800/events?limit=20
```

只需要介入：
- 高风险 canary promote
- 架构争议
- 组织层面决策（要不要开新角色、换 Owner）

---

## 考试 = 非对称鉴权

| 概念 | 类比 |
|------|------|
| 考题 | 公钥（公开的） |
| 能力 | 私钥（不可复制的） |
| 判卷 | 验签（快速验证） |
| Owner 出题 | CA 签发 |
| 认证过期 | 密钥轮换 |
| 选择题 | 弱密钥（可暴力） |
| 开放题 + 实操 | 强密钥 |

---

## API 速查

### 认证体系
| 方法 | 端点 | 谁用 |
|------|------|------|
| GET | `/roles` | 所有人 |
| GET | `/roles/{id}/exam` | Worker |
| POST | `/roles/{id}/exam` | Worker 提交答案 |
| POST | `/certifications/{agent}/{role}/grade` | Master/Interviewer |
| GET | `/certifications/{agent}` | 所有人 |

### 工单生命周期
| 方法 | 端点 | 谁用 |
|------|------|------|
| GET | `/tickets?available=true` | Worker |
| GET | `/tickets/{id}` | 所有人 |
| POST | `/tickets` | Master |
| POST | `/tickets/{id}/claim` | Worker（需认证） |
| POST | `/tickets/{id}/submit` | Worker |
| POST | `/tickets/{id}/reject` | Master/Reviewer |
| POST | `/tickets/{id}/advance` | Master |
| POST | `/tickets/{id}/release` | Worker |

### 协作
| 方法 | 端点 | 谁用 |
|------|------|------|
| GET | `/inbox/{agent}` | Worker |
| GET | `/attention` | Master |
| GET | `/status` | Human |
| POST | `/tickets/{id}/comments` | 所有人 |
| POST | `/knowledge` | Master/Owner |
| GET | `/events` | 所有人 |
