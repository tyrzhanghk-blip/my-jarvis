"""脚本注册器：加载 scripts/ 下 manifest → 动态 function tool + 意图匹配。"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from agents import function_tool

from my_jarvis.tools.base import ExecSpec, run_spec


@dataclass
class ScriptDef:
    manifest: dict[str, Any]
    root: Path

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def description(self) -> str:
        return self.manifest.get("description", "")

    @property
    def params(self) -> dict:
        return self.manifest.get("params", {})

    def is_active(self) -> bool:
        return self.manifest.get("status") == "active"

    def fill_defaults(self, given: dict) -> dict:
        """参数合并：只读/dry_run 参数用默认；实际生效参数缺失交给调用方反问。"""
        merged = {}
        for name, spec in self.params.items():
            if name in given:
                merged[name] = given[name]
            elif "default" in spec:
                merged[name] = spec["default"]
        return merged

def load_scripts() -> list[ScriptDef]:
    """加载内置脚本（随包分发）+ 用户脚本（~/.my-jarvis/scripts）。"""
    import my_jarvis.gateway.paths as paths
    builtin = Path(__file__).parent.parent.parent / "scripts"
    out, seen = [], set()
    for base in (builtin, paths.SCRIPTS_DIR):
        if not base.exists():
            continue
        for mf in sorted(base.glob("*/manifest.yaml")):
            try:
                m = yaml.safe_load(mf.read_text())
                if m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                out.append(ScriptDef(m, mf.parent))
            except Exception:
                continue
    return out


def match_script(text: str, scripts: list[ScriptDef]) -> ScriptDef | None:
    """脚本命中优先：description + trigger_examples 关键词粗匹配（语义匹配后续升级）。"""
    best, best_score = None, 0
    for s in scripts:
        if not s.is_active():
            continue
        words = {w.strip("，。！？.,!?") for w in s.manifest.get("trigger_examples", [])}
        words.add(s.description)
        score = sum(1 for w in words if w and w in text)
        if score > best_score:
            best, best_score = s, score
    return best


def promote_candidate(store, cid: str) -> ScriptDef:
    """候选模板转正：DB 记录 → 用户脚本目录（manifest + run.sh）→ active。

    飞轮最后一环：agent 沉淀候选 → 人工审核调用本函数 → 成为可执行固化脚本。
    """
    row = store.promote_candidate(cid)
    if row is None:
        raise KeyError(f"candidate {cid} not found")

    sid = _slugify(row["description"] or f"candidate-{cid[:8]}")
    import my_jarvis.gateway.paths as paths
    target = paths.SCRIPTS_DIR / sid
    target.mkdir(parents=True, exist_ok=True)

    manifest = {
        "id": sid, "version": 1,
        "description": row["description"],
        "trigger_examples": [row["description"]],
        "params": {},
        "permissions": {"category": "write", "paths": [], "commands": []},
        "timeout": 300,
        "entry": "run.sh",
        "status": "active",
        "created_from": f"candidate:{cid}",
    }
    (target / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
    (target / "run.sh").write_text(
        "#!/bin/bash\n# 固化自候选模板 " + cid + "\nset -euo pipefail\n"
        + row["script_text"].strip() + "\n")
    (target / "run.sh").chmod(0o755)

    for s in load_scripts():
        if s.id == sid:
            return s
    raise RuntimeError(f"promoted script {sid} not found after write")


def _slugify(text: str) -> str:
    """描述 → 脚本 id（中文转拼音不可靠，取可读 ASCII + 短哈希保底唯一）。"""
    import hashlib
    import re
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:32]
    digest = hashlib.md5(text.encode()).hexdigest()[:6]
    return f"{ascii_part or 'script'}-{digest}"


def build_script_tools(scripts: list[ScriptDef]) -> list:
    """把 active 脚本包装成 function tools（经 run_script 权限路径）。"""
    by_id = {s.id: s for s in scripts if s.is_active()}

    @function_tool
    async def run_script(script_id: str, params_json: str = "{}") -> str:
        """执行一个已注册的固化脚本。

        script_id: 脚本 id
        params_json: 参数 JSON 字符串
        """
        from my_jarvis.tools.context import CURRENT_CTX, guard
        s = by_id.get(script_id)
        if not s:
            return f"脚本 {script_id} 不存在或未转正"
        params = s.fill_defaults(json.loads(params_json or "{}"))
        await guard({"tool": "run_script", "kind": "script", "script_id": script_id,
                     "command": f"run_script {script_id}"},
                    f"执行固化脚本 {script_id}({s.description})")
        ctx = CURRENT_CTX.get()
        ctx and ctx.store.audit("system", ctx.session_id, "run_script",
                                {"script_id": script_id, "params": params}, "allow", "script")
        import asyncio
        entry = s.manifest.get("entry", "run.py")
        script = s.root / entry
        timeout = int(s.manifest.get("timeout", 300))
        runner = f'python3 "{script}"' if script.suffix == ".py" else f'bash "{script}"'
        env_prefix = f"JARVIS_PARAMS='{json.dumps(params, ensure_ascii=False)}' "
        result = await run_spec(ExecSpec(kind="shell", script_text=env_prefix + runner, timeout=timeout))
        return result.brief()

    return [run_script]
