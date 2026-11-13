# my-jarvis Gateway 架构设计

> 状态：设计中 · 基于产品方案 grilling 会话已确认决策（2026-11-13）
> 技术基线：Python + OpenAI Agents SDK，macOS 本地常驻

## 1. 定位与职责

Gateway 是 my-jarvis 守护进程的中央协调层，参考 OpenClaw 的 Gateway+Agent 架构，用 Python 重新实现。它是**唯一**的消息入口和权限执行点：

| 职责 | 说明 |
|------|------|
| 消息路由 | 将各入口消息统一协议后路由给 Agent 层 |
| 认证授权 | 入口信任分级 + 身份校验（企微回调验签） |
| 会话管理 | 会话创建/休眠/压缩/清理，SQLite 持久化 |
| 确认队列 | 待确认任务异步挂起、超时拒绝、批量批准 |
| 权限执行 | 解析 `permissions.yaml`，对每个工具调用做放行判定 |
| 调度 | 定时任务（cron）与心跳（heartbeat）主动任务 |
| 日志审计 | 全量操作日志，供追溯与"候选模板"沉淀 |

**非职责**：意图理解、工具实现（属于 Agent 层与 Tool 层，另文档）。

## 2. 进程模型

```
macOS launchd 常驻（用户级 LaunchAgent，开机自启）
┌────────────────────────────────────────────────┐
│ my-jarvis daemon (单进程, uvicorn)             │
│                                                │
│  FastAPI app                                   │
│  ├─ /api/ws        WebSocket（CLI/Web 长连接） │
│  ├─ /api/chat      HTTP（无状态调用）          │
│  ├─ /api/confirm   确认回调（批准/拒绝/列表）  │
│  ├─ /wecom/callback 企微自建应用回调端点       │
│  └─ /web/          内置 Web UI（静态页）       │
│                                                │
│  内部组件                                       │
│  ├─ MessageRouter   统一消息 → Agent Runner    │
│  ├─ SessionStore    SQLite (sessions)          │
│  ├─ ConfirmQueue    SQLite (pending_actions)   │
│  ├─ PermissionEngine permissions.yaml 解析求值 │
│  ├─ Scheduler       APScheduler（cron/心跳）   │
│  └─ AuditLog        SQLite (audit_log)         │
└────────────────────────────────────────────────┘
        ↑ 本地直连                    ↑ frp / Cloudflare Tunnel
   CLI / Web UI               企微回调（公网隧道转发）
```

- **监听**：默认 `127.0.0.1:8787`，不直接暴露公网；企微流量经隧道进入，回调端点验签后才受理。
- **单进程 + asyncio**：单用户场景吞吐无压力；工具执行用 `asyncio.create_subprocess_exec`，浏览器/UI 自动化各自以独立 worker 进程隔离（崩溃不拖垮 Gateway）。

## 3. 统一消息协议

所有入口收敛到同一 envelope，入口差异在适配层终结：

```json
// 入站 InboundMessage
{
  "msg_id": "uuid",
  "entrance": "cli | web | wecom",
  "session_id": "string | null",      // null 则按 入口+用户 路由到默认会话
  "text": "用户消息原文",
  "attachments": [],                   // 文件/图片的本地路径或 URL
  "reply_to": "msg_id | null"          // 确认类消息的回指
}

// 出站 OutboundMessage
{
  "msg_id": "uuid",
  "session_id": "string",
  "type": "reply | confirm_request | task_progress | push",
  "text": "回复内容（markdown）",
  "confirm": {                         // type=confirm_request 时
    "action_id": "uuid",
    "summary": "将执行: rm -rf ~/Downloads/*.dmg",
    "risk_level": "destructive",
    "expires_at": "ISO8601"            // 默认 10 分钟
  }
}
```

适配器职责：

| 入口 | 适配内容 |
|------|---------|
| CLI | stdin/stdout ↔ WebSocket；确认用交互式 y/n；`--yes` 仅对 CLI 入口生效 |
| Web | 内置静态页 ↔ WebSocket；确认用按钮 |
| 企微 | 回调验签解密（企微加解密协议）→ InboundMessage；出站调企微应用消息 API；确认用卡片按钮 |

## 4. 关键流程

### 4.1 普通消息

```
入口 → 适配器 → InboundMessage → MessageRouter
  → SessionStore 取会话（SQLiteSession 交给 Agents SDK）
  → Agent Runner（主 agent，handoff 到领域子 agent）
      每个工具调用 → PermissionEngine 判定
        ├─ allow    → 执行
        ├─ deny     → 拒绝并回告原因
        └─ confirm  → 挂起：写 ConfirmQueue，发 confirm_request
  → 最终回复 → 出站适配 → 入口
```

### 4.2 确认流程（异步挂起，已确认方案）

