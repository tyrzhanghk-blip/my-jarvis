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




class TestPromoteLoop:
    def test_promote_candidate_creates_script(self, tmp_path, monkeypatch):
        """沉淀闭环：DB 候选 → 脚本目录 → load_scripts 可见且 active。"""
        import my_jarvis.gateway.paths as paths
        import my_jarvis.scripts.registry as reg
        from my_jarvis.gateway.store import Store
        monkeypatch.setattr(paths, "SCRIPTS_DIR", tmp_path / "scripts")
        reg.load_scripts.cache_clear() if hasattr(reg.load_scripts, "cache_clear") else None

        s = Store(tmp_path / "t.db")
        cid = s.add_candidate("run_command", "echo hello-promoted",
                              "create a test dir named promoted")
        sd = reg.promote_candidate(s, cid)
        assert sd.is_active()
        # 文件落盘
        d = tmp_path / "scripts" / sd.id
        assert (d / "manifest.yaml").exists()
        assert "hello-promoted" in (d / "run.sh").read_text()
        # load_scripts 能从用户目录加载
        found = [x for x in reg.load_scripts() if x.id == sd.id]
        assert found and found[0].is_active()
        # DB 状态翻转
        assert s.candidates("active") and not s.candidates("candidate")

    def test_slugify_unique_and_safe(self):
        from my_jarvis.scripts.registry import _slugify
        a = _slugify("列出最新文件")
        b = _slugify("list newest files")
        assert a != b and a.startswith("script-")
        assert b.startswith("list-newest-files")




class TestModelRouting:
    def test_model_for_custom_base_url(self, monkeypatch, tmp_path):
        """自定义 base_url → 返回绑定 client 的 Model 实例。"""
        import my_jarvis.gateway.paths as paths
        import my_jarvis.agent.core as core
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
        (tmp_path / "jarvis.yaml").write_text(
            "models:\n  base_url: https://relay.example.com/v1\n"
            "  strong: { model: hy4 }\n  light: { model: hy3 }\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        core._CLIENTS.clear()
        from agents import OpenAIChatCompletionsModel
        m = core._model_for("strong")
        assert isinstance(m, OpenAIChatCompletionsModel)
        m2 = core._model_for("light")
        assert isinstance(m2, OpenAIChatCompletionsModel)
        assert m is not m2

    def test_model_for_per_alias_override(self, monkeypatch, tmp_path):
        import my_jarvis.gateway.paths as paths
        import my_jarvis.agent.core as core
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
        (tmp_path / "jarvis.yaml").write_text(
            "models:\n  strong: { model: gpt-4.1 }\n"
            "  light: { model: hy3, base_url: https://other.example.com/v1 }\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        core._CLIENTS.clear()
        from agents import OpenAIChatCompletionsModel
        assert core._model_for("strong") == "gpt-4.1"
        assert isinstance(core._model_for("light"), OpenAIChatCompletionsModel)

    def test_client_shared_per_base_url(self, monkeypatch, tmp_path):
        import my_jarvis.gateway.paths as paths
        import my_jarvis.agent.core as core
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
        (tmp_path / "jarvis.yaml").write_text(
            "models:\n  base_url: https://relay.example.com/v1\n"
            "  strong: { model: a }\n  light: { model: b }\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        core._CLIENTS.clear()
        core._model_for("strong"); core._model_for("light")
        assert len(core._CLIENTS) == 1


class TestExecBase:
    async def test_run_spec_shell(self):
        from my_jarvis.tools.base import ExecSpec, run_spec
        r = await run_spec(ExecSpec(kind="shell", script_text="echo hello"))
        assert r.ok and "hello" in r.stdout

    async def test_run_spec_timeout(self):
        from my_jarvis.tools.base import ExecSpec, run_spec
        r = await run_spec(ExecSpec(kind="shell", script_text="sleep 5", timeout=1))
        assert not r.ok and "timeout" in r.stderr
