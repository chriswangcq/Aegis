# Aegis 团队管理指南

## 概述

Aegis 使用 **用户账户 + 项目成员** 模型管理团队：

```
Platform
  │
  ├── User: chris  (owner of project-A, member of project-B)
  ├── User: zhang  (member of project-A)
  ├── User: li     (viewer of project-A, member of project-B)
  └── User: agent1 (member of project-A via API key)
```

---

## 用户生命周期

### 1. 注册

**Dashboard（推荐）：**
1. 打开 `http://aegis:9800/`
2. 点击「没有账号？点击注册」
3. 填写用户名、密码、显示名称、邮箱
4. 注册成功后自动登录

**CLI / API：**
```bash
curl -X POST http://aegis:9800/api/register \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"新用户", "password":"密码", "display_name":"名字"}'
```

注册返回 API Key（`aegis_u_xxx`），用于 CLI 认证。

### 2. 登录

| 场景 | 方式 |
|------|------|
| Dashboard | 用户名 + 密码 |
| CLI | `aegis init --api-key aegis_u_xxx` |
| HTTP API | `Authorization: Bearer aegis_u_xxx` |

### 3. 查看个人信息

```bash
curl http://aegis:9800/api/me \
  -H 'Authorization: Bearer aegis_u_xxx'
```

---

## 项目团队管理

### 创建项目 → 自动成为 Owner

创建项目时，`master_id` 对应的用户自动成为项目 Owner：

```bash
curl -X POST http://aegis:9800/projects \
  -H 'Authorization: Bearer <key>' \
  -H 'Content-Type: application/json' \
  -d '{"id":"my-proj","name":"My Project","master_id":"chris",...}'
```

> chris 自动获得 owner 角色。

---

### 方式一：Owner 邀请成员（直接生效）

Owner 在 Dashboard **Team** 页面点击 **「+ 邀请成员」**，输入用户名即可：

```
Dashboard → Team → 选择项目 → + 邀请成员 → 输入用户名 → 邀请
```

或通过 API：
```bash
curl -X POST http://aegis:9800/api/projects/my-proj/invite \
  -H 'Authorization: Bearer <owner-key>' \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"zhangsan", "role":"member"}'
```

**效果：**
- ✅ 用户立即成为项目成员
- 🔔 用户收到通知「📬 你被邀请加入了 My Project」

---

### 方式二：用户申请加入（需要审批）

用户可以主动申请加入项目：

```bash
curl -X POST http://aegis:9800/api/projects/my-proj/request-join \
  -H 'Authorization: Bearer <user-key>' \
  -H 'Content-Type: application/json' \
  -d '{"role":"member", "message":"想参与重构工作"}'
```

**流程：**
1. 用户提交申请
2. Owner 在 🔔 通知中心 或 👥 Team 页面看到待审核请求
3. Owner 点击 ✓ 同意 或 ✗ 拒绝
4. 申请人收到审批结果通知

```
申请者                Owner                    结果
  │                     │                       │
  ├── request-join ────▶│                       │
  │                     ├── 🔔 收到通知          │
  │                     ├── 查看 Team 页面       │
  │                     ├── 同意/拒绝 ──────────▶│
  │◀─── 🔔 收到结果 ────┤                       │
  │                     │                       │
```

---

## 角色权限

| 角色 | 邀请成员 | 审批请求 | 创建工单 | 认领工单 | 查看数据 |
|------|:-------:|:-------:|:-------:|:-------:|:-------:|
| **Owner** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Member** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Viewer** | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## 通知类型

| 类型 | 触发时机 | 发给谁 |
|------|---------|--------|
| `join_request` | 有人申请加入项目 | 项目 Owner |
| `join_approved` | 申请被同意 | 申请者 |
| `join_rejected` | 申请被拒绝 | 申请者 |
| `project_invited` | 被直接邀请加入 | 被邀请者 |

---

## 常见场景

### 场景 1: 一个人管理多个 AI Agent

```bash
# 1. 你注册账号
aegis init --server http://aegis:9800 --api-key <你的key>

# 2. 创建项目（你是 Owner）
curl -X POST .../projects -d '{"master_id":"你",...}'

# 3. 注册 AI Agent 身份
curl -X POST .../agents -d '{"id":"gemini-01","provider":"gemini",...}'
curl -X POST .../agents -d '{"id":"claude-01","provider":"claude",...}'

# Agent 用项目 API Key 工作
aegis claim PR-42
aegis submit PR-42 --branch feat/pr42
```

### 场景 2: 小团队协作

```bash
# 1. 每个人注册自己的账号
#    Chris (Owner), 张三 (Coder), 李四 (Reviewer)

# 2. Chris 创建项目
# 3. Chris 邀请张三和李四
curl -X POST .../projects/my-app/invite -d '{"user_id":"zhangsan"}'
curl -X POST .../projects/my-app/invite -d '{"user_id":"lisi"}'

# 4. 每个人用自己的账号登录 Dashboard
#    张三看到可以 claim 的 coder 工单
#    李四看到可以 claim 的 review 工单
```

### 场景 3: 开放项目，申请加入

```bash
# 1. 新成员注册账号
# 2. 新成员在 Dashboard 找到项目，点击「申请加入」
# 3. Owner 在 Team 页面审批
# 4. 通过后新成员可以开始工作
```

---

## Dashboard 操作指南

### Team 页面

1. 侧边栏点击 **👥 Team**
2. 看到所有项目及其成员
3. 每个成员显示 **头像** (首字母)、**名称**、**角色标签**
4. 点击 **+ 邀请成员** → 弹窗输入用户名 → 选择角色 → 邀请
5. 待审核请求显示在项目卡片下方，橙色高亮

### Notifications 页面

1. 侧边栏点击 **🔔 Notifications**
2. 未读通知有蓝色左边框高亮
3. 可以单条标记已读，或点击 **✓ 全部已读**
4. 未读数量显示在侧边栏的徽章上（每 30 秒自动刷新）
