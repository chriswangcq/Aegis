# Aegis 工作流手册

> 从注册到接单到提交，每个角色的完整操作指南。

---

## 一个人 + 一个 Agent 的开发模式

最小配置：你（Master）+ 1 个 Coder Agent + 1 个 Reviewer Agent。

```bash
# === 你（Master）创建 ticket ===
curl -X POST http://127.0.0.1:9800/tickets -H 'Content-Type: application/json' -d '{
  "id": "PR-25",
  "title": "提取 send_message 纯函数",
  "priority": 3,
  "domain": "python",
  "checklist": ["提取 parse_send_logic.py [unit]"],
  "test_specs": [
    {"input": "空消息", "expect": "ValueError"},
    {"input": "text=hello", "expect": "ParsedMessage"}
  ]
}'

# === 告诉 Coder Agent: "去接 PR-25" ===
# Agent: claim → 写代码 → submit repo_path
# Aegis 自动跑 CI → 通过 → 进入 code_review

# === 告诉 Reviewer Agent: "去审 PR-25" ===
# Reviewer: claim code_review → 审 → approve

# === 你 advance ===
curl -X POST http://127.0.0.1:9800/tickets/PR-25/advance \
  -H 'Content-Type: application/json' -d '{"target_phase":"done"}'
```

---

## Worker Agent 完整生命周期

### Step 1: 注册

```bash
curl -X POST http://127.0.0.1:9800/agents \
  -H 'Content-Type: application/json' \
  -d '{"id":"antigravity-gemini","display_name":"Gemini Coder","provider":"gemini"}'
```

### Step 2: 接单

```bash
# 查看收件箱
curl http://127.0.0.1:9800/inbox/antigravity-gemini

# 认领
curl -X POST http://127.0.0.1:9800/tickets/PR-25/claim \
  -H 'Content-Type: application/json' -d '{"agent_id":"antigravity-gemini"}'
```

### Step 3: 提交（Aegis 自动验证）

```bash
curl -X POST http://127.0.0.1:9800/tickets/PR-25/submit \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"antigravity-gemini","repo_path":"/path/to/repo"}'

# Aegis 执行：
# ✅ pytest — 53 passed
# ✅ lint_purity — 0 violations
# ✅ kill_test — all public functions properly tested
# ✅ spec_coverage — all 2 test specs covered
#
# → verification_mode: "system_executed"
# → phase: code_review
```

### Step 4: 被 reject 时

```bash
# 读 blocker comments
curl http://127.0.0.1:9800/tickets/PR-25

# 修代码 → 重新 submit
curl -X POST http://127.0.0.1:9800/tickets/PR-25/submit \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"antigravity-gemini","repo_path":"/path/to/repo"}'
```

---

## Master 的一天

```bash
# 1. 看需要关注什么
curl http://127.0.0.1:9800/attention

# 2. 推进阶段
curl -X POST http://127.0.0.1:9800/tickets/PR-25/advance \
  -H 'Content-Type: application/json' -d '{"target_phase":"implementation"}'

# 3. 打回
curl -X POST http://127.0.0.1:9800/tickets/PR-25/reject \
  -H 'Content-Type: application/json' -d '{
    "agent_id": "master",
    "reason": "测试没覆盖 edge case",
    "blocker_comments": ["空消息时应该抛 ValueError，但没有这个测试"]
  }'

# 4. 看 DORA 指标
curl http://127.0.0.1:9800/metrics/dora

# 5. 看 post-mortem（reject ≥ 2 次自动触发）
curl http://127.0.0.1:9800/post-mortems
```

---

## 关键纪律

### Coder 三问自检

1. 我勾的每一项都有可执行凭证吗？
2. 这个 commit 能独立 revert 吗？
3. 去掉生产代码这个测试会红吗？（kill_test 会帮你验证）

### Reviewer 五问审查

1. 去掉生产代码，这些测试会红吗？
2. git diff 涉及的文件是否都在 ticket scope 内？
3. commit 是否拆成了独立的 feat/test/chore/docs？
4. Evidence 中的结果是 Aegis 执行的（system_executed）还是 agent 自报的？
5. 测试是否覆盖了 Master 定义的 test_specs？

### Master 审查原则

- 每个 blocker 必须具体到"T1 照这段描述能写出可编译的代码"
- 不要 handwave（"方向对但细节不够" 不是合格的 review）
- 如果 Worker 反复犯同一个错误 → 触发 post-mortem → 更新流程文档
