"""模拟扫码枪适配器。

真实扫码枪对系统来说就是一个「扫描 -> 条码」事件源。这里用进程内队列模拟：
- 其他模块/测试/页面通过 emit() 投递扫描事件（可选用脚本回放一连串扫描）；
- 领还页轮询 /api/scan 取走事件，模拟键盘楔入式扫码枪。
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(order=True)
class ScanEvent:
    seq: int
    code: str = field(compare=False)          # 扫到的条码：刀号 / 工号 / 槽位号
    kind: str = field(compare=False)          # knife / staff / slot / unknown
    ts: str = field(compare=False, default_factory=_now_iso)


class MockScanner:
    """单例式模拟扫码枪：线程安全 FIFO，web 层和测试共用。"""

    def __init__(self) -> None:
        self._q: "queue.Queue[ScanEvent]" = queue.Queue()
        self._seq = 0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._q = queue.Queue()
            self._seq = 0

    def emit(self, code: str, kind: str | None = None) -> ScanEvent:
        with self._lock:
            self._seq += 1
            ev = ScanEvent(seq=self._seq, code=code, kind=kind or self.classify(code))
        self._q.put(ev)
        return ev

    def drain(self, max_events: int = 20) -> list[dict]:
        out = []
        for _ in range(max_events):
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            out.append({"seq": ev.seq, "code": ev.code, "kind": ev.kind, "ts": ev.ts})
        return out

    def pending(self) -> int:
        return self._q.qsize()

    @staticmethod
    def classify(code: str) -> str:
        c = code.strip().upper()
        if c.startswith("K-"):
            return "knife"
        if c[:1].isalpha() and "-" in c and c.split("-", 1)[0].isalpha() and c.split("-", 1)[1][:1].isdigit():
            # A-03 这类槽位条码（字母-数字）
            return "slot" if len(c.split("-")[0]) <= 2 else "unknown"
        if c.startswith(("E", "M", "S")) and c[1:].isdigit():
            return "staff"
        return "unknown"
