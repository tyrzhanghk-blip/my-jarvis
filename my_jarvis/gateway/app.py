"""Gateway FastAPI 装配。"""
from __future__ import annotations
import os
import secrets
from pathlib import Path

# 中转网关不支持 OpenAI tracing，关闭以消除 401 噪音
os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from my_jarvis.gateway.paths import (CONFIG_DIR, DATA_DIR, SCRIPTS_DIR, TOKEN_PATH,
                                     ensure_dirs)
from my_jarvis.gateway.permission import PermissionEngine
from my_jarvis.gateway.protocol import Entrance, InboundMessage, OutboundMessage
from my_jarvis.gateway.router import MessageRouter
from my_jarvis.gateway.scheduler import JarvisScheduler
from my_jarvis.gateway.store import Store
from my_jarvis.tools.confirmbus import notify


def load_config() -> dict:
    ensure_dirs()
    cfg_path = CONFIG_DIR / "jarvis.yaml"
    if not cfg_path.exists():
        default = Path(__file__).parent.parent.parent / "config" / "jarvis.yaml"
        cfg_path.write_text(default.read_text())
    return yaml.safe_load(cfg_path.read_text()) or {}


CFG = load_config()
ensure_token = (lambda: (
    TOKEN_PATH.write_text(secrets.token_hex(24)) or
    os.chmod(TOKEN_PATH, 0o600)) if not TOKEN_PATH.exists() else None)()
TOKEN = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""


def auth(x_jarvis_token: str | None = Header(default=None)) -> None:
    if not x_jarvis_token or not secrets.compare_digest(x_jarvis_token, TOKEN):
        raise HTTPException(401, "invalid token")


app = FastAPI(title="my-jarvis gateway")
store = Store(DATA_DIR / "jarvis.db")
perm_path = CONFIG_DIR / "permissions.yaml"
if not perm_path.exists():
    perm_path.write_text((Path(__file__).parent.parent.parent / "config" / "permissions.yaml").read_text())
engine = PermissionEngine(perm_path)

# WS 客户端注册表（出站推送目标）
_ws_clients: list[WebSocket] = []


async def broadcast(out: OutboundMessage) -> None:
    import json
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(json.dumps(out.model_dump(), default=str, ensure_ascii=False))
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)
    # 企微出口
    if out.entrance == Entrance.wecom or out.type == "push" and out.entrance == Entrance.wecom:
        from my_jarvis.entrances.wecom import send_message
        try:
            await send_message(out.text)
        except Exception:
            pass


router = MessageRouter(store, engine, broadcast,
                       confirm_timeout_min=int(CFG.get("confirm", {}).get("default_timeout_min", 10)))
scheduler = JarvisScheduler(router)


@app.on_event("startup")
async def _startup() -> None:
    scheduler.start()
    # 默认心跳示例：每天 08:00 早报（用户可在 schedules 表调整）
    # scheduler.heartbeat("0 8 * * *", "给我一份今日简报：日期天气 + 下载目录现状", "web")


# ── 消息入口 ──
@app.websocket("/api/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            import json
            raw = await ws.receive_text()
            data = json.loads(raw)
            msg = InboundMessage(**data)
            asyncio_task = __import__("asyncio").create_task(router.handle(msg))
            asyncio_task.add_done_callback(lambda t: t.exception())
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


class ChatIn(BaseModel):
    text: str
    session_id: str | None = None
    entrance: str = "web"


@app.post("/api/chat")
async def chat(body: ChatIn, _: None = Depends(auth)) -> dict:
    msg = InboundMessage(entrance=Entrance(body.entrance), session_id=body.session_id, text=body.text)
    import asyncio
    await router.handle(msg)
    return {"ok": True}


# ── 确认 ──
class ConfirmIn(BaseModel):
    action_id: str
    decision: str  # approve | reject


@app.post("/api/confirm")
async def confirm(body: ConfirmIn, _: None = Depends(auth)) -> dict:
    row = store.decide(body.action_id, body.decision)
    if not row:
        raise HTTPException(404, "action not found")
    notify(body.action_id, body.decision == "approve")
    return {"ok": True, "status": row["status"]}


@app.get("/api/confirm")
async def confirm_list(_: None = Depends(auth)) -> list:
    return store.pending_list()


# ── 脚本管理 ──
@app.get("/api/scripts")
async def scripts_list(_: None = Depends(auth)) -> list:
    from my_jarvis.scripts import load_scripts
    return [{"id": s.id, "description": s.description, "status": s.manifest.get("status"),
             "params": s.params} for s in load_scripts()]


@app.post("/api/scripts/{script_id}/promote")
async def promote(script_id: str, _: None = Depends(auth)) -> dict:
    """候选模板转正：把 candidate 状态的 manifest 改为 active。"""
    for mf in SCRIPTS_DIR.glob("*/manifest.yaml"):
        import yaml as _y
        m = _y.safe_load(mf.read_text())
        if m.get("id") == script_id:
            m["status"] = "active"
            mf.write_text(_y.safe_dump(m, allow_unicode=True))
            router._script_tools = __import__("my_jarvis.scripts", fromlist=["build_script_tools"]).build_script_tools(
                __import__("my_jarvis.scripts", fromlist=["load_scripts"]).load_scripts())
            return {"ok": True}
    raise HTTPException(404, "script not found")


@app.get("/api/candidates")
async def candidates(status: str | None = None, _: None = Depends(auth)) -> list:
    return store.candidates(status)


@app.post("/api/candidates/{cid}/promote")
async def promote_candidate(cid: str, _: None = Depends(auth)) -> dict:
    row = store.promote_candidate(cid)
    if not row:
        raise HTTPException(404, "candidate not found")
    return {"ok": True, "candidate": row}


# ── 审计 ──
@app.get("/api/audit")
async def audit(page: int = 1, size: int = 50, _: None = Depends(auth)) -> dict:
    off = (page - 1) * size
    rows = store.conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (size, off)).fetchall()
    total = store.conn.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"]
    return {"total": total, "items": [dict(r) for r in rows]}


# ── 企微回调 ──
@app.get("/wecom/callback")
async def wecom_verify(msg_signature: str, timestamp: str, nonce: str, echostr: str):
    from my_jarvis.entrances.wecom import verify_url
    ok, plain = verify_url(msg_signature, timestamp, nonce, echostr)
    return HTMLResponse(plain if ok else "verify failed")


@app.post("/wecom/callback")
async def wecom_callback(request):
    from my_jarvis.entrances.wecom import handle_callback
    return await handle_callback(request, router)


@app.get("/api/status")
async def status() -> dict:
    return {"status": "online", "version": "0.1.0", "pending": len(store.pending_list())}


# ── Web 控制台（静态）──
from fastapi.staticfiles import StaticFiles  # noqa: E402

webui_dir = Path(__file__).parent.parent / "webui"
app.mount("/web", StaticFiles(directory=str(webui_dir), html=True), name="webui")


# ── 启动入口 ──
def main() -> None:  # python -m my_jarvis.gateway.app
    import uvicorn
    server = CFG.get("server", {})
    uvicorn.run(app, host=server.get("host", "127.0.0.1"), port=int(server.get("port", 8787)))


if __name__ == "__main__":
    main()
