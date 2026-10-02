"""领域服务：领还、磨刀、复检、异常隔离、遗失、跨班交接的全部规则。

关键约束：
- 待修/遗失/隔离中的刀不能发放；磨刀超期、有刃口缺口的刀不能发放。
- 同一把刀同一时刻只能被一人领用（条件 UPDATE 原子占位）。
- 磨刀者不能批准自己的复检；维修/找回的复检也必须由另一个人完成。
- 每一步都写 events，并刷新 last_touched_by / last_touched_at（最后接触人可追溯）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# ---------- 常量 ----------

KNIFE_TYPES = {
    "chef":    "主厨刀",
    "slicer":  "片刀",
    "cleaver": "砍刀",
    "santoku": "三德刀",
    "paring":  "果皮刀",
    "boning":  "剔骨刀",
}

STATUS_LABELS = {
    "in_rack": "在架",
    "issued": "领用中",
    "sharpening": "磨刀/待复检",
    "quarantine": "异常隔离",
    "repair": "待修",
    "lost": "遗失",
}

EDGE_LABELS = {"sharp": "锋利", "acceptable": "可用", "dull": "钝", "chipped": "缺口"}

REASON_LABELS = {
    "chipped": "刃口缺口",
    "loose_handle": "刀柄松动",
    "misplaced": "错放工位",
    "found_pending": "找回待检",
    "repair_pending": "维修待检",
    "rejected": "复检未过",
}

EVENT_LABELS = {
    "shift_start": "开班",
    "shift_close": "收班",
    "issue": "领刀",
    "return_ok": "清洗核对归还",
    "return_wrong_slot": "归还至错误槽位",
    "report_chipped": "上报刃口缺口",
    "report_loose": "上报刀柄松动",
    "send_sharpen": "送磨",
    "sharpen_done": "磨刀完成待复检",
    "reinspect_pass": "复检通过",
    "reinspect_fail": "复检未过",
    "send_repair": "送修",
    "repair_done": "维修完成待检",
    "repair_inspect_pass": "维修复检通过",
    "repair_inspect_fail": "维修复检未过",
    "report_lost": "上报遗失",
    "found": "找回登记",
    "found_inspect_pass": "找回复检通过",
    "found_inspect_fail": "找回复检未过",
    "relocate": "核对归位",
    "handover": "跨班交接",
    "photo": "刃口拍照",
}


class KnifeError(Exception):
    """业务规则冲突。code 供界面区分处理入口。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------- 服务 ----------

