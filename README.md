# my jarvis

运行于 macOS / Windows 的私人 AI 助手：自然语言驱动网页操作、软件控制、命令执行，常用操作固化为免确认脚本。

- 设计文档：`docs/architecture/gateway.md`（决策清单）· `docs/design/detailed-design.md`（详细设计 v0.2）
- 产品页：`docs/product/index.html`

## 快速开始

```bash
# 依赖（Python 3.12+）
uv venv --python /opt/homebrew/bin/python3.12 .venv
uv pip install -p .venv/bin/python -e ".[dev]"

# 启动 Gateway（首次启动自动初始化 ~/.my-jarvis/ 配置与 token）
.venv/bin/python -m my_jarvis.gateway.app

# CLI 对话（另开终端）
.venv/bin/jarvis            # 交互模式
.venv/bin/jarvis status     # 单发
.venv/bin/jarvis scripts    # 脚本列表
```

- Web 控制台：http://127.0.0.1:8787/web （token 在 `~/.my-jarvis/token`）
- API Key：`export OPENAI_API_KEY=...`（agent 对话必需；key 只从环境变量读取，不落配置文件）
- 自定义模型网关（中转/代理/私有部署）：在 `~/.my-jarvis/config/jarvis.yaml` 配置
  ```yaml
  models:
    base_url: "https://your-relay.example.com/v1"   # 留空则用官方端点或 OPENAI_BASE_URL 环境变量
    api_key_env: OPENAI_API_KEY                     # 从哪个环境变量读 key
    strong: { model: "hy4" }                        # 跑 agent 的强模型
    light:  { model: "hy3" }                        # 参数填充/摘要的轻量模型
    # 每个别名可单独覆盖 base_url，实现多网关路由
  ```

## 企微接入（可选）

凭据写入 keyring，回调经 frp/cloudflared 隧道转发到 `/wecom/callback`：

```bash
python -c "import keyring; keyring.set_password('my-jarvis','my-jarvis/wecom/corp_id','<corpid>')"
# 同理写入 secret / callback_token / aes_key
```

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
```

## 目录

- `my_jarvis/gateway/` — Gateway（消息路由/权限引擎/确认队列/调度器/审计）
- `my_jarvis/agent/` — 主 agent + shell/browser/ui 三底座子 agent
- `my_jarvis/tools/` — 统一执行底座（ExecSpec）+ 权限 guard
- `my_jarvis/scripts/` — 脚本注册器（manifest → function tool）
- `my_jarvis/entrances/` — CLI / Web / 企微适配
- `my_jarvis/platform/` — PlatformAdapter（macOS/Windows 差异）
- `scripts/` — 内置固化脚本（cleanup_downloads）
