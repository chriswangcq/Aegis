# novaic-command-center

AI-Native 研发指挥中心 — 独立 HTTP 服务，基于 SQLite。

Worker Agent 通过 HTTP API 浏览、认领、提交工单，如同零工接单。
Master Agent 通过 HTTP API 创建工单、审批、追踪信任分。
Human 通过 CLI 客户端或未来的 Dashboard 操控全局。

## Quick Start

```bash
pip install -r requirements.txt
python -m server.main          # 默认 http://127.0.0.1:9800
```

## API Overview

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/tickets` | any | 浏览工单（支持 ?phase=ready&available=true） |
| GET | `/tickets/{id}` | any | 查看工单详情（Worker 先读再决定是否接） |
| POST | `/tickets` | master | 创建工单 |
| POST | `/tickets/{id}/claim` | worker | 原子认领 |
| POST | `/tickets/{id}/submit` | worker | 提交 + Gate Check |
| POST | `/tickets/{id}/release` | worker | 放弃认领 |
| POST | `/tickets/{id}/advance` | master | 手动推进阶段（merge/canary/done） |
| GET | `/agents` | any | 查看所有 Agent |
| POST | `/agents` | any | 注册 Agent |
| GET | `/status` | any | 全局看板 |
| GET | `/failure-patterns` | any | 查看失败模式注册表 |
