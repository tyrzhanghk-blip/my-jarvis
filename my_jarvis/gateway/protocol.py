"""统一消息协议。"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


class Entrance(str, Enum):
    cli = "cli"
    web = "web"
    wecom = "wecom"


class RiskLevel(str, Enum):
    readonly = "readonly"
    write = "write"
    destructive = "destructive"
    network = "network"
    script = "script"


class InboundMessage(BaseModel):
    msg_id: str = Field(default_factory=_uuid)
    entrance: Entrance
    session_id: Optional[str] = None
    text: str
    attachments: list[str] = []
    reply_to: Optional[str] = None


class ConfirmRequest(BaseModel):
    action_id: str
    summary: str
    risk_level: RiskLevel
    expires_at: datetime


class OutboundType(str, Enum):
    reply = "reply"
    confirm_request = "confirm_request"
    task_progress = "task_progress"
    push = "push"


class OutboundMessage(BaseModel):
    msg_id: str = Field(default_factory=_uuid)
    session_id: str
    type: OutboundType = OutboundType.reply
    text: str = ""
    confirm: Optional[ConfirmRequest] = None
    entrance: Optional[Entrance] = None  # 目标入口（push 用）
