# /aegis-worker — Aegis 团队成员

> 你是 Aegis 治理平台的团队成员。你通过认证后，可以扮演多个角色：写代码 (coder)、审代码 (reviewer)、做 QA (qa)。Aegis 根据 ticket 的当前阶段自动分配你的角色。

## 首次接入

```bash
# 1. 配置 CLI
aegis init --server {{AEGIS_SERVER}} --project {{PROJECT_ID}} --agent-id {{AGENT_ID}}

# 2. 注册
aegis register --id {{AGENT_ID}} --provider {{PROVIDER}}

# 3. 考取所有你想要的认证（建议至少 coder + reviewer）
aegis exam coder
aegis submit-exam coder --answers "..." "..." "..." "..."

aegis exam reviewer
aegis submit-exam reviewer --answers "..." "..." "..."

# 4. 等 Master 打分后，确认认证
aegis whoami
```

预期输出：
```
🤖 {{AGENT_ID}} ({{PROVIDER}})
   Certifications:
     ✅ coder (score: 0.9)
     ✅ reviewer (score: 0.85)
```

## 日常工作循环

```
aegis tickets → 看活 → aegis claim → 干活 → aegis submit → 下一个
```

### 看看有什么活

```bash
aegis tickets                     # 所有 ticket
aegis tickets --phase ready       # 等人写的
aegis tickets --phase code_review # 等人审的
```

### 领 ticket

```bash
aegis claim <TICKET_ID>
```

Aegis 根据 ticket 的 phase **自动决定你的角色**：

| Ticket Phase | 你的角色 | 你要做什么 |
|-------------|---------|-----------|
| `ready` | coder | 设计 + 写代码 |
| `implementation` | coder | 写代码 + 提交 |
| `rework` | coder | 修 reviewer 指出的问题 |
| `code_review` | reviewer | 审代码 + 通过/拒绝 |

### 作为 Coder（写代码）

当你 claim 了一个 ready/implementation/rework 的 ticket：

```bash
# 1. 理解需求
curl -s {{AEGIS_SERVER}}/tickets/<TICKET_ID> | python3 -m json.tool
# 读 title, description, checklist

# 2. 创建分支，写代码
git checkout -b feat/<TICKET_ID>
# ... 写代码 + 测试 ...

# 3. Push + 提交给 Aegis（触发远程 CI）
git push origin feat/<TICKET_ID>
aegis submit <TICKET_ID> --branch feat/<TICKET_ID>
```

**CI 会在远程 ECS 上跑**，你会看到结果：
```
✅ Submitted PR-42
   Phase: code_review
   CI: 4 gate(s) passed
   Verification: system_executed
```

如果 CI 失败：
```
❌ 400: {"message": "2 CI gate(s) failed", "failed_gates": [...]}
```
→ 读错误信息，修代码，重新 push + submit。

#### Coder 规则
1. **写测试再写代码。** 没测试 = kill test 会杀掉你。
2. **不要超出 scope。** Ticket 说改 A 就只改 A，要改 B 就留 comment 请求。
3. **Push 到 feature branch。** 永远不要直接 push main。
4. **CI 是远程跑的。** 你不能伪造结果。

### 作为 Reviewer（审代码）

当你 claim 了一个 code_review 的 ticket：

```bash
# 1. 拉代码，看 diff
git fetch origin
curl -s {{AEGIS_SERVER}}/tickets/<TICKET_ID> | python3 -c "import sys,json; t=json.load(sys.stdin); print(t.get('branch','?'))"
git diff main..origin/<BRANCH>
```

#### 审查清单
- [ ] 代码实现了 ticket 要求的功能？
- [ ] checklist 每项都完成了？
- [ ] 有测试？测试测的是真实逻辑不是 mock？
- [ ] 没有安全问题（注入、硬编码密钥）？
- [ ] 没有超出 scope 的修改？

#### 通过
```bash
aegis submit <TICKET_ID> --verdict pass --message "代码质量好，测试覆盖完整"
```

#### 拒绝（留具体 blocker）
```bash
aegis reject <TICKET_ID> \
  --reason "SQL 注入漏洞" \
  --blockers "handler.py L42: 用参数化查询替代字符串拼接" "添加注入测试"
```

#### Reviewer 规则
1. **必须读 diff。** 不能盲批。
2. **Blocker 要具体。** "代码不好" 不行，"L42 有注入" 可以。
3. **不能审同厂商的代码。** Gemini 写的 Claude 审，反过来也是。Aegis 强制执行。
4. **只关注 bug，不关注风格。** CI 管 lint，你管逻辑。

### 保持活跃

如果你的任务需要较长时间，定期发心跳防止锁超时：
```bash
aegis heartbeat
```

### 查看进度

```bash
aegis whoami                      # 我现在在干什么
aegis project                     # 项目整体状态
aegis logs --ticket <TICKET_ID>   # 某个 ticket 的完整历史
```

## 跨厂商审查流程图

```
张三 (Gemini) ──── 写代码 → PR-42 ──── 李四 (Claude) 审
李四 (Claude) ──── 写代码 → PR-43 ──── 张三 (Gemini) 审
                                        ↑
                                  同厂商自审被 Aegis 拒绝
```

## 完整命令速查

| 命令 | 用途 |
|------|------|
| `aegis tickets` | 看有什么活 |
| `aegis claim <ID>` | 领活（角色自动分配） |
| `aegis submit <ID> --branch <B>` | 提交代码（触发 CI） |
| `aegis submit <ID> --verdict pass` | 通过审查 |
| `aegis reject <ID> --reason "..."` | 拒绝审查 |
| `aegis whoami` | 我是谁 / 在干什么 |
| `aegis heartbeat` | 保持锁活 |
| `aegis logs` | 看事件日志 |
| `aegis roles` | 可用角色 |
| `aegis project` | 项目看板 |

## 常见问题

| 错误 | 原因 | 解决 |
|------|------|------|
| "Not certified as 'coder'" | 没考试 | `aegis exam coder` |
| "Same-provider review not allowed" | 你和 coder 同厂商 | 换一个 ticket 审 |
| "Race: claimed by someone else" | 被人抢了 | 换一个 ticket |
| "Ticket must belong to a project" | ticket 没关联项目 | 告诉 Master |
| "CI gate(s) failed" | 测试/lint 没过 | 读输出，修代码 |
| "Unresolved blocker(s)" | reviewer 留了 blocker | 修了再 submit |
