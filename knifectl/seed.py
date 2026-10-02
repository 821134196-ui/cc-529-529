"""种子数据：一条命令即可得到带缺口案例的可演示环境。

覆盖：正常在架、磨刀超期、刃口缺口（带照片）、刀柄松动、磨刀后待复检、
遗失冻结、班次跨日未还、错放工位隔离、待修、找回待检、即将到期。
"""
from __future__ import annotations

import json
from datetime import timedelta

from . import photos
from .db import connect, init_db
from .service import KNIFE_TYPES, KnifeService, iso

STAFF = [
    ("E01", "张德海", "chef"),
    ("E02", "林小满", "chef"),
    ("E03", "赵铁柱", "chef"),
    ("E04", "周晓梅", "chef"),
    ("S01", "莫立坚", "sharpener"),
    ("S02", "钱守正", "inspector"),
    ("M01", "冯班", "manager"),
]

STATIONS = [
    ("hot_a", "热厨 A 线", "热厨"),
    ("hot_b", "热厨 B 线", "热厨"),
    ("cold", "冷菜间", "冷菜"),
]

# (刀号, 刀型, 槽位, 磨刀周期天)
KNIVES = [
    ("K-001", "chef", "HA-01", 7),
    ("K-002", "slicer", "HA-02", 7),    # 磨刀超期
    ("K-003", "cleaver", "HA-03", 14),  # 刃口缺口
    ("K-004", "chef", "HA-04", 7),      # 刀柄松动
    ("K-005", "santoku", "HA-05", 7),   # 磨刀后待复检
    ("K-006", "paring", "HA-06", 10),   # 遗失
    ("K-007", "boning", "HB-01", 7),    # 跨日班次仍领用
    ("K-008", "slicer", "HB-02", 7),    # 错放工位
    ("K-009", "cleaver", "HB-03", 30),  # 待修
    ("K-010", "santoku", "HB-04", 7),   # 磨刀即将到期
    ("K-011", "chef", "CL-01", 7),      # 今日早班正常领用
    ("K-012", "paring", "CL-02", 10),   # 找回待检
]


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def seed(db_path: str, base_dir: str, now) -> dict:
    conn = connect(db_path)
    init_db(conn)
    conn.execute("DELETE FROM photos")
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM knives")
    conn.execute("DELETE FROM shifts")
    conn.execute("DELETE FROM slots")
    conn.execute("DELETE FROM stations")
    conn.execute("DELETE FROM staff")

    clock = Clock(now)
    svc = KnifeService(conn, clock=clock)

    for sid, name, role in STAFF:
        conn.execute("INSERT INTO staff(id,name,role) VALUES (?,?,?)", (sid, name, role))
    for stid, name, zone in STATIONS:
        conn.execute("INSERT INTO stations(id,name,zone) VALUES (?,?,?)", (stid, name, zone))

    # 真实刀架：每工位一架，2 排 × 4 列
    prefixes = {"hot_a": "HA", "hot_b": "HB", "cold": "CL"}
    for stid, _, _ in STATIONS:
        n = 0
        for row in (1, 2):
            for col in range(1, 5):
                n += 1
                conn.execute(
                    "INSERT INTO slots(id,station_id,rack,slot_row,slot_col) VALUES (?,?,?,?,?)",
                    (f"{prefixes[stid]}-{n:02d}", stid, "R1", row, col))

    for kid, ktype, slot, interval in KNIVES:
        conn.execute(
            """INSERT INTO knives(id,type,home_slot_id,sharpen_interval_days,
               next_sharpen_due,last_sharpen_at,last_touched_by,last_touched_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kid, ktype, slot, interval, iso(now + timedelta(days=30)),
             iso(now - timedelta(days=2)), "S02", iso(now - timedelta(days=2))))

    summary: dict[str, str] = {}

    # ---- 历史夜班（昨日开班，至今未收班 → 班次跨日）----
    clock.now = now - timedelta(days=1, hours=10)
    night = svc.start_shift("夜班（昨日）", "M01", started_at=clock.now)
    svc.issue("K-007", "E03", night)
    summary["cross_day_knife"] = "K-007"

    # K-002 磨刀超期
    clock.now = now - timedelta(days=12)
    conn.execute(
        "UPDATE knives SET last_sharpen_at=?, next_sharpen_due=? WHERE id='K-002'",
        (iso(clock.now), iso(clock.now + timedelta(days=7))))
    conn.execute(
        "INSERT INTO events(knife_id,ts,actor_id,type,detail) VALUES (?,?,?,?,?)",
        ("K-002", iso(clock.now), "S02", "reinspect_pass",
         json.dumps({"historical": True}, ensure_ascii=False)))

    # K-010 明天到期
    conn.execute(
        "UPDATE knives SET next_sharpen_due=? WHERE id='K-010'",
        (iso(now + timedelta(days=1)),))

    # ---- 今日早班 ----
    clock.now = now - timedelta(hours=2)
    morning = svc.start_shift("早班", "M01", started_at=clock.now)
    svc.issue("K-011", "E01", morning)

    # K-003 领出、归还时发现刃口缺口 → 隔离（带刃口照片）
    svc.issue("K-003", "E02", morning)
    clock.now = now - timedelta(hours=1, minutes=40)
    svc.return_knife("K-003", "E02", "HA-03", condition="chipped")
    p = photos.save_synthetic(base_dir, "K-003", "chipped", tag="edge")
    ev = conn.execute("SELECT MAX(id) m FROM events WHERE knife_id='K-003'").fetchone()["m"]
    conn.execute(
        "INSERT INTO photos(knife_id,event_id,path,label,taken_by,taken_at) VALUES (?,?,?,?,?,?)",
        ("K-003", ev, p, "edge", "E02", iso(clock.now)))
    summary["chipped"] = "K-003"

    # K-004 刀柄松动 → 隔离 → 送修流转一部分？这里保持隔离待处理
    svc.issue("K-004", "E04", morning)
    clock.now = now - timedelta(hours=1, minutes=20)
    svc.return_knife("K-004", "E04", "HA-04", condition="loose")

    # K-009 此前已送修（待修状态，冻结发放）
    svc.issue("K-009", "E03", morning)
    svc.return_knife("K-009", "E03", "HB-03", condition="loose")
    clock.now = now - timedelta(hours=1)
    svc.send_repair("K-009", "M01")

    # K-008 归还是扫错槽位（扫到冷菜间 CL-03）→ 错放工位隔离
    svc.issue("K-008", "E01", morning)
    clock.now = now - timedelta(minutes=50)
    svc.return_knife("K-008", "E01", "CL-03", condition="clean")

    # K-005 磨刀完成，等待 S02 复检（S01 不能自批）
    clock.now = now - timedelta(minutes=35)
    svc.send_sharpen("K-005", "M01")
    clock.now = now - timedelta(minutes=15)
    p5 = photos.save_synthetic(base_dir, "K-005", "sharp", tag="edge")
    svc.sharpen_done("K-005", "S01", photo_path=p5)

    # K-006 遗失
    clock.now = now - timedelta(hours=3)
    svc.issue("K-006", "E04", morning)
    clock.now = now - timedelta(minutes=25)
    svc.report_lost("K-006", "E04", note="收档核对时未在刀架，全场查找无果")
    summary["lost"] = "K-006"

    # K-012 昨日遗失，今早被找回，等待复检
    clock.now = now - timedelta(days=1)
    old_shift = svc.start_shift("归档夜班", "M01", started_at=clock.now)
    svc.issue("K-012", "E02", old_shift)
    svc.report_lost("K-012", "E02")
    clock.now = now - timedelta(minutes=20)
    svc.report_found("K-012", "E03", slot_id="CL-02")
    # 归档夜班收班（遗失已清占用，可正常收班）
    svc.close_shift(old_shift)

    clock.now = now
    conn.commit()
    conn.close()
    summary.update({"morning_shift": morning, "night_shift": night,
                    "knife_types": KNIFE_TYPES})
    return summary
