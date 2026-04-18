# Command Center 工作流手册

> 三种角色如何与 Command Center 协作的完整说明。

---

## 角色总览

| 角色 | 是谁 | 交互方式 | 核心动作 |
|------|------|---------|---------|
| **Human** | 你（老板） | 浏览器看 `/docs` + 终端 curl | 战略决策、最终否决、风险承担 |
| **Master Agent** | 高级 AI（Claude/Gemini） | HTTP API | 拆票、review、打回、推进、知识沉淀 |
| **Worker Agent** | 执行 AI（各种模型） | HTTP API | 浏览工单、认领、编码、提交 |

---

## 一、Human 的一天

### 你只需要做 3 件事

**1. 定方向（偶尔）**

告诉 Master Agent 你要什么：
> "我想做消息推送重构"

Master 会自动拆解成 tickets 并写入 Command Center。

**2. 看看板（每天）**

```bash
# 一条命令看全局状态
curl -s http://127.0.0.1:9800/status | python3 -m json.tool
```

你关心的信号：
- `expired_locks` 不为空 → 有 agent 卡住了
- `open_blockers` 不为空 → 有 review 争议
- 某个 agent `failure_count` 很高 → 该换人或降级信任

**3. 拍板（需要时）**

只有两种情况需要你介入：
- **高风险 canary promote**：Master 会在 comment 里 @human 请求审批
- **架构争议**：Master 和 Worker 在 comments 里讨论不出结论

```bash
# 审批 canary promote
curl -X POST http://127.0.0.1:9800/tickets/PR-18/advance \
  -H 'Content-Type: application/json' \
  -d '{"target_phase":"done","reason":"human approved after 6h canary"}'
```

**你永远不需要**：写代码、写 review、管 agent 注册、管状态流转。

---

## 二、Master Agent 的工作流

Master 通过 HTTP API 和 Command Center 交互。以下是它在每个阶段的具体操作。

### 阶段 1：规划（Planning）

Human 说 "做消息推送重构" 后，Master：

```bash
# 1. 拆成多个 tickets，定义依赖
curl -X POST http://127.0.0.1:9800/tickets -H 'Content-Type: application/json' -d '{
  "id": "PR-18",
  "title": "删除 inline dispatch",
  "description": "删除所有 _dispatch_trigger 调用，subscriber 成为唯一路径",
  "priority": 3,
  "risk_level": "high",
  "depends_on": [],
  "scope_includes": ["novaic-business/business/message_actions.py"],
  "scope_excludes": ["scripts/", "docs/"],
  "checklist": ["删除 _dispatch_trigger 函数", "删除所有调用点", "保留 outbox insert", "更新测试"],
  "created_by": "master"
}'

# 2. 沉淀相关知识
curl -X POST http://127.0.0.1:9800/knowledge -H 'Content-Type: application/json' -d '{
  "id": "AD-005",
  "category": "architecture_decision",
  "title": "subscriber 是唯一投递路径",
  "content": "PR-17 canary 验证通过后，inline dispatch 必须完全删除...",
  "source_tickets": ["PR-17", "PR-18"]
}'
```

### 阶段 2：Preflight Review

Worker 提交 preflight 后，Master 自动被触发（通过轮询 `phase=preflight_review` 的 tickets）：

```bash
# 1. 看有没有待 review 的 ticket
curl -s 'http://127.0.0.1:9800/tickets?phase=preflight_review'

# 2. 读 ticket 详情（含 evidence 和 comments）
curl -s http://127.0.0.1:9800/tickets/PR-18

# 3a. 如果 OK → 推进到 implementation
curl -X POST http://127.0.0.1:9800/tickets/PR-18/advance \
  -H 'Content-Type: application/json' \
  -d '{"target_phase":"implementation","reason":"preflight 方案清晰"}'

# 3b. 如果有问题 → 打回 + 留 blocker
curl -X POST http://127.0.0.1:9800/tickets/PR-18/reject \
  -H 'Content-Type: application/json' \
  -d '{
    "reason": "scope 漂移",
    "blocker_comments": [
      "preflight 包含了 PR-19 的 HealthWorker 改动，请移除",
      "Bootstrap 方案没有查明具体的 API 端点"
    ]
  }'
```

