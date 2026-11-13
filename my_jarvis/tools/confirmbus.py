"""确认事件总线：/api/confirm 批准后唤醒挂起的工具调用。"""
from __future__ import annotations
import asyncio

_EVENTS: dict[str, asyncio.Event] = {}
_RESULTS: dict[str, bool] = {}


def _get(action_id: str) -> asyncio.Event:
    if action_id not in _EVENTS:
        _EVENTS[action_id] = asyncio.Event()
    return _EVENTS[action_id]


def notify(action_id: str, approved: bool) -> None:
    _RESULTS[action_id] = approved
    _get(action_id).set()


async def wait_approved(action_id: str, timeout_min: int) -> bool:
    ev = _get(action_id)
    try:
        await asyncio.wait_for(ev.wait(), timeout=timeout_min * 60)
    except asyncio.TimeoutError:
        return False
    finally:
        _EVENTS.pop(action_id, None)
    return _RESULTS.pop(action_id, False)
