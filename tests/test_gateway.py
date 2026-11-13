"""单元测试：权限引擎 / 确认队列 / 脚本注册器 / 执行底座。"""
from __future__ import annotations
from pathlib import Path

import pytest

from my_jarvis.gateway.permission import Decision, PermissionEngine, classify_command
from my_jarvis.gateway.protocol import Entrance, RiskLevel
from my_jarvis.scripts import load_scripts, match_script

PERM_YAML = Path(__file__).parent.parent / "config" / "permissions.yaml"


class TestPermissionEngine:
    def setup_method(self):
        self.eng = PermissionEngine(PERM_YAML, platform="darwin")

    def test_readonly_allow(self):
        v = self.eng.judge({"tool": "run_command", "command": "ls -la ~/Downloads"}, Entrance.cli)
        assert v.decision == Decision.allow

    def test_destructive_confirm(self):
        v = self.eng.judge({"tool": "run_command", "command": "rm -rf /tmp/x"}, Entrance.cli)
        assert v.decision == Decision.confirm

    def test_rule_overrides(self):
        # git status 命中规则 → allow
        v = self.eng.judge({"tool": "run_command", "command": "git status"}, Entrance.cli)
        assert v.decision == Decision.allow

    def test_destructive_never_downgrade(self):
        # rm 在任何情况下不能被规则降级（always:true）
        v = self.eng.judge({"tool": "run_command", "command": "rm ~/x.txt"}, Entrance.wecom)
        assert v.decision == Decision.confirm

    def test_unknown_command_confirm(self):
        v = self.eng.judge({"tool": "run_command", "command": "some-unknown-cli --flag"}, Entrance.cli)
        assert v.decision == Decision.confirm

    def test_script_allow(self):
        v = self.eng.judge({"tool": "run_script", "kind": "script", "script_id": "cleanup_downloads"}, Entrance.cli)
        assert v.decision == Decision.allow

    def test_windows_dialect(self):
        eng = PermissionEngine(PERM_YAML, platform="windows")
        assert classify_command("Remove-Item x", "windows") == RiskLevel.destructive
        assert classify_command("Get-ChildItem", "windows") == RiskLevel.readonly

    def test_sudo(self):
        v = self.eng.judge({"tool": "run_command", "command": "sudo reboot"}, Entrance.cli)
        assert v.decision == Decision.confirm


class TestStore:
    def test_pending_flow(self, tmp_path):
        from my_jarvis.gateway.store import Store
        s = Store(tmp_path / "t.db")
        aid = s.add_pending("sess", Entrance.cli, "run_command", {"command": "rm x"},
                            RiskLevel.destructive, "rm x", 10)
        assert len(s.pending_list()) == 1
        row = s.decide(aid, "approve")
        assert row["status"] == "executing"
        s.finish(aid, True)
        assert len(s.pending_list()) == 0

    def test_expire(self, tmp_path):
        from datetime import datetime
        from my_jarvis.gateway.store import Store
        s = Store(tmp_path / "t.db")
        aid = s.add_pending("sess", Entrance.cli, "t", {}, RiskLevel.write, "s", 10)
        # 手动把过期时间改到过去
        s.conn.execute("UPDATE pending_actions SET expires_at=? WHERE action_id=?",
                       ("2000-01-01", aid))
        s.conn.commit()
        assert s.expire_sweep() == 1
        assert len(s.pending_list()) == 0

    def test_candidate_promote(self, tmp_path):
        from my_jarvis.gateway.store import Store
        s = Store(tmp_path / "t.db")
        cid = s.add_candidate("run_command", "echo hi", "测试候选")
        assert s.candidates("candidate")
        row = s.promote_candidate(cid)
        assert row["status"] == "active"


class TestRegistry:
    def test_load_builtin_script(self):
        import my_jarvis.scripts.registry as reg
        reg_scripts = [s for s in reg.load_scripts() if s.id == "cleanup_downloads"]
        assert reg_scripts, "builtin cleanup_downloads script should load"
        s = reg_scripts[0]
        assert s.is_active()
        assert s.fill_defaults({})["dry_run"] is True

    def test_match(self):
        import my_jarvis.scripts.registry as reg
        scripts = reg.load_scripts()
        hit = reg.match_script("帮我整理一下下载文件夹", scripts)
        assert hit is not None and hit.id == "cleanup_downloads"


class TestExecBase:
    async def test_run_spec_shell(self):
        from my_jarvis.tools.base import ExecSpec, run_spec
        r = await run_spec(ExecSpec(kind="shell", script_text="echo hello"))
        assert r.ok and "hello" in r.stdout

    async def test_run_spec_timeout(self):
        from my_jarvis.tools.base import ExecSpec, run_spec
        r = await run_spec(ExecSpec(kind="shell", script_text="sleep 5", timeout=1))
        assert not r.ok and "timeout" in r.stderr