### 阶段 3：Code Review

同样的流程，但 Master 可以选择让独立的 CR Agent 做初筛：

```bash
# CR Agent 认领 code_review
curl -X POST http://127.0.0.1:9800/tickets/PR-18/claim \
  -H 'Content-Type: application/json' -d '{"agent_id":"cr-1"}'

# CR Agent 提交 review evidence
curl -X POST http://127.0.0.1:9800/tickets/PR-18/submit \
  -H 'Content-Type: application/json' -d '{
    "agent_id": "cr-1",
    "evidence": [{"evidence_type":"review","content":"LGTM, 无假测试","verdict":"approved"}]
  }'

# 或者 CR Agent 发现问题 → reject
curl -X POST http://127.0.0.1:9800/tickets/PR-18/reject \
  -H 'Content-Type: application/json' -d '{
    "reason": "假测试",
    "blocker_comments": ["test_expired_lock 只是 mock 传参，去掉生产代码测试不会红"]
  }'
```

### 阶段 4：推进到完成

```bash
# QA 通过后，Master 审批 merge
curl -X POST http://127.0.0.1:9800/tickets/PR-18/advance \
  -d '{"target_phase":"merging","reason":"QA pass + CR approved"}'

# merge 完成后推到 canary 或直接 done
curl -X POST http://127.0.0.1:9800/tickets/PR-18/advance \
  -d '{"target_phase":"done","reason":"低风险，跳过 canary"}'
# → 自动解除 PR-19 的阻塞！
```

---

## 三、Worker Agent 的工作流

Worker 是"零工"——它启动一个 session，浏览工单，决定是否接单，干完就走。

### 典型 session

```
1. 我是谁？先注册（只需一次）
2. 有什么活？浏览可接的工单
3. 这个活我能干吗？读详情
4. 接单 → 干活 → 交付
5. 处理 review 反馈（如果被打回）
```

### 具体操作

```bash
# ① 注册（首次）
curl -X POST http://127.0.0.1:9800/agents -H 'Content-Type: application/json' \
  -d '{"id":"coder-1","role":"coder","display_name":"Gemini Pro","provider":"gemini"}'

# ② 浏览可接的工单
curl -s 'http://127.0.0.1:9800/tickets?available=true'
# 返回所有 phase 在 ready/preflight_rework/rework/... 且无阻塞的 tickets

# ③ 读详情（关键！读完再决定接不接）
curl -s http://127.0.0.1:9800/tickets/PR-18
# 返回：
#   - scope（我要改哪些文件）
#   - checklist（具体要做什么）
#   - depends_on（前置条件满足了吗）
#   - open_blockers（有没有未解决的问题）
#   - failure_patterns（我需要注意什么陷阱）
#   - comments（之前的讨论和 review 反馈）

# ④ 认领
curl -X POST http://127.0.0.1:9800/tickets/PR-18/claim \
  -H 'Content-Type: application/json' -d '{"agent_id":"coder-1"}'
# 如果是新 ticket：phase ready → preflight（写调研报告）
# 如果是返工：phase rework → rework（按 blocker 改）

# ⑤ 开始工作...（在代码仓库里写代码、跑测试）

# ⑥ 提交（附带 evidence）
curl -X POST http://127.0.0.1:9800/tickets/PR-18/submit \
  -H 'Content-Type: application/json' -d '{
    "agent_id": "coder-1",
    "evidence": [
      {"evidence_type": "stdout", "content": "pytest: 12 passed in 0.8s", "verdict": "pass"},
      {"evidence_type": "diff", "content": "3 files changed, +45 -120", "verdict": "pass"}
    ]
  }'
# 自动流转到下一阶段（preflight→preflight_review, self_test→code_review, etc.）

# ⑦ 如果被打回了，读 blocker comments
curl -s http://127.0.0.1:9800/tickets/PR-18
# 看到 open_blockers > 0，读 comments 里的 blocker 类型条目
# 重新 claim → 修改 → resolve blocker → submit
```

