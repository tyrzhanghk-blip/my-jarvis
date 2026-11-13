"""工具执行上下文：权限判定 + 确认挂起（contextvar 注入，避免工具签名污染）。"""
from __future__ import annotations
import asyncio
import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from my_jarvis.gateway.permission import Decision, PermissionEngine, Verdict
from my_jarvis.gateway.protocol import Entrance, RiskLevel
from my_jarvis.gateway.store import Store


@dataclass
class ToolContext:
    entrance: Entrance
    session_id: str
    engine: PermissionEngine
    store: Store
    send: Callable[[Any], Awaitable[None]]      # 发 OutboundMessage
    confirm_timeout_min: int = 10
    msg_id: str = ""


CURRENT_CTX: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar("jarvis_ctx", default=None)


class PermissionDenied(Exception):
    pass


async def guard(tool_call: dict[str, Any], summary: str) -> None:
    """权限求值；confirm 则挂起等待用户批准（异步队列语义）。"""
    ctx = CURRENT_CTX.get()
    if ctx is None:  # 无 Gateway 上下文（如测试直跑）→ 收紧
        raise PermissionDenied("no gateway context")

    v: Verdict = ctx.engine.judge(tool_call, ctx.entrance)
    risk = ctx.engine._risk_of(tool_call)
    ctx.store.audit(ctx.entrance.value, ctx.session_id, tool_call.get("tool", "unknown"),
                    tool_call, v.decision.value, v.matched_rule, msg_id=ctx.msg_id)

    if v.decision == Decision.deny:
        raise PermissionDenied(f"denied by {v.matched_rule}")

    if v.decision == Decision.confirm:
        timeout = ctx.confirm_timeout_min
        action_id = ctx.store.add_pending(
            ctx.session_id, ctx.entrance,
            tool_call.get("tool", "unknown"), tool_call, risk, summary, timeout)
        from my_jarvis.gateway.protocol import ConfirmRequest, OutboundMessage, OutboundType
        await ctx.send(OutboundMessage(
            session_id=ctx.session_id, type=OutboundType.confirm_request,
            text=f"需要确认：{summary}",
            confirm=ConfirmRequest(
                action_id=action_id, summary=summary, risk_level=risk,
                expires_at=datetime.now() + timedelta(minutes=timeout)),
            entrance=ctx.entrance))
        # 等待批准（/api/confirm 会 set 对应 event）
        from .confirmbus import wait_approved
        approved = await wait_approved(action_id, timeout_min=timeout)
        if not approved:
            raise PermissionDenied("rejected or timeout")
