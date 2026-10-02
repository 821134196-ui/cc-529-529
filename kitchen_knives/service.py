"""领域服务：领还、磨刀、复检、异常隔离、跨班交接的全部业务规则。

关键不变式：
  * 待修/遗失（隔离中）的刀不能重新发放；
  * 同一把刀同一时刻只能被一人领用（独占）；
  * 磨刀者不能复检自己磨的刀（职责分离）；
  * 每次流转写入 movements，最后接触人可取最近一条记录追溯。
"""
from __future__ import annotations

from datetime import datetime

from .db import DB, iso, now, parse

ISSUES = ("缺口", "松柄", "错放", "遗失", "超期", "复检驳回")
BLOCKED_STATUS = ("待修", "遗失", "磨刀中")


class DomainError(Exception):
    """业务规则冲突。"""


class Service:
    def __init__(self, db: DB):
        self.db = db

    # ---------- 班次 ----------
    def start_shift(self, name: str, ts: datetime | None = None) -> int:
        ts = ts or now()
        if self.db.open_shift():
            raise DomainError("已有进行中的班次，请先交接或关闭再开班")
        sid = self.db.start_shift(name, ts)
        return sid

    def handover(self, new_shift_name: str, actor: str, ts: datetime | None = None,
                 note: str = "") -> dict:
        """班次跨日交接：旧班结束，未归还的刀整批移交新班，逐刀留痕。"""
        ts = ts or now()
        old = self.db.open_shift()
        if not old:
            raise DomainError("当前没有进行中的班次，无法交接")
        outstanding = self.db.q(
            "SELECT * FROM knives WHERE current_user IS NOT NULL AND status='在用'"
        )
        self.db.execute(
            "UPDATE shifts SET end_ts=?, status='已交接' WHERE id=?",
            (iso(ts), old["id"]),
        )
        for k in outstanding:
            self.db.log(k["id"], "跨班交接", actor, ts, shift_id=old["id"],
                        note=f"{k['current_user']} 未归还，随班移交 → {new_shift_name} {note}".strip())
        new_id = self.db.start_shift(new_shift_name, ts)
        for k in outstanding:
            self.db.log(k["id"], "跨班接收", actor, ts, shift_id=new_id,
                        note=f"自班次 #{old['id']} 承接，领用人仍为 {k['current_user']}")
        return {"old_shift": old["id"], "new_shift": new_id,
                "transferred": [k["id"] for k in outstanding]}

    def close_shift(self, actor: str, ts: datetime | None = None) -> dict:
        """关闭班次：所有刀必须已在架/隔离，否则拒绝（防漏还）。"""
        ts = ts or now()
        sh = self.db.open_shift()
        if not sh:
            raise DomainError("没有进行中的班次")
        outstanding = self.db.q(
            "SELECT * FROM knives WHERE current_user IS NOT NULL AND status='在用'"
        )
        if outstanding:
            names = "、".join(f"{k['id']}({k['current_user']})" for k in outstanding)
            raise DomainError(f"仍有 {len(outstanding)} 把刀未归还：{names}，请先归还或交接")
        self.db.execute("UPDATE shifts SET end_ts=?, status='已关闭' WHERE id=?",
                        (iso(ts), sh["id"]))
        self.db.log(None, "关班核对", actor, ts, shift_id=sh["id"], note="逐件清洗核对完成")
        return {"shift": sh["id"]}

    # ---------- 领 / 还 ----------
    def checkout(self, knife_id: str, user: str, ts: datetime | None = None,
                 station_id: str | None = None) -> int:
        """班次开始领刀（扫码）。返回流转记录 id。"""
        ts = ts or now()
        k = self._must_knife(knife_id)
        sh = self.db.open_shift()
        if k["status"] == "遗失":
            raise DomainError(f"{knife_id} 已登记遗失，冻结发放；找回登记后方可领用")
        if k["status"] == "待修" or k["quarantined"]:
            raise DomainError(f"{knife_id} 状态为待修/隔离（{k['issue_type'] or '异常'}），不能发放")
        if k["status"] == "磨刀中":
            job = self.db.last_sharpen_job(knife_id)
            if job and job["status"] == "待复检":
                raise DomainError(f"{knife_id} 磨刀后尚未经他人复检，不能发放")
            raise DomainError(f"{knife_id} 正在磨刀流程中，不能发放")
        if k["current_user"]:
            raise DomainError(
                f"{knife_id} 已被 {k['current_user']} 领用，同一把刀不能同时被两人领用")
        if station_id and k["home_station_id"] != station_id:
            # 错放工位的刀从别的工位扫出：单独提示，不允许直接领用，先归位
            raise DomainError(
                f"{knife_id} 归属 {k['home_station_id']} 工位，当前在 {station_id}，"
                "属于错放工位，请先登记归位")
        if k["issue_type"] == "错放":
            raise DomainError(
                f"{knife_id} 属于错放工位（{k['issue_note'] or '位置不符'}），未归位前不能发放，"
                "请先在异常隔离页登记归位")
        self.db.execute(
            "UPDATE knives SET status='在用', current_user=? WHERE id=?", (user, knife_id))
        return self.db.log(knife_id, "领用", user, ts,
                           shift_id=sh["id"] if sh else None,
                           to_station=k["home_station_id"], to_slot=k["slot"])

    def checkin(self, knife_id: str, user: str, ts: datetime | None = None,
                washed: bool = True, note: str = "") -> int:
        """班次结束逐件清洗、核对归还（扫码）。"""
        ts = ts or now()
        k = self._must_knife(knife_id)
        sh = self.db.open_shift()
        if not k["current_user"]:
            raise DomainError(f"{knife_id} 当前不在领用状态，无法归还")
        if k["current_user"] != user:
            # 允许代还，但必须记录实际归还人并保留原领用人信息
            note = (note + " ").strip() + f"代还（原领用人 {k['current_user']}）"
        if not washed:
            note = (note + " ").strip() + "归还时未完成清洗"
        self.db.execute(
            "UPDATE knives SET status='在架', current_user=NULL WHERE id=?", (knife_id,))
        return self.db.log(knife_id, "归还", user, ts, shift_id=sh["id"] if sh else None,
                           to_station=k["home_station_id"], to_slot=k["slot"],
                           note=note or None)

    # ---------- 异常 ----------
    def report_issue(self, knife_id: str, issue_type: str, actor: str,
                     ts: datetime | None = None, note: str = "",
                     found_station: str | None = None) -> int:
        """登记异常：缺口/松柄→待修隔离；遗失→冻结；错放→标记待归位。"""
        ts = ts or now()
        if issue_type not in ISSUES:
            raise DomainError(f"未知异常类型 {issue_type}")
        k = self._must_knife(knife_id)
        sh = self.db.open_shift()
        if issue_type == "遗失":
            self.db.execute(
                "UPDATE knives SET status='遗失', current_user=NULL, issue_type='遗失', "
                "issue_note=?, issue_ts=?, quarantined=1 WHERE id=?",
                (note or "班次结束核对缺失", iso(ts), knife_id))
            return self.db.log(knife_id, "登记遗失", actor, ts,
                               shift_id=sh["id"] if sh else None, note=note or None)
        if issue_type == "错放":
            self.db.execute(
                "UPDATE knives SET issue_type='错放', issue_note=?, issue_ts=?, "
                "found_station_id=? WHERE id=?",
                (note or f"出现于 {found_station or '非归属工位'}", iso(ts),
                 found_station, knife_id))
            return self.db.log(knife_id, "错放登记", actor, ts,
                               shift_id=sh["id"] if sh else None,
                               to_station=found_station, note=note or None)
        # 缺口 / 松柄 / 复检驳回 → 待修隔离
        self.db.execute(
            "UPDATE knives SET status='待修', current_user=NULL, issue_type=?, issue_note=?, "
            "issue_ts=?, quarantined=1 WHERE id=?",
            (issue_type, note, iso(ts), knife_id))
        return self.db.log(knife_id, f"异常隔离·{issue_type}", actor, ts,
                           shift_id=sh["id"] if sh else None, note=note or None)

    def restore_to_rack(self, knife_id: str, actor: str, ts: datetime | None = None,
                        note: str = "", station_id: str | None = None) -> int:
        """异常解除：维修完成送回 / 遗失找回 / 错放归位。"""
        ts = ts or now()
        k = self._must_knife(knife_id)
        sh = self.db.open_shift()
        action = {"待修": "修毕归架", "遗失": "遗失找回", "在架": "错放归位"}.get(k["status"], "归位")
        if k["issue_type"] == "错放" and k["status"] == "在架":
            action = "错放归位"
        self.db.execute(
            "UPDATE knives SET status='在架', current_user=NULL, issue_type=NULL, "
            "issue_note=NULL, issue_ts=NULL, found_station_id=NULL, found_slot=NULL, "
            "quarantined=0 WHERE id=?",
            (knife_id,))
        return self.db.log(knife_id, action, actor, ts, shift_id=sh["id"] if sh else None,
                           to_station=station_id or k["home_station_id"], to_slot=k["slot"],
                           note=note or None)

    # ---------- 磨刀 / 复检 ----------
    def request_sharpen(self, knife_id: str, actor: str, ts: datetime | None = None,
                        note: str = "") -> int:
        ts = ts or now()
        k = self._must_knife(knife_id)
        sh = self.db.open_shift()
        if k["status"] == "遗失":
            raise DomainError(f"{knife_id} 已遗失，不能排磨刀")
        if k["status"] == "在用":
            raise DomainError(f"{knife_id} 仍由 {k['current_user']} 领用，请先归还再送磨")
        job = self.db.last_sharpen_job(knife_id)
        if job and job["status"] in ("待磨刀", "待复检"):
            raise DomainError(f"{knife_id} 已有进行中的磨刀单（{job['status']}）")
        cur = self.db.execute(
            """INSERT INTO sharpen_jobs(knife_id,shift_id,request_ts,requested_by,status,note)
               VALUES(?,?,?,?,'待磨刀',?)""",
            (knife_id, sh["id"] if sh else None, iso(ts), actor, note or None))
        self.db.execute(
            "UPDATE knives SET status='磨刀中', current_user=NULL, issue_type=NULL, "
            "issue_note=NULL, quarantined=0 WHERE id=?",
            (knife_id,))
        self.db.log(knife_id, "送磨", actor, ts, shift_id=sh["id"] if sh else None,
                    note=note or None)
        return cur.lastrowid

    def complete_sharpen(self, job_id: int, actor: str, edge_state: str = "锋利",
                         ts: datetime | None = None, photo_path: str | None = None,
                         note: str = "") -> int:
        """磨刀完成 → 待复检（必须由另一人复检）。"""
        ts = ts or now()
        job = self._must_job(job_id)
        if job["status"] != "待磨刀":
            raise DomainError(f"磨刀单 #{job_id} 状态为 {job['status']}，无法登记磨刀完成")
        self.db.execute(
            "UPDATE sharpen_jobs SET sharpened_ts=?, sharpened_by=?, edge_state=?, "
            "photo_path=?, status='待复检' WHERE id=?",
            (iso(ts), actor, edge_state, photo_path, job_id))
        self.db.log(job["knife_id"], "磨刀完成", actor, ts, note=f"刃口：{edge_state}")
        return job_id

    def check_sharpen(self, job_id: int, inspector: str, result: str,
                      ts: datetime | None = None, note: str = "") -> int:
        """复检：通过则归架并刷新磨刀周期；驳回则隔离待修。

        复检人不得是磨刀人本人（磨刀者不能批准自己的复检）。
        """
        ts = ts or now()
        if result not in ("通过", "驳回"):
            raise DomainError("复检结果只能是 通过/驳回")
        job = self._must_job(job_id)
        if job["status"] != "待复检":
            raise DomainError(f"磨刀单 #{job_id} 状态为 {job['status']}，无需复检")
        if not job["sharpened_by"]:
            raise DomainError("该磨刀单尚未完成磨刀")
        if inspector == job["sharpened_by"]:
            raise DomainError(
                f"复检被拒绝：{inspector} 是该刀的磨刀人，磨刀者不能复检自己的工作，"
                "请指定另一位同事复检")
        self.db.execute(
            "UPDATE sharpen_jobs SET check_ts=?, checked_by=?, check_result=?, check_note=?, "
            "status=? WHERE id=?",
            (iso(ts), inspector, result, note or None, result, job_id))
        kid = job["knife_id"]
        if result == "通过":
            self.db.execute(
                "UPDATE knives SET status='在架', current_user=NULL, issue_type=NULL, "
                "issue_note=NULL, issue_ts=NULL, found_station_id=NULL, found_slot=NULL, "
                "quarantined=0, last_sharpen_ts=? WHERE id=?",
                (iso(ts), kid))
            self.db.log(kid, "复检通过", inspector, ts,
                        note=f"锋利度与刀身完整性合格 {note}".strip())
        else:
            self.db.execute(
                "UPDATE knives SET status='待修', current_user=NULL, issue_type='复检驳回', "
                "issue_note=?, issue_ts=?, quarantined=1 WHERE id=?",
                (note or "复检未过", iso(ts), kid))
            self.db.log(kid, "复检驳回", inspector, ts, note=note or None)
        return job_id

    # ---------- 查询 ----------
    def last_handler(self, knife_id: str) -> str | None:
        m = self.db.last_movement(knife_id)
        return m["actor"] if m else None

    def timeline(self, knife_id: str) -> list[dict]:
        rows = self.db.q(
            "SELECT m.*, s.name AS shift_name FROM movements m "
            "LEFT JOIN shifts s ON s.id=m.shift_id WHERE m.knife_id=? ORDER BY m.id",
            (knife_id,))
        return [dict(r) for r in rows]

    # ---------- 内部 ----------
    def _must_knife(self, knife_id: str):
        k = self.db.knife(knife_id)
        if not k:
            raise DomainError(f"刀具编号 {knife_id} 不存在")
        return k

    def _must_job(self, job_id: int):
        j = self.db.one("SELECT * FROM sharpen_jobs WHERE id=?", (job_id,))
        if not j:
            raise DomainError(f"磨刀单 #{job_id} 不存在")
        return j
