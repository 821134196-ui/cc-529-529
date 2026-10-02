"""进程内提醒：后台线程定时扫描，不落库、不依赖外部服务。

提醒类型：
- overdue_sharpen  磨刀超期
- due_soon         磨刀将到期（默认 1 天内）
- pending_reinspect 磨刀/维修/找回后等待复检
- outstanding      已收班/跨日仍领用未还（由调用方传入当前班次判断）
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReminderEngine:
    def __init__(self, get_conn: Callable, interval_sec: float = 30.0,
                 due_soon_days: int = 1):
        self.get_conn = get_conn
        self.interval_sec = interval_sec
        self.due_soon_days = due_soon_days
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._snapshot: list[dict] = []

    # ----- 纯计算，方便单测 -----

    def scan(self, now: datetime | None = None) -> list[dict]:
        now = now or _utcnow()
        conn = self.get_conn()
        out: list[dict] = []
        for k in conn.execute("SELECT * FROM knives").fetchall():
            if k["status"] == "lost":
                out.append({"level": "danger", "type": "lost", "knife_id": k["id"],
                            "msg": f"{k['id']} 已报遗失并冻结发放"})
                continue
            if k["status"] == "repair":
                out.append({"level": "warn", "type": "in_repair", "knife_id": k["id"],
                            "msg": f"{k['id']} 待修中，修复复检通过前不得发放"})
            if k["status"] == "quarantine" and k["quarantine_reason"] not in (
                    "found_pending", "repair_pending"):
                out.append({"level": "warn", "type": "quarantine", "knife_id": k["id"],
                            "msg": f"{k['id']} 异常隔离中"})
            if k["pending_reinspect"]:
                out.append({"level": "warn", "type": "pending_reinspect", "knife_id": k["id"],
                            "msg": f"{k['id']} 磨刀完成，等待他人复检锋利度与刀身完整性"})
            if k["quarantine_reason"] in ("found_pending", "repair_pending"):
                label = "找回" if k["quarantine_reason"] == "found_pending" else "维修"
                out.append({"level": "warn", "type": "pending_reinspect", "knife_id": k["id"],
                            "msg": f"{k['id']} {label}后等待他人复检"})
            due = k["next_sharpen_due"]
            if due and k["status"] in ("in_rack", "issued"):
                due_dt = datetime.fromisoformat(due)
                if due_dt < now:
                    out.append({"level": "danger", "type": "overdue_sharpen", "knife_id": k["id"],
                                "msg": f"{k['id']} 磨刀超期（应于 {due[:10]} 前），已禁止发放"})
                elif due_dt <= now + timedelta(days=self.due_soon_days):
                    out.append({"level": "info", "type": "due_soon", "knife_id": k["id"],
                                "msg": f"{k['id']} 磨刀将于 {due[:10]} 到期"})
        # 跨日未还
        for sh in conn.execute("SELECT * FROM shifts WHERE ended_at IS NULL").fetchall():
            started = datetime.fromisoformat(sh["started_at"])
            if started.date() != now.date():
                cnt = conn.execute(
                    "SELECT COUNT(*) c FROM knives WHERE status='issued' AND issued_shift_id=?",
                    (sh["id"],)).fetchone()["c"]
                if cnt:
                    out.append({"level": "danger", "type": "cross_day_open", "shift_id": sh["id"],
                                "msg": f"班次 {sh['name']} 已跨日，仍有 {cnt} 把刀未交接/归还"})
        out.sort(key=lambda r: (r["level"] != "danger", r["type"], r.get("knife_id", "")))
        return out

    def latest(self) -> list[dict]:
        with self._lock:
            return list(self._snapshot)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                snap = self.scan()
            except Exception as exc:  # 提醒线程不能拖垮主进程
                snap = [{"level": "danger", "type": "engine_error", "msg": f"提醒扫描失败: {exc}"}]
            with self._lock:
                self._snapshot = snap

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        # 立即扫一次，避免页面在首个周期前没有提醒
        try:
            self._snapshot = self.scan()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._loop, name="reminder-engine",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
