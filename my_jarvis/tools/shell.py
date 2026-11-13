"""shell 工具（shell-agent）：命令执行 + 文件操作。"""
from __future__ import annotations
from agents import function_tool

from my_jarvis.tools.base import ExecSpec, run_spec
from my_jarvis.tools.context import PermissionDenied, guard


@function_tool
async def run_command(command: str, timeout: int = 60) -> str:
    """执行一条 shell 命令并返回结果。用于命令执行与文件管理类任务。

    command: 要执行的 shell 命令（macOS 为 zsh，Windows 为 PowerShell）
    timeout: 超时秒数，默认 60
    """
    await guard({"tool": "run_command", "kind": "command", "command": command},
                f"将执行命令: {command}")
    result = await run_spec(ExecSpec(kind="shell", script_text=command, timeout=timeout))
    return result.brief()


@function_tool
async def make_candidate(description: str, command: str) -> str:
    """将一次成功的命令操作沉淀为候选脚本模板（无执行权限，待人工在 Web 管理台审核转正）。

    description: 脚本用途描述（给用户审核与后续意图匹配用）
    command: 成功执行过的命令
    """
    from my_jarvis.tools.context import CURRENT_CTX
    ctx = CURRENT_CTX.get()
    if ctx is None:
        return "no gateway context"
    ctx.store.add_candidate("run_command", command, description, ctx.session_id)
    return "已沉淀为候选模板，等待人工审核转正"
