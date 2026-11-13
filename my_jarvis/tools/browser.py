"""浏览器工具（browser-agent）：探索 + 固化。

依赖外部能力：agent-browser（探索）、playwright-cli（固化 shell 脚本）。
当前提供结构化封装；若外部 CLI 不可用则返回明确错误。
"""
from __future__ import annotations
import shutil
from agents import function_tool

from my_jarvis.tools.base import ExecSpec, run_spec
from my_jarvis.tools.context import CURRENT_CTX, guard


def _has(cli: str) -> bool:
    return shutil.which(cli) is not None


@function_tool
async def browser_explore(instruction: str) -> str:
    """用 agent-browser 探索操作网页（导航/点击/填表/截图）。适用于未固化的页面。

    instruction: 一步浏览器操作指令，如 "打开 https://example.com 并截图" 或 "点击登录按钮"
    """
    await guard({"tool": "browser_explore", "kind": "command",
                 "command": f"agent-browser {instruction}", "risk": "write"},
                f"浏览器操作: {instruction}")
    if not _has("agent-browser"):
        return "agent-browser CLI 未安装，请先安装（brew/npm）后再使用网页探索能力。"
    result = await run_spec(ExecSpec(kind="shell",
                                     script_text=f'agent-browser "{instruction}"', timeout=120))
    return result.brief()


@function_tool
async def solidify_browser(description: str, steps: str) -> str:
    """将探索成功的网页操作序列固化为 playwright-cli shell 脚本候选模板。

    description: 脚本用途描述
    steps: 依次成功的操作步骤描述（自然语言或命令序列，每行一步）
    """
    from my_jarvis.tools.context import CURRENT_CTX
    ctx = CURRENT_CTX.get()
    if ctx is None:
        return "no gateway context"
    script = f"# playwright-cli 固化脚本（候选）\n# steps:\n# " + "\n# ".join(steps.splitlines())
    ctx.store.add_candidate("browser", script, description, ctx.session_id)
    return "已生成 playwright 候选脚本模板，等待人工审核转正"
