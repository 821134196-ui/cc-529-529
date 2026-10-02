"""SQLite 数据访问层：逐刀流转全程留痕，可追溯最后接触人。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ISO = "%Y-%m-%dT%H:%M:%S"


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime(ISO) if dt else None


def parse(s: str | None) -> datetime | None:
    return datetime.strptime(s, ISO) if s else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    rack_rows   INTEGER NOT NULL,
    rack_cols   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knives (
    id               TEXT PRIMARY KEY,           -- 刀号/条码
    name             TEXT NOT NULL,              -- 刀型
    profile          TEXT NOT NULL DEFAULT 'chef',
    home_station_id  TEXT NOT NULL REFERENCES stations(id),
    slot             TEXT NOT NULL,              -- 真实槽位 如 A-03
    holder           TEXT NOT NULL DEFAULT '周敏',
    sharpen_cycle_d  INTEGER NOT NULL DEFAULT 7,
    last_sharpen_ts  TEXT,
    status           TEXT NOT NULL DEFAULT '在架',
    current_user     TEXT,
    issue_type       TEXT,                       -- 缺口/松柄/错放/遗失/超期/未复检
    issue_note       TEXT,
    issue_ts          TEXT,
    found_station_id TEXT,                       -- 错放时实际所在工位
    found_slot       TEXT,                       -- 错放时实际所在槽位
    quarantined      INTEGER NOT NULL DEFAULT 0,
    created_ts       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shifts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    start_ts    TEXT NOT NULL,
    end_ts      TEXT,
    status      TEXT NOT NULL DEFAULT '进行中'    -- 进行中/已交接/已关闭
);

CREATE TABLE IF NOT EXISTS movements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    knife_id    TEXT REFERENCES knives(id),
    shift_id    INTEGER REFERENCES shifts(id),
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL,                    -- 最后接触人
    to_station  TEXT,
    to_slot     TEXT,
    note        TEXT,
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_move_knife ON movements(knife_id, id);

CREATE TABLE IF NOT EXISTS sharpen_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    knife_id      TEXT NOT NULL REFERENCES knives(id),
    shift_id      INTEGER REFERENCES shifts(id),
    request_ts    TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    sharpened_ts  TEXT,
    sharpened_by  TEXT,
    edge_state    TEXT,                           -- 锋利/合格/偏钝
    check_ts      TEXT,
    checked_by    TEXT,
    check_result  TEXT,                           -- 通过/驳回
    check_note    TEXT,
    photo_path    TEXT,
    status        TEXT NOT NULL DEFAULT '待磨刀',  -- 待磨刀/待复检/通过/驳回
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sharpen_knife ON sharpen_jobs(knife_id, id);
"""


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """给旧库补新增列（SQLite 的 ADD COLUMN 幂等由本检查保证）。"""
        cols = {r["name"] for r in self.q("PRAGMA table_info(knives)")}
        for col, decl in (("found_station_id", "TEXT"), ("found_slot", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE knives ADD COLUMN {col} {decl}")

    # ---- 基础工具 ----
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(args)).fetchone()

    def execute(self, sql: str, args: Iterable[Any] | dict = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, args if isinstance(args, dict) else tuple(args))
        self.conn.commit()
        return cur

    def knife(self, knife_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM knives WHERE id=?", (knife_id,))

    def station(self, station_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM stations WHERE id=?", (station_id,))

    def all_knives(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM knives ORDER BY home_station_id, slot")

    def open_shift(self) -> sqlite3.Row | None:
        return self.one("SELECT * FROM shifts WHERE status='进行中' ORDER BY id DESC LIMIT 1")

    def last_movement(self, knife_id: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT * FROM movements WHERE knife_id=? ORDER BY id DESC LIMIT 1", (knife_id,)
        )

    def last_sharpen_job(self, knife_id: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT * FROM sharpen_jobs WHERE knife_id=? ORDER BY id DESC LIMIT 1", (knife_id,)
        )

    # ---- 写入 ----
    def add_station(self, sid: str, name: str, rows: int, cols: int) -> None:
        self.execute(
            "INSERT OR IGNORE INTO stations(id,name,rack_rows,rack_cols) VALUES(?,?,?,?)",
            (sid, name, rows, cols),
        )

    def add_knife(self, k: dict) -> None:
        self.execute(
            """INSERT OR REPLACE INTO knives
               (id,name,profile,home_station_id,slot,holder,sharpen_cycle_d,
                last_sharpen_ts,status,current_user,issue_type,issue_note,issue_ts,
                found_station_id,found_slot,quarantined,created_ts)
               VALUES (:id,:name,:profile,:home_station_id,:slot,:holder,:sharpen_cycle_d,
                :last_sharpen_ts,:status,:current_user,:issue_type,:issue_note,:issue_ts,
                :found_station_id,:found_slot,:quarantined,:created_ts)""",
            k,
        )

    def start_shift(self, name: str, ts: datetime) -> int:
        cur = self.execute(
            "INSERT INTO shifts(name,start_ts,status) VALUES(?,?, '进行中')",
            (name, iso(ts)),
        )
        return cur.lastrowid

    def log(self, knife_id: str | None, action: str, actor: str, ts: datetime,
            shift_id: int | None = None, to_station: str | None = None,
            to_slot: str | None = None, note: str | None = None) -> int:
        cur = self.execute(
            """INSERT INTO movements(knife_id,shift_id,action,actor,to_station,to_slot,note,ts)
               VALUES(?,?,?,?,?,?,?,?)""",
            (knife_id, shift_id, action, actor, to_station, to_slot, note, iso(ts)),
        )
        return cur.lastrowid

    # ---- 派生状态 ----
    def sharpen_due(self, k: sqlite3.Row, at: datetime | None = None) -> bool:
        """磨刀是否超期（最后磨刀日 + 周期 <= 现在）。"""
        at = at or now()
        last = parse(k["last_sharpen_ts"])
        if last is None:
            return True
        return last + timedelta(days=k["sharpen_cycle_d"]) <= at

    def knife_flags(self, k: sqlite3.Row, at: datetime | None = None) -> list[dict]:
        """汇总一把刀当前所有应被关注的标记（缺件/异常/超期/未复检）。"""
        at = at or now()
        flags: list[dict] = []
        if k["issue_type"] == "错放" and k["status"] == "在架":
            where = f"实际发现于 {k['found_station_id'] or '其他工位'}" \
                    f"{(' ' + k['found_slot']) if k['found_slot'] else ''}"
            flags.append({"kind": "错放", "label": "错放工位",
                          "note": k["issue_note"] or where})
        if k["status"] in ("待修", "遗失"):
            flags.append({"kind": k["issue_type"] or k["status"],
                          "label": f"{k['status']}·{k['issue_type'] or ''}".strip("·"),
                          "note": k["issue_note"]})
        job = self.last_sharpen_job(k["id"])
        if job and job["status"] == "待复检":
            flags.append({"kind": "未复检", "label": "磨刀后未复检",
                          "note": f"磨刀人 {job['sharpened_by']}，等待他人复检"})
        if k["status"] not in ("待修", "遗失", "磨刀中") and self.sharpen_due(k, at):
            over = (at - (parse(k["last_sharpen_ts"]) or at)).days if k["last_sharpen_ts"] else None
            flags.append({"kind": "超期",
                          "label": "磨刀超期" + (f"（已过 {over} 天）" if over is not None else "（从未磨刀）"),
                          "note": f"周期 {k['sharpen_cycle_d']} 天"})
        return flags

    def export(self) -> str:
        tables = ("stations", "knives", "shifts", "movements", "sharpen_jobs")
        out = {}
        for t in tables:
            out[t] = [dict(r) for r in self.q(f"SELECT * FROM {t} ORDER BY id")]
        return json.dumps(out, ensure_ascii=False, indent=2)
