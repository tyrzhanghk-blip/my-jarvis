"""PlatformAdapter：平台差异收敛点（UI 自动化 / shell 方言 / 常驻 / 凭据）。"""
from __future__ import annotations
import platform as _plat
import shutil
import sys


class PlatformAdapter:
    def __init__(self, name: str | None = None):
        self.name = name or ("darwin" if _plat.system() == "Darwin" else "windows")

    @property
    def is_mac(self) -> bool:
        return self.name == "darwin"

    @property
    def shell_args(self) -> list[str]:
        # ExecSpec(kind="shell") 的执行载体
        if self.is_mac:
            return ["/bin/zsh", "-c"]
        return ["powershell.exe", "-NoProfile", "-Command"]

    def applescript_args(self, script: str) -> list[str]:
        return ["osascript", "-e", script]

    def expand(self, p: str) -> str:
        from pathlib import Path
        return str(Path(p).expanduser())


ADAPTER = PlatformAdapter()
