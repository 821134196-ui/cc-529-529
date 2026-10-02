"""初始化演示数据：工位刀架、刀具台账，并植入要求的各类异常案例。

案例覆盖：缺口、刀柄松动、错放工位、遗失、磨刀超期、磨刀后未复检、
班次跨日交接、缺件（在用未还）。刃口照片本地生成，无外部依赖。
"""
from __future__ import annotations

import struct
import zlib
from datetime import timedelta
from pathlib import Path

from .db import DB, iso, now
from .service import Service

# (编号, 刀型, 轮廓, 工位, 槽位, 领用人(责任人), 周期天, 距上次磨刀天数, 状态, 当前使用人, 异常, 备注)
KNIVES = [
    ("D-001", "主厨刀", "chef",    "ST1", "A-01", "周敏", 7,  2,  "在架", None, None, ""),
    ("D-002", "片刀",   "slicer",  "ST1", "A-02", "陈海", 5,  11, "在架", None, None, ""),   # 磨刀超期
    ("D-003", "砍刀",   "cleaver", "ST1", "A-03", "老赵", 14, 30, "待修", None, "缺口",
     "刃口近刀尖处有约 4mm 缺口，归还清洗时发现"),                         # 缺口案例
    ("D-004", "水果刀", "paring",  "ST1", "A-04", "孙琳", 7,  1,  "待修", None, "松柄",
     "刀柄铆接处松动，握持有旷量"),                                     # 刀柄松动
    ("D-005", "剔骨刀", "boning",  "ST1", "A-05", "周敏", 7,  3,  "在架", None, None, ""),
    ("D-006", "万用刀", "utility", "ST1", "A-06", "陈海", 7,  5,  "在架", None, None, ""),
    ("D-007", "面包刀", "bread",   "ST2", "B-01", "林晓", 10, 4,  "在架", None, None, ""),
    ("D-008", "片刀",   "slicer",  "ST2", "B-02", "林晓", 5,  6,  "在架", None, "错放",
     "应在凉菜间 B-02，实际发现于热菜间 B-06 刀架"),                   # 错放工位
    ("D-009", "水果刀", "paring",  "ST2", "B-03", "孙琳", 7,  2,  "遗失", None, "遗失",
     "昨晚收档核对时 B-03 槽位缺件，全厨房寻找未果"),                    # 遗失冻结
    ("D-010", "主厨刀", "chef",    "ST2", "B-04", "林晓", 7,  1,  "在用", "王强", None, ""),  # 在用=架上缺件
    ("D-011", "三德刀", "santoku", "ST2", "B-05", "何俊", 7,  0,  "磨刀中", None, None, ""),  # 待复检
    ("D-012", "削皮刀", "peeler",  "ST2", "B-06", "孙琳", 14, 9,  "在架", None, None, ""),
    ("D-013", "砍刀",   "cleaver", "ST3", "C-01", "老赵", 14, 6,  "在架", None, None, ""),
    ("D-014", "剔骨刀", "boning",  "ST3", "C-02", "郑涛", 7,  4,  "在架", None, None, ""),
    ("D-015", "片刀",   "slicer",  "ST3", "C-03", "郑涛", 5,  13, "在架", None, None, ""),    # 磨刀超期
    ("D-016", "万用刀", "utility", "ST3", "C-04", "何俊", 7,  2,  "在架", None, None, ""),
]

STATIONS = [
    ("ST1", "热菜间刀架", 2, 6),
    ("ST2", "凉菜间刀架", 2, 6),
    ("ST3", "粗加工间刀架", 2, 6),
]


