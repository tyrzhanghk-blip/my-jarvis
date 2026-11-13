"""MessageRouter：统一消息 → 会话 → agent 执行 → 出站。"""
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable

from my_jarvis.agent.core import run_agent
from my_jarvis.gateway.permission import PermissionEngine
from my_jarvis.gateway.protocol import (Entrance, InboundMessage, OutboundMessage, OutboundType)
from my_jarvis.gateway.store import Store
from my_jarvis.scripts import build_script_tools, load_scripts
from my_jarvis.tools.context import CURRENT_CTX, ToolContext

Sender = Callable[[OutboundMessage], Awaitable[None]]


class MessageRouter:
    def __init__(self, store: Store, engine: PermissionEngine, send: Sender,
                 confirm_timeout_min: int = 10):
        self.store = store
        self.engine = engine
        self.send = send
        self.confirm_timeout_min = confirm_timeout_min
        self._script_tools = build_script_tools(load_scripts())

    def reload_scripts(self) -> None:
        """转正/禁用脚本后热加载：重建 function tools 与主 agent 提示词。"""
        from my_jarvis.agent.core import build_agents
        build_agents.cache_clear()
        self._script_tools = build_script_tools(load_scripts())

    async def handle(self, msg: InboundMessage) -> None:
        session_id = self.store.get_or_create_session(msg.session_id, msg.entrance)
        ctx = ToolContext(
            entrance=msg.entrance, session_id=session_id, engine=self.engine,
            store=self.store, send=self.send,
            confirm_timeout_min=self.confirm_timeout_min, msg_id=msg.msg_id)
        token = CURRENT_CTX.set(ctx)
        try:
            reply = await run_agent(msg.text, script_tools=self._script_tools)
            await self.send(OutboundMessage(session_id=session_id, text=reply,
                                            entrance=msg.entrance))
        except Exception as e:  # noqa: BLE001
            await self.send(OutboundMessage(
                session_id=session_id,
                text=f"任务执行失败：{e}。已中止，未完成的确认可在管理台处理。",
                entrance=msg.entrance))
        finally:
            CURRENT_CTX.reset(token)
