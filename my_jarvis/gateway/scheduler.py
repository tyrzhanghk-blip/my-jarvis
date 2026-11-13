"""调度器：cron 定时任务 + 心跳推送。"""
from __future__ import annotations
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


class JarvisScheduler:
    def __init__(self, router):
        self.router = router
        self.sched = AsyncIOScheduler()

    def start(self) -> None:
        self._load()
        self.sched.start()

    def _load(self) -> None:
        rows = self.router.store.conn.execute("SELECT * FROM schedules WHERE enabled=1").fetchall()
        for row in rows:
            self.sched.add_job(self._fire, CronTrigger.from_crontab(row["cron"]),
                               args=[row["id"], row["target"], row["push_entrance"]],
                               id=row["id"], replace_existing=True)

    async def _fire(self, schedule_id: str, target: str, push_entrance: str) -> None:
        """触发：target 为脚本 id 或 'agent:<提示词>'；结果推送到指定入口。"""
        from my_jarvis.gateway.protocol import Entrance, InboundMessage
        text = f"[定时任务 {schedule_id}] " + (
            f"运行脚本 {target}" if not target.startswith("agent:")
            else target[len("agent:"):])
        msg = InboundMessage(entrance=Entrance(push_entrance or "web"), text=text,
                             session_id=f"cron-{schedule_id}")
        await self.router.handle(msg)

    def heartbeat(self, cron: str, prompt: str, push_entrance: str = "web") -> None:
        """注册心跳任务（HEARTBEAT 语义）。"""
        sid = f"heartbeat-{abs(hash(prompt)) % 99999}"
        self.router.store.conn.execute(
            "INSERT OR REPLACE INTO schedules VALUES (?,?,?,?,?,?)",
            (sid, cron, f"agent:{prompt}", push_entrance, 1, None))
        self.router.store.conn.commit()
        self.sched.add_job(self._fire, CronTrigger.from_crontab(cron),
                           args=[sid, f"agent:{prompt}", push_entrance],
                           id=sid, replace_existing=True)

    def stop(self) -> None:
        self.sched.shutdown(wait=False)