### Worker 放弃工单

如果 Worker 发现自己干不了（scope 太大、不熟悉领域）：

```bash
curl -X POST http://127.0.0.1:9800/tickets/PR-18/release \
  -H 'Content-Type: application/json' -d '{"agent_id":"coder-1"}'
# ticket 回到可认领状态，其他 Worker 可以接
```

### Worker 留言提问

```bash
curl -X POST http://127.0.0.1:9800/tickets/PR-18/comments \
  -H 'Content-Type: application/json' -d '{
    "author_id": "coder-1",
    "author_role": "coder",
    "content": "scope 里的 subagent.py 需要改吗？ticket 没明确说",
    "comment_type": "question"
  }'
# Master 会看到这条 question 并回复
```

---

## 四、一个完整的 Ticket 生命周期

以 PR-18 为例，展示三种角色的交互时序：

```
时间线    Human           Master              Worker (coder-1)       Worker (cr-1)
─────────────────────────────────────────────────────────────────────────────────
T0       "做切流重构"  →
T1                       POST /tickets        
                         (创建 PR-18, ready)
T2                                            GET /tickets?available
                                              GET /tickets/PR-18
                                              POST .../claim
                                              (→ preflight)
T3                                            [写 preflight 报告]
                                              POST .../submit
                                              (→ preflight_review)
T4                       GET /tickets?phase=   
                         preflight_review      
                         [读 evidence]         
                         POST .../reject       
                         (blocker: "scope 漂移")
                         (→ preflight_rework)
T5                                            GET /tickets/PR-18
                                              [看到 blocker]
                                              POST .../claim
                                              [修改 preflight]
                                              PATCH .../comments/1
                                              (resolve blocker)
                                              POST .../submit
                                              (→ preflight_review)
T6                       [re-review]
                         POST .../advance
                         (→ implementation)
T7                                            POST .../claim
                                              [写代码 + 跑测试]
                                              POST .../submit
                                              (→ self_test → code_review)
T8                                                                 GET /tickets?phase=
                                                                   code_review
                                                                   POST .../claim
                                                                   [review 代码]
                                                                   POST .../submit
                                                                   (→ qa)
T9                       POST .../advance
                         (→ merge_ready → done)
                         → PR-19 自动解除阻塞！
T10      [看 /status]
         "PR-18 done,
          PR-19 ready" ✓
```

---

## 五、Agent 的实际集成方式

Agent（无论 Master 还是 Worker）本质上是一个 AI 对话 session。集成方式有两种：

### 方式 A：在 system prompt 中注入 API 说明（推荐起步）

在 Agent 的 system prompt 中加入：

```
你是一个 Coder Agent。你可以通过以下 HTTP API 与 Command Center 交互：
- 浏览工单：GET http://127.0.0.1:9800/tickets?available=true
- 读详情：GET http://127.0.0.1:9800/tickets/{id}
- 认领：POST http://127.0.0.1:9800/tickets/{id}/claim  body: {"agent_id":"你的ID"}
- 提交：POST http://127.0.0.1:9800/tickets/{id}/submit  body: {"agent_id":"你的ID","evidence":[...]}
- 提问：POST http://127.0.0.1:9800/tickets/{id}/comments body: {"author_id":"你的ID","content":"...","comment_type":"question"}

你的 agent_id 是 "coder-1"。
工作流程：先浏览 → 读详情 → 决定是否认领 → 写代码 → 跑测试 → 提交 evidence。
```

Agent 通过 tool_use（curl/HTTP）直接调 API。这就是目前的 Gemini CLI / Antigravity 已经支持的。

### 方式 B：封装为 MCP Tool（中期）

把 API 封装成 Model Context Protocol 工具，Agent 直接调用而不需要拼 curl：

```json
{
  "name": "browse_tickets",
  "description": "浏览可认领的工单",
  "parameters": {"available": {"type": "boolean"}}
}
```

### 方式 C：Agent Runtime 集成（远期）

把 Command Center 作为 NovAIC Agent Runtime 的一个 worker 类型，Agent 的生命周期完全自动化。
