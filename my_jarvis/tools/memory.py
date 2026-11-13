"""长期记忆工具（主 agent）。"""
from __future__ import annotations
import json
from pathlib import Path
from agents import function_tool

from my_jarvis.gateway.paths import DATA_DIR

MEMORY_PATH = DATA_DIR / "memory.json"


def load_memory() -> dict:
    if MEMORY_PATH.exists():
        return json.loads(MEMORY_PATH.read_text())
    return {}


@function_tool
async def save_memory(key: str, value: str) -> str:
    """保存一条跨会话长期记忆（用户偏好、常用路径、账号别名等）。

    key: 记忆键名，如 "常用下载目录"
    value: 记忆内容
    """
    m = load_memory()
    m[key] = value
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2))
    return f"已记住: {key}"


def memory_prompt() -> str:
    m = load_memory()
    if not m:
        return ""
    lines = [f"- {k}: {v}" for k, v in m.items()]
    return "\n[用户长期记忆]\n" + "\n".join(lines)
