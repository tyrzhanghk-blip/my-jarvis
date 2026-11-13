"""桌面 UI 工具（ui-agent）：平台自适应（macOS: AppleScript→Accessibility；Windows: UIA）。

v0.1 实现 AppleScript 层；Accessibility/UIA 层为接口占位（worker 隔离进程后续接入）。
"""
from __future__ import annotations
from agents import function_tool

from my_jarvis.tools.base import ExecSpec, run_spec
from my_jarvis.tools.context import guard


@function_tool
async def run_applescript(script: str) -> str:
    """执行 AppleScript 脚本控制 macOS 应用（仅 macOS 平台）。

    script: AppleScript 脚本文本
    """
    await guard({"tool": "run_applescript", "kind": "applescript", "script_text": script},
                f"AppleScript: {script[:120]}")
    result = await run_spec(ExecSpec(kind="applescript", script_text=script, timeout=60))
    return result.brief()


@function_tool
async def ui_inspect() -> str:
    """查询前台应用 UI 元素树（Accessibility/UIA）。v0.1 占位。"""
    return "ui_inspect 将在 UI worker 进程接入后可用（Accessibility / Win UIA）。"
