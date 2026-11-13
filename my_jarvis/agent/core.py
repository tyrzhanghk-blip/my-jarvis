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


def _models_cfg() -> dict:
    """读取 jarvis.yaml 的 models 配置段。"""
    import yaml
    from my_jarvis.gateway.paths import CONFIG_DIR
    try:
        cfg = yaml.safe_load((CONFIG_DIR / "jarvis.yaml").read_text()) or {}
        return cfg.get("models") or {}
    except Exception:
        return {}


_CLIENTS: dict[str, "object"] = {}


def _client_for(base_url: str | None, api_key: str | None):
    """按 base_url 复用 AsyncOpenAI client（同网关共享连接池）。"""
    import openai
    key = base_url or "__official__"
    if key not in _CLIENTS:
        kwargs = {"api_key": api_key} if api_key else {}
        if base_url:
            kwargs["base_url"] = base_url
        _CLIENTS[key] = openai.AsyncOpenAI(**kwargs)
    return _CLIENTS[key]


def _model_for(alias: str):
    """别名 → Model 实例或模型名。

    优先级（base_url）：模型别名内 base_url > models.base_url > OPENAI_BASE_URL 环境变量。
    优先级（api_key）：api_key_env 指定的环境变量 > OPENAI_API_KEY。
    自定义 base_url 时返回 OpenAIChatCompletionsModel（显式绑定 client），
    否则返回模型名交由 SDK 用默认 client（官方端点/环境变量）。
    """
    import os

    cfg = _models_cfg()
    m = cfg.get(alias) or {}
    if isinstance(m, str):
        m = {"model": m}
    name = m.get("model") or alias
    base_url = m.get("base_url") or cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or None
    api_key_env = m.get("api_key_env") or cfg.get("api_key_env") or "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")

    if not base_url:
        return name  # SDK 默认 client（官方端点）
    from agents import OpenAIChatCompletionsModel
    return OpenAIChatCompletionsModel(model=name, openai_client=_client_for(base_url, api_key))


@lru_cache
def build_agents() -> tuple[Agent, dict[str, Agent]]:
    strong = _model_for("strong")

    # 注入已注册 active 脚本清单（脚本命中优先的前提）
    from my_jarvis.scripts import load_scripts
    active = [s for s in load_scripts() if s.is_active()]
    if active:
        listing = "\n".join(
            f"- {s.id}: {s.description}（参数: {', '.join(s.params) or '无'}）" for s in active)
        script_section = f"\n[已注册固化脚本清单——命中即优先调用 run_script]\n{listing}"
    else:
        script_section = "\n[当前无已注册固化脚本]"

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
        instructions=IDENTITY + script_section + "\n" + memory_prompt(),
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
    # handoff + 多步工具调用会消耗轮次，默认 10 轮不够
    result = await Runner.run(main, text, max_turns=25)
    return result.final_output