def _png(width: int, height: int, pixels: bytes) -> bytes:
    """无依赖生成一张灰度 PNG（pixels 为每像素 1 字节）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    raw = b"".join(b"\x00" + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _edge_photo(path: Path, with_nick: bool = False) -> None:
    """生成一张示意刃口照片：亮带为刃线，缺口案例在刃线上留豁口。"""
    w, h = 240, 80
    buf = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            edge_y = 52
            nick = with_nick and 95 <= x <= 112 and abs(y - edge_y) < 12
            if abs(y - edge_y) <= 1 and not (with_nick and 95 <= x <= 112):
                buf[y * w + x] = 235
            elif 30 <= y <= 64:
                buf[y * w + x] = 150 + (x % 7) * 4
            else:
                buf[y * w + x] = 40 + (x % 5) * 3
    path.write_bytes(_png(w, h, bytes(buf)))


def seed(db: DB, photos_dir: Path, reset: bool = False) -> None:
    if reset:
        db.execute("DELETE FROM movements")
        db.execute("DELETE FROM sharpen_jobs")
        db.execute("DELETE FROM knives")
        db.execute("DELETE FROM shifts")
    if db.one("SELECT COUNT(*) c FROM stations")["c"] == 0:
        for sid, name, r, c in STATIONS:
            db.add_station(sid, name, r, c)

    if db.one("SELECT COUNT(*) c FROM knives")["c"] == 0 or reset:
        t0 = now()
        for (kid, name, prof, st, slot, holder, cycle, age, status,
             user, issue, note) in KNIVES:
            last = None if status == "遗失" and kid == "D-009" else iso(t0 - timedelta(days=age))
            db.add_knife({
                "id": kid, "name": name, "profile": prof, "home_station_id": st,
                "slot": slot, "holder": holder, "sharpen_cycle_d": cycle,
                "last_sharpen_ts": last, "status": status, "current_user": user,
                "issue_type": issue, "issue_note": note,
                "issue_ts": iso(t0 - timedelta(hours=14)) if issue else None,
                "found_station_id": "ST1" if kid == "D-008" else None,
                "found_slot": "B-06" if kid == "D-008" else None,
                "quarantined": 1 if status in ("待修", "遗失") else 0,
                "created_ts": iso(t0 - timedelta(days=90)),
            })

        svc = Service(db)
        # 直接开班，避免演示时无班可领
        if not db.open_shift():
            svc.start_shift(f"{t0:%m-%d} 晚市班", t0 - timedelta(hours=2))
        sh = db.open_shift()
        def L(m):
            return t0 - timedelta(minutes=m)
        # 逐刀补流转痕迹，保证每把刀都能追溯最后接触人
        for kid, name, prof, st, slot, holder, cycle, age, status, user, issue, note in KNIVES:
            db.log(kid, "建档上架", holder, L(120), shift_id=sh["id"],
                   to_station=st, to_slot=slot, note="后厨按编号登记入库")
        db.log("D-003", "领用", "老赵", L(100), shift_id=sh["id"])
        db.log("D-003", "归还", "老赵", L(40), shift_id=sh["id"], note="清洗发现缺口")
        db.log("D-003", "异常隔离·缺口", "值班主管", L(38), shift_id=sh["id"],
               note="刃口 4mm 缺口，转待修，禁止发放")
        db.log("D-004", "异常隔离·松柄", "孙琳", L(130), shift_id=sh["id"], note="刀柄松动")
        db.log("D-008", "错放登记", "林晓", L(55), shift_id=sh["id"], to_station="ST1",
               note="凉菜片刀出现在热菜间 B-06")
        db.log("D-009", "登记遗失", "值班主管", L(900), shift_id=None,
               note="昨夜收档 B-03 缺件")
        db.log("D-010", "领用", "王强", L(50), shift_id=sh["id"], note="晚市领刀未还")
        # 磨刀 → 完成 → 未复检（磨刀人老李，等待他人复检）
        jid = svc.request_sharpen("D-011", "何俊", L(35), note="保养排程到期")
        photos_dir.mkdir(parents=True, exist_ok=True)
        pdir = photos_dir / "D-011"
        pdir.mkdir(exist_ok=True)
        photo = pdir / "seed_edge.png"
        _edge_photo(photo)
        svc.complete_sharpen(jid, "老李", "锋利", L(18), photo_path="D-011/seed_edge.png")
        # 缺口刀留一张带豁口的刃口照片
        pdir3 = photos_dir / "D-003"
        pdir3.mkdir(exist_ok=True)
        _edge_photo(pdir3 / "seed_edge_nick.png", with_nick=True)
