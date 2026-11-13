"""权限引擎：rules > categories > default_policy；always:true 强制 confirm。

命令分类模式库按平台提供（PlatformAdapter 注入）。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time as dtime
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional
import re
import yaml

from .protocol import Entrance, RiskLevel


class Decision(str, Enum):
    allow = "allow"
    confirm = "confirm"
    deny = "deny"


@dataclass
class Verdict:
    decision: Decision
    matched_rule: str
    reason: str


# ── 平台命令分类模式库（PlatformAdapter 的内置实现）──
CMD_PATTERNS: dict[str, dict[str, list[str]]] = {
    "darwin": {
        "readonly": [r"^ls\b", r"^cat\b", r"^head\b", r"^tail\b", r"^grep\b", r"^find\b", r"^ps\b",
                     r"^pwd$", r"^which\b", r"^stat\b", r"^file\b", r"^du\b", r"^df\b",
                     r"^git (status|log|diff|show|branch)\b", r"^echo\b", r"^date\b"],
        "destructive": [r"\brm\b", r"^sudo\b", r"^kill\b", r"^pkill\b", r"^diskutil\b", r"^truncate\b",
                        r"^mkfs", r"^dd\b", r"^chmod 777", r"^mv\s+\S+\s+/dev/null"],
        "write": [r"^mv\b", r"^cp\b", r"^mkdir\b", r"^touch\b", r"^tee\b", r"^defaults write\b",
                  r"^open\b", r"^brew (install|uninstall)\b", r"^pip\w* install\b", r"^git (add|commit|push|checkout)\b"],
    },
    "windows": {
        "readonly": [r"^Get-ChildItem\b", r"^Get-Content\b", r"^Get-Process\b", r"^Get-Item\b",
                     r"^Select-String\b", r"^Get-Location$", r"^Get-Command\b",
                     r"^git (status|log|diff|show|branch)\b", r"^Write-Output\b"],
        "destructive": [r"^Remove-Item\b", r"^Stop-Process\b", r"^Format-Volume\b", r"^Remove-\w+", r"^Clear-\w+"],
        "write": [r"^Move-Item\b", r"^Copy-Item\b", r"^New-Item\b", r"^Set-Content\b",
                  r"^Start-Process\b", r"\bwinget (install|uninstall)\b"],
    },
}


def classify_command(cmd: str, platform: str = "darwin") -> RiskLevel:
    pats = CMD_PATTERNS.get(platform, CMD_PATTERNS["darwin"])
    for level in ("destructive", "write", "readonly"):
        for pat in pats[level]:
            if re.search(pat, cmd.strip()):
                return RiskLevel(level)
    return RiskLevel.write  # 未识别 → 按写操作处理（default_policy=confirm）


class PermissionEngine:
    """求值：rules(先命中先赢) → categories → default_policy；always 后置强制。"""

    def __init__(self, config_path: Path, platform: str = "darwin"):
        raw = yaml.safe_load(config_path.read_text()) or {}
        self.cfg = raw
        self.platform = platform
        self.categories: dict[str, dict] = raw.get("categories", {})
        self.rules: list[dict] = raw.get("rules", [])
        self.contexts: dict = raw.get("contexts", {})

    # ── 主入口 ──
    def judge(self, tool_call: dict[str, Any], entrance: Entrance, now: Optional[datetime] = None) -> Verdict:
        now = now or datetime.now()
        risk = self._risk_of(tool_call)

        for rule in self.rules:
            hit, why = self._match_rule(rule.get("match", {}), tool_call, risk)
            if hit:
                v = Decision(rule["policy"])
                if self._in_night_window(now):
                    v = Decision.confirm
                v = self._enforce_always(v, risk)
                return Verdict(v, f"rule:{why}", f"matched rule {why}")

        cat = self.categories.get(risk.value, {})
        v = Decision(cat.get("policy", self.cfg.get("default_policy", "confirm")))
        if self._in_night_window(now):
            v = Decision.confirm
        v = self._enforce_always(v, risk)
        return Verdict(v, f"category:{risk.value}", f"category {risk.value}")

    # ── 内部 ──
    def _risk_of(self, tc: dict[str, Any]) -> RiskLevel:
        if tc.get("kind") == "script":
            return RiskLevel.script
        if tc.get("risk"):
            return RiskLevel(tc["risk"])
        cmd = tc.get("command") or tc.get("script_text") or ""
        return classify_command(cmd, self.platform)

    def _match_rule(self, m: dict, tc: dict, risk: RiskLevel) -> tuple[bool, str]:
        t = m.get("type")
        if t == "command":
            pat = m.get("pattern", "")
            return bool(re.search(pat, tc.get("command", ""))), f"command~/{pat}/"
        if t == "path":
            p = tc.get("path", "")
            if not p:
                return False, ""
            exp = str(Path(m.get("pattern", "")).expanduser())
            return fnmatch(str(Path(p).expanduser()), exp), f"path:{m.get('pattern')}"
        if t == "script":
            return tc.get("script_id") == m.get("id"), f"script:{m.get('id')}"
        return False, ""

    def _enforce_always(self, v: Decision, risk: RiskLevel) -> Decision:
        if risk == RiskLevel.destructive:
            cat = self.categories.get("destructive", {})
            if cat.get("always"):
                return Decision.confirm
        return v

    def _in_night_window(self, now: datetime) -> bool:
        for sch in self.contexts.get("schedules", []):
            try:
                a, b = sch["range"].split("-")
                ha, ma = map(int, a.split(":")); hb, mb = map(int, b.split(":"))
                t = now.time()
                lo, hi = dtime(ha, ma), dtime(hb, mb)
                crossed = lo <= t <= hi if lo <= hi else (t >= lo or t <= hi)
                if crossed and sch.get("override", {}).get("default_policy") == "confirm":
                    return True
            except Exception:
                continue
        return False
