# Architecture

Aegis 采用严格的三层分离架构，确保每一行业务逻辑都可以零 mock 单测。

## 分层

```
┌────────────────────────────────────────────┐
│  main.py — 路由层 (Glue)                    │
│  职责: HTTP 解析 → 调纯函数 → 写 DB → 返回   │
│  测试: E2E                                  │
├────────────────────────────────────────────┤
│  logic.py — 纯逻辑层                        │
│  职责: input → output, 零 I/O               │
│  测试: 单测 (74 tests, zero mock, <0.3s)    │
├────────────────────────────────────────────┤
│  ci_runner.py — CI 执行层                    │
│  职责: subprocess 执行 pytest/lint/kill_test │
│  测试: 集成测试 (需要真实 repo)               │
├────────────────────────────────────────────┤
│  db.py — 数据层                              │
│  职责: SQLite schema, 常量, helper           │
│  测试: 通过 E2E 间接覆盖                     │
└────────────────────────────────────────────┘
```

## 为什么这样分？

**核心原则：纯函数可以被无限信任，I/O 必须被怀疑。**

| 层 | I/O | 可信度 | 测试方式 |
|----|-----|--------|---------|
| logic.py | ❌ 零 | 最高 — 数学级确定性 | 单测，零 mock |
| ci_runner.py | ✅ subprocess | 中 — 依赖外部环境 | 集成测试 |
| main.py | ✅ HTTP + DB | 低 — 依赖网络/数据库 | E2E |
| db.py | ✅ SQLite | 低 | E2E |

### logic.py 的规则

```python
# ✅ 允许
from __future__ import annotations
from dataclasses import dataclass

# ❌ 禁止（lint_purity 会自动检测）
import sqlite3      # I/O
import requests      # I/O
import subprocess    # I/O
from pathlib import Path  # I/O potential
```

Aegis 的 `lint_purity` gate 会 AST 扫描 `_logic.py`，发现 I/O import 就拒绝 submit。

## 数据模型

```
tickets ─── 工单（生命周期核心）
  │
  ├── evidence ─── 提交证据（system_executed / agent_reported）
  ├── comments ─── 讨论 + blocker
  └── event_log ── 完整审计链

agents ──── Agent 身份（provider, status, current_ticket）

roles ───── 角色定义（coder, reviewer）

post_mortems ── 失败模式分析

knowledge ──── 沉淀的知识

trust_events ── 信任变动日志
```

## CI Runner 执行流程

```
Agent submit (repo_path)
  │
  ▼
ci_runner.run_all_gates(repo_path, test_specs)
  │
  ├── 1. run_pytest(repo_path)
  │     subprocess.run(["python", "-m", "pytest", "tests/"])
  │     → fail: 400 "Tests failed"
  │
  ├── 2. run_lint(repo_path)
  │     AST 扫描 *_logic.py 的 import
  │     → fail: 400 "Logic file imports I/O"
  │
  ├── 3. run_kill_test(repo_path)
  │     对每个 *_logic.py 的公开函数:
  │       1. 删除函数体 (pass)
  │       2. 跑 pytest
  │       3. 如果测试还是绿的 → mutant survived → fail
  │     → fail: 400 "Mutant survived: function_name"
  │
  └── 4. run_spec_coverage(repo_path, test_specs)
        AST 扫描 test_*.py 函数名 + docstring
        对比 Master 定义的 test_specs 关键词
        → fail: 400 "Spec not covered: {spec}"
```

## 安全模型

```
旧模式 (agent_reported):
  Agent → "我跑了测试，通过了" → Aegis 信任 → 可伪造 ❌

新模式 (system_executed):
  Agent → "代码在这里" → Aegis 自己跑 → 不可伪造 ✅
```

所有 system_executed 的 evidence 写入数据库时 `agent_id = "system"`，与 agent 提交的 evidence 严格区分。
