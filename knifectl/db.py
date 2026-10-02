"""SQLite 持久层：建表与连接管理。逐刀流转全部落 events 表。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS staff (
    id         TEXT PRIMARY KEY,          -- 工号，同时是工牌条码
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('chef','sharpener','inspector','manager')),
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stations (
    id     TEXT PRIMARY KEY,              -- 例如 hot_a
    name   TEXT NOT NULL,
    zone   TEXT NOT NULL
);

-- 刀架槽位：工位内按 架/排/列 的真实位置排列
CREATE TABLE IF NOT EXISTS slots (
    id          TEXT PRIMARY KEY,         -- 槽位条码，例如 A-03
    station_id  TEXT NOT NULL REFERENCES stations(id),
    rack        TEXT NOT NULL,            -- 刀架编号
    slot_row    INTEGER NOT NULL,
    slot_col    INTEGER NOT NULL,
    UNIQUE (station_id, rack, slot_row, slot_col)
);

CREATE TABLE IF NOT EXISTS knives (
    id                   TEXT PRIMARY KEY, -- 刀具条码/编号 K-001
    type                 TEXT NOT NULL,    -- chef/slicer/cleaver/santoku/paring/boning
    home_slot_id         TEXT NOT NULL REFERENCES slots(id),
    status               TEXT NOT NULL DEFAULT 'in_rack'
        CHECK (status IN ('in_rack','issued','sharpening','quarantine','repair','lost')),
    edge_state           TEXT NOT NULL DEFAULT 'sharp'
        CHECK (edge_state IN ('sharp','acceptable','dull','chipped')),
    quarantine_reason    TEXT,             -- chipped/loose_handle/misplaced/found_pending/repair_pending/rejected
    current_holder_id    TEXT REFERENCES staff(id),
    issued_at            TEXT,
    issued_shift_id      INTEGER,
    sharpen_interval_days INTEGER NOT NULL DEFAULT 7,
    last_sharpen_at      TEXT,
    sharpen_done_at      TEXT,             -- 磨刀完成时间（待复检）
    next_sharpen_due     TEXT,
    sharpened_by         TEXT REFERENCES staff(id),
    pending_reinspect    INTEGER NOT NULL DEFAULT 0,
    last_touched_by      TEXT REFERENCES staff(id),
    last_touched_at      TEXT,
    found_at             TEXT,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS shifts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    leader_id   TEXT REFERENCES staff(id),
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    knife_id  TEXT NOT NULL REFERENCES knives(id),
    ts        TEXT NOT NULL,
    actor_id  TEXT REFERENCES staff(id),
    type      TEXT NOT NULL,               -- 见 service.EVENT_LABELS
    shift_id  INTEGER REFERENCES shifts(id),
    detail    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_knife ON events(knife_id, id);

CREATE TABLE IF NOT EXISTS photos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    knife_id  TEXT NOT NULL REFERENCES knives(id),
    event_id  INTEGER REFERENCES events(id),
    path      TEXT NOT NULL,               -- 本地相对路径
    label     TEXT NOT NULL,               -- edge(刃口) / body(刀身)
    taken_by  TEXT REFERENCES staff(id),
    taken_at  TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
