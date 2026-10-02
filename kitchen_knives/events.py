"""进程内事件总线：模拟扫码枪事件、磨刀超期提醒，经 SSE 推给页面。"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta

from .db import DB, now, parse


class EventBus:
    def __init__(self):
        self._cond = threading.Condition()
        self._seq = 0
        self._subs: dict[int, queue.Queue] = {}

    def publish(self, kind: str, payload: dict) -> None:
        with self._cond:
            self._seq += 1
            evt = {"id": self._seq, "kind": kind, "payload": payload}
            for q in list(self._subs.values()):
                q.put(evt)
            self._cond.notify_all()

    def subscribe(self) -> tuple[int, queue.Queue]:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._cond:
            sid = self._seq
            self._subs[id(q)] = q
        return sid, q

    def unsubscribe(self, key) -> None:
        with self._cond:
            self._subs.pop(key, None)


class ScannerAdapter:
    """模拟扫码枪适配器：按固定节拍产生扫码事件，也支持手动注入。

    真实部署时替换 emit 背后的来源即可（串口/HID 扫码枪驱动）。
    """

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.mode = "manual"           # manual / auto
        self.actor = "扫码枪"
        self.on_scan = None            # 回调 (code, actor) -> None，由 App 注入领域动作
        self._timer: threading.Timer | None = None
        self._script: list[str] = []
        self._i = 0
        self._lock = threading.Lock()

    def scan(self, code: str, actor: str | None = None) -> None:
        code = code.strip()
        self.bus.publish("scan", {"code": code,
                                  "actor": actor or self.actor,
                                  "ts": now().strftime("%Y-%m-%d %H:%M:%S")})
        if self.on_scan:
            self.on_scan(code, actor or self.actor)

    def start_script(self, codes: list[str], actor: str, interval: float = 1.5) -> None:
        with self._lock:
            self.stop_script()
            self.mode = "auto"
            self.actor = actor
            self._script = list(codes)
            self._i = 0
            self._tick(interval)

    def stop_script(self) -> None:
        self.mode = "manual"
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _tick(self, interval: float) -> None:
        if self._i < len(self._script):
            code = self._script[self._i]
            self._i += 1
            self.scan(code, self.actor)
            self._timer = threading.Timer(interval, self._tick, args=(interval,))
            self._timer.daemon = True
            self._timer.start()
        else:
            self.mode = "manual"
            self.bus.publish("scanner", {"msg": "模拟扫码序列已播完", "ts": now().strftime("%H:%M:%S")})


class ReminderDaemon:
    """进程内保养提醒：周期检查磨刀超期 / 待复检未处理，推送到页面。"""

    def __init__(self, db: DB, bus: EventBus, interval: float = 30.0):
        self.db = db
        self.bus = bus
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._alerted_jobs: set[int] = set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="reminder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def check_once(self, at: datetime | None = None) -> dict:
        at = at or now()
        overdue, unchecked = [], []
        for k in self.db.all_knives():
            if k["status"] in ("遗失", "磨刀中", "待修"):
                continue
            if self.db.sharpen_due(k, at):
                last = parse(k["last_sharpen_ts"])
                overdue.append({"knife_id": k["id"], "name": k["name"],
                                "days": (at - last).days if last else None,
                                "cycle": k["sharpen_cycle_d"]})
        for j in self.db.q("SELECT j.*, k.name FROM sharpen_jobs j JOIN knives k ON k.id=j.knife_id "
                           "WHERE j.status='待复检'"):
            waited = int((at - parse(j["sharpened_ts"])).total_seconds() // 60) if j["sharpened_ts"] else 0
            unchecked.append({"job_id": j["id"], "knife_id": j["knife_id"], "name": j["name"],
                              "sharpened_by": j["sharpened_by"], "waited_min": waited})
        return {"overdue": overdue, "unchecked": unchecked}

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                r = self.check_once()
                if r["overdue"] or r["unchecked"]:
                    self.bus.publish("reminder", r)
            except Exception as e:  # 提醒线程绝不能拖垮主进程
                self.bus.publish("reminder_error", {"msg": str(e)})
