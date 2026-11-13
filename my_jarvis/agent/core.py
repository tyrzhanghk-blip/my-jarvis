"""主 agent 与子 agent 装配（OpenAI Agents SDK）。"""
from __future__ import annotations
from functools import lru_cache

from agents import Agent, ModelSettings, Runner

from my_jarvis.tools.browser import browser_explore, solidify_browser
from my_jarvis.tools.memory import memory_prompt, save_memory
from my_jarvis.tools.shell import make_candidate, run_command
from my_jarvis.tools.ui import run_applescript, ui_inspect

IDENTITY = """你是 my jarvis，用户 Mac/Windows 电脑上的私人 AI 助手。你真正能替用户执行操作，而不只是回答。

工作准则：
1. 收到任务先判断是否命中已注册固化脚本（下方脚本清单）；命中且参数完整则直接调用 run_script。
2. 未命中则 handoff 给对应底座子 agent：命令/文件 → shell-agent；网页 → browser-agent；桌面软件 → ui-agent。
3. 多步任务自行编排，逐步汇报进度；完成后汇总结果。
4. 一次成功的、可能重复的操作，主动询问用户是否沉淀为候选脚本（调用 make_candidate / solidify_browser）。
5. 任务成功后，若发现值得跨会话记住的用户偏好，调用 save_memory。
6. 任何被权限系统拒绝的操作，如实告知原因，不要尝试绕过。"""

SHELL_PROMPT = """你是 shell 底座子 agent，负责命令执行与文件管理。
- 必须先向用户说明将执行的命令，再调用 run_command。
- 不猜测路径：涉及文件位置先 ls / Get-ChildItem 确认。
- 只在授权路径内做写操作；删除类操作会被要求确认，属于预期行为。
- 成功的常用操作，提示用户可调用 make_candidate 固化。"""

BROWSER_PROMPT = """你是浏览器底座子 agent，负责网页操作。
- 未固化的页面用 browser_explore 逐步探索（一次一步：导航/点击/填表/截图）。
- 探索成功的操作序列，调用 solidify_browser 固化为候选脚本。"""

UI_PROMPT = """你是桌面 UI 底座子 agent，负责控制本地软件（macOS: AppleScript 优先，缺口用 Accessibility；Windows: UIA）。
- 按平台选择工具：macOS 优先 run_applescript。
- 操作前建议截图留证；修改性操作会被要求确认，属于预期行为。"""


def _resolve_models() -> dict[str, str]:
    """从 jarvis.yaml 解析模型别名 → 真实模型名（分层多模型路由）。"""
    import yaml
    from my_jarvis.gateway.paths import CONFIG_DIR
    cfg_path = CONFIG_DIR / "jarvis.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        out = {}
        for alias, m in (cfg.get("models") or {}).items():
            out[alias] = m.get("model", "gpt-4.1")
        return out
    except Exception:
        return {}


@lru_cache
def build_agents() -> tuple[Agent, dict[str, Agent]]:
    models = _resolve_models()
    strong = models.get("strong", "gpt-4.1")

    shell_agent = Agent(
        name="shell-agent", instructions=SHELL_PROMPT,
        tools=[run_command, make_candidate], model=strong)
    browser_agent = Agent(
        name="browser-agent", instructions=BROWSER_PROMPT,
        tools=[browser_explore, solidify_browser], model=strong)
    ui_agent = Agent(
        name="ui-agent", instructions=UI_PROMPT,
        tools=[run_applescript, ui_inspect], model=strong)

    main = Agent(
        name="jarvis-core",
        instructions=IDENTITY + "\n" + memory_prompt(),
        tools=[save_memory],
        handoffs=[shell_agent, browser_agent, ui_agent],
        model=strong)
    return main, {"shell": shell_agent, "browser": browser_agent, "ui": ui_agent}


async def run_agent(text: str, script_tools: list | None = None) -> str:
    """执行一轮主 agent（Gateway MessageRouter 调用）。

    script_tools: 注册器动态生成的固化脚本 function tools（命中优先）。
    """
    main, _ = build_agents()
    if script_tools:
        main = main.clone(tools=[*main.tools, *script_tools])
    result = await Runner.run(main, text)
    return result.final_output
