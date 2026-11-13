"""统一执行底座：脚本生成 → 子进程执行 → 结构化结果。"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from my_jarvis.platform import ADAPTER


@dataclass
class ExecSpec:
    kind: str                 # shell | applescript | powershell | playwright
    script_text: str
    timeout: int = 60
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int

    def brief(self, limit: int = 2000) -> str:
        out = self.stdout[-limit:]
        err = self.stderr[-limit:]
        parts = [f"exit={self.exit_code} ({self.duration_ms}ms)"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)


async def run_spec(spec: ExecSpec) -> ExecResult:
    if spec.kind == "shell":
        cmd = ADAPTER.shell_args + [spec.script_text]
    elif spec.kind == "powershell":
        cmd = ["powershell.exe", "-NoProfile", "-Command", spec.script_text] if ADAPTER.is_mac else ["powershell", "-NoProfile", "-Command", spec.script_text]
    elif spec.kind == "applescript":
        cmd = ADAPTER.applescript_args(spec.script_text)
    elif spec.kind == "playwright":
        cmd = ["/bin/zsh", "-c", spec.script_text]  # playwright-cli 生成的 shell 脚本
    else:
        raise ValueError(f"unknown kind: {spec.kind}")

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=ADAPTER.expand(spec.cwd) if spec.cwd else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ExecResult(False, "", f"timeout after {spec.timeout}s", -1, int((time.monotonic()-t0)*1000))
        return ExecResult(
            proc.returncode == 0,
            out.decode(errors="replace"), err.decode(errors="replace"),
            proc.returncode, int((time.monotonic()-t0)*1000))
    except Exception as e:  # noqa: BLE001
        return ExecResult(False, "", str(e), -1, int((time.monotonic()-t0)*1000))