class KnifeService:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], datetime] = utcnow):
        self.conn = conn
        self.clock = clock

    # ===== 基础 =====

    def _now(self) -> datetime:
        return self.clock()

    def _staff(self, staff_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
        if not row:
            raise KnifeError("bad_staff", f"未知工号 {staff_id}")
        if not row["active"]:
            raise KnifeError("inactive", f"{row['name']} 已停用")
        return row

    def _knife(self, knife_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM knives WHERE id=?", (knife_id,)).fetchone()
        if not row:
            raise KnifeError("bad_knife", f"未知刀具 {knife_id}")
        return row

    def _slot(self, slot_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
        if not row:
            raise KnifeError("bad_slot", f"未知槽位 {slot_id}")
        return row

    def _touch(self, knife_id: str, actor_id: str | None) -> None:
        self.conn.execute(
            "UPDATE knives SET last_touched_by=?, last_touched_at=? WHERE id=?",
            (actor_id, iso(self._now()), knife_id),
        )

    def _event(self, knife_id: str, actor_id: str | None, etype: str,
               shift_id: int | None = None, detail: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO events(knife_id, ts, actor_id, type, shift_id, detail) VALUES (?,?,?,?,?,?)",
            (knife_id, iso(self._now()), actor_id, etype, shift_id,
             json.dumps(detail or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    # ===== 班次（含跨日单独处理）=====

    def start_shift(self, name: str, leader_id: str, started_at: datetime | None = None) -> int:
        self._staff(leader_id)
        ts = iso(started_at or self._now())
        cur = self.conn.execute(
            "INSERT INTO shifts(name, leader_id, started_at) VALUES (?,?,?)",
            (name, leader_id, ts))
        sid = int(cur.lastrowid)
        self.conn.commit()
        return sid

    def open_shift(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM shifts WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()

    def outstanding_for_shift(self, shift_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM knives WHERE status='issued' AND issued_shift_id=? ORDER BY id",
            (shift_id,)).fetchall())

    def close_shift(self, shift_id: int) -> dict[str, Any]:
        """收班：逐件核对。仍有领用未还/未交接的刀时拒绝收班；跨午夜单独标记。"""
        shift = self.conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise KnifeError("bad_shift", "班次不存在")
        if shift["ended_at"]:
            raise KnifeError("already_closed", "该班次已收班")
        outstanding = self.outstanding_for_shift(shift_id)
        if outstanding:
            raise KnifeError(
                "outstanding",
                f"还有 {len(outstanding)} 把刀未归还或交接："
                + "、".join(k["id"] for k in outstanding),
            )
        started = datetime.fromisoformat(shift["started_at"])
        now = self._now()
        cross_day = started.astimezone(timezone.utc).date() != now.date()
        note = "cross_day: 班次跨午夜" if cross_day else ""
        self.conn.execute("UPDATE shifts SET ended_at=?, note=? WHERE id=?",
                          (iso(now), note, shift_id))
        self.conn.commit()
        return {"cross_day": cross_day, "outstanding": 0}

    def handover(self, knife_id: str, to_chef_id: str, to_shift_id: int,
                 actor_id: str) -> dict[str, Any]:
        """跨班交接：from 厨师的刀转给 to 厨师，全程保持 issued，持有人唯一。"""
        knife = self._knife(knife_id)
        if knife["status"] != "issued":
            raise KnifeError("not_issued", f"{knife_id} 当前不是领用状态，无法交接")
        to = self._staff(to_chef_id)
        if to["role"] != "chef":
            raise KnifeError("not_chef", "接收人必须是厨师")
        to_shift = self.conn.execute("SELECT * FROM shifts WHERE id=?", (to_shift_id,)).fetchone()
        if not to_shift:
            raise KnifeError("bad_shift", "目标班次不存在")
        from_id = knife["current_holder_id"]
        from_shift = knife["issued_shift_id"]
        if actor_id != from_id and self._staff(actor_id)["role"] != "manager":
            raise KnifeError("forbidden", "只有持用人本人或值班经理可以交接")
        if to_shift["ended_at"]:
            raise KnifeError("shift_closed", "目标班次已收班，不能交接进去")
        if to_chef_id == from_id:
            raise KnifeError("same_person", "交接双方不能是同一人")
        self.conn.execute(
            "UPDATE knives SET current_holder_id=?, issued_shift_id=? WHERE id=?",
            (to_chef_id, to_shift_id, knife_id))
        self._touch(knife_id, actor_id)
        self._event(knife_id, actor_id, "handover", to_shift_id, {
            "from_chef": from_id, "to_chef": to_chef_id,
            "from_shift": from_shift, "to_shift": to_shift_id,
            "cross_day": from_shift != to_shift_id,
        })
        self.conn.commit()
        return {"from": from_id, "to": to_chef_id}

    # ===== 领刀（独占领用 + 发放前闸门）=====

    def issue(self, knife_id: str, chef_id: str, shift_id: int) -> dict[str, Any]:
        chef = self._staff(chef_id)
        if chef["role"] != "chef":
            raise KnifeError("not_chef", "只有厨师可以领刀")
        shift = self.conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise KnifeError("bad_shift", "班次不存在")
        if shift["ended_at"]:
            raise KnifeError("shift_closed", "该班次已收班，不能领刀")

        knife = self._knife(knife_id)
        # 先给出精确的拒绝原因（界面据此走单独处理流程）
        if knife["status"] == "lost":
            raise KnifeError("frozen_lost", f"{knife_id} 已报遗失，冻结发放")
        if knife["status"] == "repair":
            raise KnifeError("frozen_repair", f"{knife_id} 处于待修，不能发放")
        if knife["status"] == "quarantine":
            reason = REASON_LABELS.get(knife["quarantine_reason"] or "", "异常隔离")
            raise KnifeError("frozen_quarantine", f"{knife_id} 正在隔离（{reason}），不能发放")
        if knife["edge_state"] == "chipped":
            raise KnifeError("frozen_chipped", f"{knife_id} 刃口有缺口，必须先处理")
        if knife["pending_reinspect"]:
            raise KnifeError("pending_reinspect", f"{knife_id} 磨刀后尚未复检，不能发放")
        due = knife["next_sharpen_due"]
        if due and datetime.fromisoformat(due) < self._now():
            raise KnifeError("overdue_sharpen",
                             f"{knife_id} 磨刀超期（应于 {due[:10]} 前磨刀），请先送磨")

        # 原子占位：只有 in_rack 的刀能被更新成功 → 并发下只有一人领到
        cur = self.conn.execute(
            """UPDATE knives
               SET status='issued', current_holder_id=?, issued_at=?, issued_shift_id=?,
                   last_touched_by=?, last_touched_at=?
               WHERE id=? AND status='in_rack'""",
            (chef_id, iso(self._now()), shift_id, chef_id, iso(self._now()), knife_id))
        if cur.rowcount == 0:
            holder = self._knife(knife_id)["current_holder_id"]
            raise KnifeError("already_issued",
                            f"{knife_id} 已被 {holder} 领用，同一把刀不能同时领用")
        self._event(knife_id, chef_id, "issue", shift_id)
        self.conn.commit()
        return {"knife": knife_id, "holder": chef_id}

    # ===== 归还（清洗、逐件核对、分流异常）=====

    def return_knife(self, knife_id: str, actor_id: str, slot_id: str | None,
                     condition: str = "clean") -> dict[str, Any]:
        knife = self._knife(knife_id)
        if knife["status"] != "issued":
            raise KnifeError("not_issued", f"{knife_id} 不是领用中状态")
        actor = self._staff(actor_id)
        if actor["role"] not in ("chef", "manager"):
            raise KnifeError("forbidden", "该角色不能归还刀具")
        if actor["role"] == "chef" and actor_id != knife["current_holder_id"]:
            raise KnifeError("not_holder", "只能归还自己领用的刀（经理可代收）")
        if condition not in ("clean", "chipped", "loose", "dull"):
            raise KnifeError("bad_condition", "未知的归还状态")

        shift_id = knife["issued_shift_id"]
        holder = knife["current_holder_id"]

        if condition == "chipped":
            self._quarantine(knife_id, actor_id, "chipped", "report_chipped", shift_id,
                             edge="chipped", clear_holder=True)
            return {"result": "quarantine", "reason": "chipped"}

        if condition == "loose":
            self._quarantine(knife_id, actor_id, "loose_handle", "report_loose", shift_id,
                             clear_holder=True)
            return {"result": "quarantine", "reason": "loose_handle"}

        if condition == "dull":
            # 钝刀直接进磨刀流程，不回架
            self.conn.execute(
                """UPDATE knives SET status='sharpening', current_holder_id=NULL,
                   issued_at=NULL, issued_shift_id=NULL,
                   last_touched_by=?, last_touched_at=? WHERE id=?""",
                (actor_id, iso(self._now()), knife_id))
            self._touch(knife_id, actor_id)
            self._event(knife_id, actor_id, "send_sharpen", shift_id,
                        {"from": "return", "last_holder": holder, "edge": knife["edge_state"]})
            self.conn.commit()
            return {"result": "sharpening"}

        # clean：必须扫入槽位逐件核对
        if not slot_id:
            raise KnifeError("slot_required", "归还必须扫码入槽位核对")
        slot = self._slot(slot_id)
        if slot_id != knife["home_slot_id"]:
            # 错放工位：进异常隔离，等待核对归位
            self._quarantine(knife_id, actor_id, "misplaced", "return_wrong_slot", shift_id,
                             clear_holder=True,
                             extra={"scanned_slot": slot_id, "home_slot": knife["home_slot_id"]})
            return {"result": "quarantine", "reason": "misplaced", "slot": slot_id}

        self.conn.execute(
            """UPDATE knives SET status='in_rack', current_holder_id=NULL, issued_at=NULL,
               issued_shift_id=NULL, last_touched_by=?, last_touched_at=? WHERE id=?""",
            (actor_id, iso(self._now()), knife_id))
        self._event(knife_id, actor_id, "return_ok", shift_id, {"slot": slot_id})
        self.conn.commit()
        return {"result": "in_rack", "slot": slot_id}

    def _quarantine(self, knife_id, actor_id, reason, event_type, shift_id,
                    edge: str | None = None, clear_holder: bool = False,
                    extra: dict | None = None) -> int:
        sets = ["status='quarantine'", "quarantine_reason=?", "last_touched_by=?",
                "last_touched_at=?"]
        params: list[Any] = [reason, actor_id, iso(self._now())]
        if edge:
            sets.append("edge_state=?")
            params.append(edge)
        if clear_holder:
            sets += ["current_holder_id=NULL", "issued_at=NULL", "issued_shift_id=NULL"]
        params.append(knife_id)
        self.conn.execute(f"UPDATE knives SET {', '.join(sets)} WHERE id=?", params)
        detail = {"reason": reason}
        detail.update(extra or {})
        eid = self._event(knife_id, actor_id, event_type, shift_id, detail)
        self.conn.commit()
        return eid

    # ===== 保养排程与磨刀复检 =====

    def sharpen_due_list(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM knives WHERE status IN ('in_rack','sharpening') ORDER BY next_sharpen_due IS NULL, next_sharpen_due"
        ).fetchall()
        return [r for r in rows if r["next_sharpen_due"]]

    def send_sharpen(self, knife_id: str, actor_id: str) -> dict[str, Any]:
        self._staff(actor_id)
        knife = self._knife(knife_id)
        if knife["status"] not in ("in_rack", "sharpening"):
            raise KnifeError("cannot_sharpen", "只有在架或已在磨刀流程中的刀可以送磨")
        self.conn.execute(
            "UPDATE knives SET status='sharpening', quarantine_reason=NULL, "
            "last_touched_by=?, last_touched_at=? WHERE id=?",
            (actor_id, iso(self._now()), knife_id))
        self._event(knife_id, actor_id, "send_sharpen", None, {"from": "schedule"})
        self.conn.commit()
        return {"result": "sharpening"}

    def sharpen_done(self, knife_id: str, sharpener_id: str,
                     photo_path: str | None = None) -> dict[str, Any]:
        sharpener = self._staff(sharpener_id)
        if sharpener["role"] not in ("sharpener", "manager"):
            raise KnifeError("not_sharpener", "只有磨刀岗可以登记磨刀完成")
        knife = self._knife(knife_id)
        if knife["status"] != "sharpening":
            raise KnifeError("not_sharpening", f"{knife_id} 不在磨刀流程中")
        now = self._now()
        self.conn.execute(
            """UPDATE knives SET sharpen_done_at=?, sharpened_by=?, pending_reinspect=1,
               edge_state='sharp', last_touched_by=?, last_touched_at=? WHERE id=?""",
            (iso(now), sharpener_id, sharpener_id, iso(now), knife_id))
        eid = self._event(knife_id, sharpener_id, "sharpen_done", None, {"photo": photo_path})
        if photo_path:
            self.conn.execute(
                "INSERT INTO photos(knife_id, event_id, path, label, taken_by, taken_at)"
                " VALUES (?,?,?,?,?,?)",
                (knife_id, eid, photo_path, "edge", sharpener_id, iso(now)))
        self.conn.commit()
        return {"event_id": eid, "awaiting": "reinspect_by_other"}

    def reinspect(self, knife_id: str, inspector_id: str, passed: bool,
                  photo_path: str | None = None, note: str = "") -> dict[str, Any]:
        """磨刀复检：检查锋利度与刀身完整性；复检人不得是磨刀者本人。"""
        inspector = self._staff(inspector_id)
        knife = self._knife(knife_id)
        if knife["status"] != "sharpening" or not knife["pending_reinspect"]:
            raise KnifeError("no_pending", f"{knife_id} 没有待复检的磨刀记录")
        if knife["sharpened_by"] == inspector_id:
            raise KnifeError("self_reinspect",
                             "磨刀者不能批准自己的复检，必须由另一个人检查锋利度与刀身完整性")
        if inspector["role"] not in ("inspector", "manager"):
            raise KnifeError("not_inspector", "只有复检岗或经理可以复检")
        now = self._now()
        if passed:
            next_due = now + timedelta(days=knife["sharpen_interval_days"])
            self.conn.execute(
                """UPDATE knives SET status='in_rack', pending_reinspect=0,
                   quarantine_reason=NULL, last_sharpen_at=?, next_sharpen_due=?,
                   sharpened_by=NULL, sharpen_done_at=NULL, edge_state='sharp',
                   last_touched_by=?, last_touched_at=? WHERE id=?""",
                (iso(now), iso(next_due), inspector_id, iso(now), knife_id))
            etype = "reinspect_pass"
        else:
            self.conn.execute(
                """UPDATE knives SET status='quarantine', quarantine_reason='rejected',
                   pending_reinspect=0, sharpened_by=NULL, sharpen_done_at=NULL,
                   edge_state='acceptable', last_touched_by=?, last_touched_at=? WHERE id=?""",
                (inspector_id, iso(now), knife_id))
            etype = "reinspect_fail"
        eid = self._event(knife_id, inspector_id, etype, None,
                          {"photo": photo_path, "note": note,
                           "sharpened_by": knife["sharpened_by"]})
        if photo_path:
            self.conn.execute(
                "INSERT INTO photos(knife_id, event_id, path, label, taken_by, taken_at)"
                " VALUES (?,?,?,?,?,?)",
                (knife_id, eid, photo_path, "edge", inspector_id, iso(now)))
        self.conn.commit()
        return {"result": "in_rack" if passed else "quarantine"}

    # ===== 维修（缺口/松动）=====

    def send_repair(self, knife_id: str, actor_id: str) -> dict[str, Any]:
        self._staff(actor_id)
        knife = self._knife(knife_id)
        if knife["status"] != "quarantine":
            raise KnifeError("not_quarantined", "只有隔离中的刀可以送修")
        self.conn.execute(
            "UPDATE knives SET status='repair', quarantine_reason=NULL, "
            "last_touched_by=?, last_touched_at=? WHERE id=?",
            (actor_id, iso(self._now()), knife_id))
        self._event(knife_id, actor_id, "send_repair", None)
        self.conn.commit()
        return {"result": "repair"}

    def repair_done(self, knife_id: str, repairer_id: str) -> dict[str, Any]:
        repairer = self._staff(repairer_id)
        if repairer["role"] not in ("sharpener", "manager"):
            raise KnifeError("not_repairer", "只有维修岗可以登记维修完成")
        knife = self._knife(knife_id)
        if knife["status"] != "repair":
            raise KnifeError("not_repair", f"{knife_id} 不在待修状态")
        self.conn.execute(
            "UPDATE knives SET status='quarantine', quarantine_reason='repair_pending', "
            "edge_state=CASE WHEN edge_state='chipped' THEN 'acceptable' ELSE edge_state END, "
            "last_touched_by=?, last_touched_at=? WHERE id=?",
            (repairer_id, iso(self._now()), knife_id))
        self._event(knife_id, repairer_id, "repair_done", None, {"repairer": repairer_id})
        self.conn.commit()
        return {"result": "quarantine", "reason": "repair_pending"}

    def reinspect_repair(self, knife_id: str, inspector_id: str, passed: bool) -> dict[str, Any]:
        inspector = self._staff(inspector_id)
        knife = self._knife(knife_id)
        if knife["status"] != "quarantine" or knife["quarantine_reason"] != "repair_pending":
            raise KnifeError("no_pending", f"{knife_id} 没有待检的维修")
        last = self.conn.execute(
            "SELECT actor_id FROM events WHERE knife_id=? AND type='repair_done' "
            "ORDER BY id DESC LIMIT 1", (knife_id,)).fetchone()
        if last and last["actor_id"] == inspector_id:
            raise KnifeError("self_reinspect", "维修者不能批准自己的复检，必须换人")
        if inspector["role"] not in ("inspector", "manager"):
            raise KnifeError("not_inspector", "只有复检岗或经理可以复检")
        if passed:
            self.conn.execute(
                "UPDATE knives SET status='in_rack', quarantine_reason=NULL, edge_state='sharp', "
                "last_touched_by=?, last_touched_at=? WHERE id=?",
                (inspector_id, iso(self._now()), knife_id))
            etype = "repair_inspect_pass"
        else:
            self.conn.execute(
                "UPDATE knives SET status='repair', quarantine_reason=NULL, "
                "last_touched_by=?, last_touched_at=? WHERE id=?",
                (inspector_id, iso(self._now()), knife_id))
            etype = "repair_inspect_fail"
        self._event(knife_id, inspector_id, etype, None, {"repairer": last["actor_id"] if last else None})
        self.conn.commit()
        return {"result": "in_rack" if passed else "repair"}

    # ===== 遗失冻结 / 找回复检 =====

    def report_lost(self, knife_id: str, actor_id: str, note: str = "") -> dict[str, Any]:
        knife = self._knife(knife_id)
        if knife["status"] == "lost":
            raise KnifeError("already_lost", f"{knife_id} 已处于遗失状态")
        self._staff(actor_id)
        self.conn.execute(
            "UPDATE knives SET status='lost', current_holder_id=NULL, issued_at=NULL, "
            "issued_shift_id=NULL, last_touched_by=?, last_touched_at=?, notes=? WHERE id=?",
            (actor_id, iso(self._now()), note, knife_id))
        self._event(knife_id, actor_id, "report_lost", None,
                    {"last_holder": knife["current_holder_id"],
                     "last_shift": knife["issued_shift_id"], "note": note})
        self.conn.commit()
        return {"result": "lost"}

    def report_found(self, knife_id: str, finder_id: str, slot_id: str | None = None) -> dict[str, Any]:
        """找回的刀一律先进异常隔离，复检完整性后才能回架。"""
        self._staff(finder_id)
        knife = self._knife(knife_id)
        if knife["status"] != "lost":
            raise KnifeError("not_lost", f"{knife_id} 不是遗失状态")
        self.conn.execute(
            "UPDATE knives SET status='quarantine', quarantine_reason='found_pending', "
            "found_at=?, last_touched_by=?, last_touched_at=? WHERE id=?",
            (iso(self._now()), finder_id, iso(self._now()), knife_id))
        self._event(knife_id, finder_id, "found", None, {"slot": slot_id})
        self.conn.commit()
        return {"result": "quarantine", "reason": "found_pending"}

    def reinspect_found(self, knife_id: str, inspector_id: str, passed: bool,
                        edge: str = "sharp") -> dict[str, Any]:
        inspector = self._staff(inspector_id)
        knife = self._knife(knife_id)
        if knife["status"] != "quarantine" or knife["quarantine_reason"] != "found_pending":
            raise KnifeError("no_pending", f"{knife_id} 没有待检的找回登记")
        found = self.conn.execute(
            "SELECT actor_id FROM events WHERE knife_id=? AND type='found' ORDER BY id DESC LIMIT 1",
            (knife_id,)).fetchone()
        if found and found["actor_id"] == inspector_id:
            raise KnifeError("self_reinspect", "找回登记人不能复检自己找回的刀，必须换人")
        if inspector["role"] not in ("inspector", "manager"):
            raise KnifeError("not_inspector", "只有复检岗或经理可以复检")
        if passed:
            self.conn.execute(
                "UPDATE knives SET status='in_rack', quarantine_reason=NULL, found_at=NULL, "
                "edge_state=?, last_touched_by=?, last_touched_at=? WHERE id=?",
                (edge, inspector_id, iso(self._now()), knife_id))
            etype = "found_inspect_pass"
        else:
            self.conn.execute(
                "UPDATE knives SET status='repair', quarantine_reason=NULL, "
                "last_touched_by=?, last_touched_at=? WHERE id=?",
                (inspector_id, iso(self._now()), knife_id))
            etype = "found_inspect_fail"
        self._event(knife_id, inspector_id, etype, None, {"edge": edge})
        self.conn.commit()
        return {"result": "in_rack" if passed else "repair"}

    # ===== 错放工位：核对归位 =====

    def relocate_home(self, knife_id: str, actor_id: str, slot_id: str) -> dict[str, Any]:
        self._staff(actor_id)
        knife = self._knife(knife_id)
        if knife["status"] != "quarantine" or knife["quarantine_reason"] != "misplaced":
            raise KnifeError("not_misplaced", f"{knife_id} 不是错放隔离状态")
        if slot_id != knife["home_slot_id"]:
            raise KnifeError("wrong_home", f"归位槽位必须是 {knife['home_slot_id']}")
        self.conn.execute(
            "UPDATE knives SET status='in_rack', quarantine_reason=NULL, current_holder_id=NULL, "
            "issued_at=NULL, issued_shift_id=NULL, last_touched_by=?, last_touched_at=? WHERE id=?",
            (actor_id, iso(self._now()), knife_id))
        self._event(knife_id, actor_id, "relocate", None, {"slot": slot_id})
        self.conn.commit()
        return {"result": "in_rack", "slot": slot_id}

    # ===== 查询 =====

    def rack_view(self) -> list[dict[str, Any]]:
        stations = self.conn.execute("SELECT * FROM stations ORDER BY zone, id").fetchall()
        out = []
        for st in stations:
            slots = self.conn.execute(
                "SELECT * FROM slots WHERE station_id=? ORDER BY rack, slot_row, slot_col",
                (st["id"],)).fetchall()
            slot_data = []
            for s in slots:
                k = self.conn.execute("SELECT * FROM knives WHERE home_slot_id=?", (s["id"],)).fetchone()
                slot_data.append({"slot": dict(s), "knife": dict(k) if k else None})
            out.append({"station": dict(st), "slots": slot_data})
        return out

    def timeline(self, knife_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT e.*, s.name AS actor_name FROM events e
               LEFT JOIN staff s ON s.id=e.actor_id
               WHERE e.knife_id=? ORDER BY e.id""", (knife_id,)).fetchall())

    def knife_detail(self, knife_id: str) -> dict[str, Any]:
        knife = dict(self._knife(knife_id))
        knife["events"] = [dict(e) for e in self.timeline(knife_id)]
        knife["photos"] = [dict(p) for p in self.conn.execute(
            "SELECT * FROM photos WHERE knife_id=? ORDER BY id", (knife_id,)).fetchall()]
        return knife

    def last_contact(self, knife_id: str) -> dict[str, Any] | None:
        k = self._knife(knife_id)
        if not k["last_touched_by"]:
            return None
        s = self.conn.execute("SELECT * FROM staff WHERE id=?", (k["last_touched_by"],)).fetchone()
        return {"staff_id": k["last_touched_by"], "name": s["name"] if s else k["last_touched_by"],
                "at": k["last_touched_at"]}