1. Agent 调用被判定 `confirm` → 工具调用挂起（不阻塞该会话其他消息，更不阻塞其他会话），`pending_actions` 落库
2. Gateway 向**来源入口**（也可配置向全部入口广播）发 `confirm_request`
3. 用户在任一入口批准/拒绝 → `/api/confirm` → ConfirmQueue 状态机流转 → 挂起的工具调用恢复执行或取消
4. 超时（默认 10 分钟，可按 risk_level 配置）自动拒绝
5. `destructive` 类（`always: true`）在企微入口额外要求二次校验（回复验证码），防伪造确认

### 4.3 定时与主动推送

- **定时任务（cron）**：Scheduler 按 cron 触发，以"系统会话"身份调用注册脚本或 agent 任务
- **心跳（heartbeat）**：借鉴 OpenClaw，HEARTBEAT 配置文件定义周期性主动行为（如每早 8 点汇总日程+新闻推送），由 Scheduler 触发、Agent 生成内容、经出口适配器推送到指定入口
- 推送复用 OutboundMessage 协议，`type=push`

## 5. 权限引擎

输入：工具调用的结构化描述（命令字符串/路径/脚本 id/网络目标）+ 入口 + 时间。
求值顺序：`rules`（先命中先赢）→ `categories` → `default_policy`；
`always: true` 的类别在任何规则命中后仍强制 `confirm`。
配置结构见 `permissions.yaml` 设计稿（已评审）。

## 6. 数据存储

单 SQLite 文件 `~/.my-jarvis/jarvis.db`：

| 表 | 用途 |
|----|------|
| sessions | 会话元数据（Agents SDK SQLiteSession 自管历史） |
| pending_actions | 确认队列（action_id, tool_call 快照, risk, status, expires_at） |
| audit_log | 全量审计（时间、入口、会话、工具、参数、判定结果、耗时、退出码） |
| candidate_templates | 成功任务自动沉淀的候选模板（无执行权限，待人工转正） |

## 7. 模块划分（代码结构）

```
my_jarvis/
  gateway/
    app.py            # FastAPI 装配
    protocol.py       # Inbound/OutboundMessage 模型
    router.py         # MessageRouter
    session.py        # SessionStore
    confirm.py        # ConfirmQueue + 状态机
    permission.py     # PermissionEngine
    scheduler.py      # APScheduler 封装 + heartbeat
    audit.py
    entrances/
      cli.py          # 终端客户端（连 /api/ws）
      web.py          # 静态 UI 挂载
      wecom.py        # 企微自建应用适配（验签/解密/发消息）
  agent/              # 另文档：主 agent + 领域子 agent
  tools/              # 另文档：命令/浏览器/UI/文件 工具集
  scripts/            # 固化脚本（manifest.yaml + run.py）
  config/
    permissions.yaml
    jarvis.yaml       # 模型、端口、隧道、企微凭据引用等
```

企微凭据（corpid/secret/token/aes_key）不落明文，从系统钥匙串（Keychain）读取。

## 8. 已确认决策清单

| # | 决策 | 结论 |
|---|------|------|
| 1 | 运行形态 | macOS 本地常驻守护进程（launchd） |
| 4 | 确认交互 | 异步挂起队列，不阻塞其他消息 |
| 8 | 入口架构 | 统一消息总线（Gateway 模式），企微走自建应用 + 回调隧道 |
| — | 权限模型 | 分级放行（见 permissions.yaml 设计） |
| 9 | 浏览器自动化 | agent-browser 探索未知页面 + playwright-cli 固化 shell 脚本重放，沉淀入脚本体系 |
| 10 | 软件 UI 自动化 | AppleScript 优先 / Accessibility 补位 / 坐标视觉兜底；统一为脚本生成+子进程执行底座 |
| 11 | 模型策略 | 分层多模型：主/子 agent 用强模型，参数填充等机械任务用轻量模型 |
| 12 | Web UI | 对话 + 管理台（脚本审核转正/确认队列/审计日志），权限规则仍手编 yaml |
| 14 | 子 Agent 分类 | 按执行底座三分：shell（命令+文件）/ browser / ui；放弃按能力域四分（cmd/file 撞车） |
| 15 | 平台支持 | macOS / Windows 双平台同等优先设计，macOS 先行实现；平台差异收敛到 PlatformAdapter（UI 自动化/shell 方言/常驻服务/凭据） |
| 13 | 脚本 manifest | schema 定稿（id/description/trigger_examples/params/permissions/timeout/status/created_from）；参数缺失按风险分级：只读或 dry_run 用默认值，实际生效参数缺失则反问，首次执行默认 dry_run |

## 9. 开放问题

（全部已决，见上方决策清单。后续文档拆分见 README。）
