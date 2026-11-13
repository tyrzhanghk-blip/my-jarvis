"""SQLite 存储：sessions / pending_actions / audit_log / candidate_templates / schedules。"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
import uuid

from .protocol import Entrance, RiskLevel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, entrance TEXT, last_active_at TEXT,
  status TEXT DEFAULT 'active', created_at TEXT);
CREATE TABLE IF NOT EXISTS pending_actions (
  action_id TEXT PRIMARY KEY, session_id TEXT, entrance TEXT,
  tool_name TEXT, params_json TEXT, risk_level TEXT,
  summary TEXT, status TEXT DEFAULT 'pending',
  expires_at TEXT, decided_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, entrance TEXT,
  session_id TEXT, tool_name TEXT, params_json TEXT,
  decision TEXT, matched_rule TEXT, exit_code INTEGER,
  duration_ms INTEGER, msg_id TEXT);
CREATE TABLE IF NOT EXISTS candidate_templates (
  id TEXT PRIMARY KEY, created_from_session TEXT, origin_tool TEXT,
  script_text TEXT, description TEXT, status TEXT DEFAULT 'candidate',
  promoted_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, cron TEXT, target TEXT,
  push_entrance TEXT, enabled INTEGER DEFAULT 1, created_at TEXT);
"""


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ── sessions ──
    def get_or_create_session(self, session_id: Optional[str], entrance: Entrance) -> str:
        if session_id:
            row = self.conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row:
                self.conn.execute("UPDATE sessions SET last_active_at=? WHERE id=?",
                                  (datetime.now().isoformat(), session_id))
                self.conn.commit()
                return session_id
        sid = session_id or f"{entrance.value}-{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()
        self.conn.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)",
                          (sid, entrance.value, now, "active", now))
        self.conn.commit()
        return sid

    # ── pending actions ──
    def add_pending(self, session_id: str, entrance: Entrance, tool_name: str,
                    params: dict, risk: RiskLevel, summary: str, timeout_min: int) -> str:
        aid = uuid.uuid4().hex
        now = datetime.now()
        exp = (now + timedelta(minutes=timeout_min)).isoformat()
        self.conn.execute(
            "INSERT INTO pending_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, session_id, entrance.value, tool_name,
             _json(params), risk.value, summary, "pending", exp, None, now.isoformat()))
        self.conn.commit()
        return aid

    def decide(self, action_id: str, decision: str) -> Optional[dict]:
        """approve / reject；超时的由 expire_sweep 流转。"""
        row = self.conn.execute("SELECT * FROM pending_actions WHERE action_id=?", (action_id,)).fetchone()
        if not row or row["status"] != "pending":
            return dict(row) if row else None
        status = "executing" if decision == "approve" else "rejected"
        self.conn.execute("UPDATE pending_actions SET status=?, decided_at=? WHERE action_id=?",
                          (status, datetime.now().isoformat(), action_id))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM pending_actions WHERE action_id=?", (action_id,)).fetchone()
        return dict(row)

    def finish(self, action_id: str, ok: bool) -> None:
        self.conn.execute("UPDATE pending_actions SET status=? WHERE action_id=?",
                          ("done" if ok else "rejected", action_id))
        self.conn.commit()

    def expire_sweep(self) -> int:
        now = datetime.now().isoformat()
        cur = self.conn.execute(
            "UPDATE pending_actions SET status='rejected', decided_at=? "
            "WHERE status='pending' AND expires_at < ?", (now, now))
        self.conn.commit()
        return cur.rowcount

    def pending_list(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM pending_actions WHERE status='pending' ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ── audit ──
    def audit(self, entrance: str, session_id: str, tool_name: str, params: dict,
              decision: str, matched_rule: str, exit_code: Optional[int] = None,
              duration_ms: Optional[int] = None, msg_id: str = "") -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts, entrance, session_id, tool_name, params_json,"
            " decision, matched_rule, exit_code, duration_ms, msg_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), entrance, session_id, tool_name, _json(params),
             decision, matched_rule, exit_code, duration_ms, msg_id))
        self.conn.commit()

    # ── candidates ──
    def add_candidate(self, origin_tool: str, script_text: str, description: str,
                      created_from_session: str = "") -> str:
        cid = uuid.uuid4().hex
        self.conn.execute("INSERT INTO candidate_templates VALUES (?,?,?,?,?,?,?,?)",
                          (cid, created_from_session, origin_tool, script_text,
                           description, "candidate", None, datetime.now().isoformat()))
        self.conn.commit()
        return cid

    def candidates(self, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self.conn.execute("SELECT * FROM candidate_templates WHERE status=?", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM candidate_templates").fetchall()
        return [dict(r) for r in rows]

    def promote_candidate(self, cid: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM candidate_templates WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        self.conn.execute("UPDATE candidate_templates SET status='active', promoted_at=? WHERE id=?",
                          (datetime.now().isoformat(), cid))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM candidate_templates WHERE id=?", (cid,)).fetchone()
        return dict(row)


def _json(o: Any) -> str:
    import json
    return json.dumps(o, ensure_ascii=False, default=str)
