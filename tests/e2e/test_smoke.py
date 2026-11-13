"""E2E 冒烟回归：三场景（只读命令 / 固化脚本命中 / 确认流闭环）。

默认跳过；需要真实 LLM 网关时显式开启：
    JARVIS_E2E=1 .venv/bin/python -m pytest tests/e2e/ -v
依赖：OPENAI_API_KEY（及可选 OPENAI_BASE_URL）已在环境中。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_E2E") != "1" or not os.environ.get("OPENAI_API_KEY"),
    reason="E2E 需 JARVIS_E2E=1 且 OPENAI_API_KEY 已配置")

ROOT = Path(__file__).parent.parent.parent
PORT = 8790
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    """独立 JARVIS_HOME 启动真实 Gateway 子进程。"""
    env = os.environ.copy()
    home = tmp_path_factory.mktemp("jarvis-e2e")
    env["JARVIS_HOME"] = str(home)
    env["JARVIS_PORT"] = str(PORT)
    # 预写 base_url 到新配置（走"配置文件"而非环境变量路径，验证自定义网关支持）
    base_url = env.get("OPENAI_BASE_URL", "")
    if base_url:
        cfg_dir = home / "config"
        cfg_dir.mkdir(parents=True)
        template = (ROOT / "config" / "jarvis.yaml").read_text()
        (cfg_dir / "jarvis.yaml").write_text(
            template.replace('base_url: ""', f'base_url: "{base_url}"', 1))
        env.pop("OPENAI_BASE_URL", None)  # 强制走配置路径
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "my_jarvis.gateway.app"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(30):
        try:
            httpx.get(f"{BASE}/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("gateway failed to start")
    token = (Path(env["JARVIS_HOME"]) / "token").read_text().strip()
    os.environ["GW_HOME"] = env["JARVIS_HOME"]  # 供测试进程内自动拒绝用
    yield {"token": token, "home": env["JARVIS_HOME"]}
    os.environ.pop("GW_HOME", None)
    proc.kill()


async def _ws_roundtrip(text: str, auto_approve_token: str | None = None,
                        timeout_s: int = 300) -> dict:
    """WS 发消息收完整对话。

    auto_approve_token 提供 → 自动批准确认（场景3）；
    否则自动拒绝确认 → agent 收到拒绝后继续给出回复，避免挂起超时。
    """
    import websockets
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws") as ws:
        await ws.send(json.dumps({"entrance": "cli", "text": text}))
        confirm_seen = None
        async with asyncio.timeout(timeout_s):
            async for msg in ws:
                o = json.loads(msg)
                if o.get("type") == "confirm_request":
                    c = o["confirm"]
                    confirm_seen = c
                    decision = "approve" if auto_approve_token else "reject"
                    token = auto_approve_token or _env_token()
                    if token:
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                f"{BASE}/api/confirm",
                                headers={"X-Jarvis-Token": token},
                                json={"action_id": c["action_id"], "decision": decision})
                elif o.get("type") == "reply":
                    return {"reply": o["text"], "confirm": confirm_seen}
    return {"reply": "", "confirm": confirm_seen}


def _env_token() -> str | None:
    """场景1/2 自动拒绝也需要 token：从 GW_HOME 环境变量（fixture 注入）取。"""
    home = os.environ.get("GW_HOME")
    if home:
        p = Path(home) / "token"
        if p.exists():
            return p.read_text().strip()
    return None


def _audit(gateway) -> list[dict]:
    db = sqlite3.connect(str(Path(gateway["home"]) / "jarvis.db"))
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id")]


class TestE2ESmoke:
    def test_01_readonly_command(self, gateway):
        out = asyncio.run(_ws_roundtrip("帮我看看 /tmp 目录下最新的3个文件是什么，简单列出即可"))
        assert out["reply"], "应收到回复"
        assert "任务执行失败" not in out["reply"]
        decisions = [a for a in _audit(gateway) if a["tool_name"] == "run_command"]
        assert any(a["decision"] == "allow" and a["matched_rule"] == "category:readonly"
                   for a in decisions), "readonly 命令应 allow"

    def test_02_script_hit_dryrun(self, gateway):
        out = asyncio.run(_ws_roundtrip(
            "帮我整理下载文件夹（若已注册的固化脚本能覆盖，请直接用它执行）"))
        assert out["reply"], "应收到回复"
        assert "任务执行失败" not in out["reply"]
        audits = _audit(gateway)
        assert any(a["tool_name"] == "run_script" and a["decision"] == "allow"
                   for a in audits), "应命中固化脚本并 allow"
        # dry_run 默认演练：回复应表达"未实际改动"的演练语义
        lowered = out["reply"].lower()
        assert any(k in lowered for k in ("dry", "演练", "尚未改动", "未实际", "预览")), \
            "首次执行应默认演练模式"

    def test_03_confirm_flow(self, gateway):
        out = asyncio.run(_ws_roundtrip(
            "在 /tmp 下创建一个叫 jarvis-e2e-regression 的空目录",
            auto_approve_token=gateway["token"]))
        assert out["confirm"] is not None, "写操作应触发确认请求"
        assert out["confirm"]["risk_level"] in ("write", "destructive")
        assert out["reply"] and "任务执行失败" not in out["reply"]
        audits = _audit(gateway)
        assert any(a["decision"] == "confirm" and a["matched_rule"] == "category:write"
                   for a in audits), "mkdir 应判定为 write→confirm"
