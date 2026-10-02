"""领域规则测试：独占领用、跨班交接、复检权限、遗失冻结，外加异常分流与提醒。"""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from knifectl.db import connect, init_db
from knifectl.reminders import ReminderEngine
from knifectl.service import (REASON_LABELS, KnifeError, KnifeService, iso)
from knifectl.scanner import MockScanner


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.conn = connect(self.base / "t.db")
        init_db(self.conn)
        self._bootstrap()
        self.now = self._fixed_now()
        self.clock = Clock(self.now)
        self.svc = KnifeService(self.conn, clock=self.clock)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _fixed_now(self):
        from knifectl.service import utcnow
        return utcnow()

    def _bootstrap(self):
        c = self.conn
        for sid, name, role in [
                ("E01", "张厨", "chef"), ("E02", "林厨", "chef"), ("E03", "赵厨", "chef"),
                ("S01", "莫磨刀", "sharpener"), ("S02", "钱复检", "inspector"),
                ("M01", "冯班", "manager")]:
            c.execute("INSERT INTO staff(id,name,role) VALUES (?,?,?)", (sid, name, role))
        c.execute("INSERT INTO stations(id,name,zone) VALUES ('st','测试工位','热厨')")
        for i in range(1, 5):
            c.execute("INSERT INTO slots(id,station_id,rack,slot_row,slot_col) VALUES (?,?,?,?,?)",
                      (f"A-{i:02d}", "st", "R1", 1, i))
        for i in range(1, 5):
            c.execute(
                "INSERT INTO knives(id,type,home_slot_id,sharpen_interval_days) VALUES (?,?,?,?)",
                (f"K-{i:03d}", "chef", f"A-{i:02d}", 7))
        c.commit()

    def fresh_svc(self):
        conn = connect(self.base / "t.db")
        return conn, KnifeService(conn, clock=self.clock)


class TestExclusiveIssue(BaseCase):
    """同一把刀不能同时被两名厨师领用 —— 并发下也只能有一人成功。"""

    def test_concurrent_issue_exactly_one_wins(self):
        shift = self.svc.start_shift("早班", "M01")
        results: list[str] = []
        errors: list[str] = []
        barrier = threading.Barrier(8)

        def worker(idx):
            conn, svc = self.fresh_svc()
            barrier.wait()  # 尽量让 8 个 UPDATE 在同一刻撞上去
            try:
                svc.issue("K-001", f"E0{idx % 3 + 1}", shift)
                results.append(f"E0{idx % 3 + 1}")
            except KnifeError as e:
                errors.append(e.code)
            conn.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, f"应有且仅有 1 人领到，实际：{results}")
        self.assertEqual(len(errors), 7)
        self.assertTrue(all(code == "already_issued" for code in errors), errors)
        row = self.conn.execute(
            "SELECT current_holder_id, status FROM knives WHERE id='K-001'").fetchone()
        self.assertEqual(row["status"], "issued")
        self.assertEqual(row["current_holder_id"], results[0])

    def test_second_issue_blocked_while_out(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-001", "E02", s)
        self.assertEqual(cm.exception.code, "already_issued")

    def test_frozen_states_cannot_issue(self):
        s = self.svc.start_shift("早班", "M01")
        # 遗失
        self.svc.issue("K-001", "E01", s)
        self.svc.report_lost("K-001", "E01")
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-001", "E02", s)
        self.assertEqual(cm.exception.code, "frozen_lost")
        # 待修
        self.svc.issue("K-002", "E02", s)
        self.svc.return_knife("K-002", "E02", None, "loose")
        self.svc.send_repair("K-002", "M01")
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-002", "E02", s)
        self.assertEqual(cm.exception.code, "frozen_repair")
        # 磨刀超期
        self.conn.execute("UPDATE knives SET next_sharpen_due=? WHERE id='K-003'",
                          (iso(self.now - timedelta(days=1)),))
        self.conn.commit()
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-003", "E03", s)
        self.assertEqual(cm.exception.code, "overdue_sharpen")

    def test_non_chef_cannot_issue_and_closed_shift_blocked(self):
        s = self.svc.start_shift("早班", "M01")
        with self.assertRaises(KnifeError):
            self.svc.issue("K-001", "S01", s)
        self.svc.close_shift(s)
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-001", "E01", s)
        self.assertEqual(cm.exception.code, "shift_closed")


class TestShiftHandover(BaseCase):
    """班次跨日与跨班交接。"""

    def test_cross_day_shift_marked_and_outstanding_blocks_close(self):
        # 昨天开班、领刀；时钟走到今天 → 跨日，且有未还刀 → 拒绝收班
        self.clock.t = self.now - timedelta(days=1, hours=2)
        night = self.svc.start_shift("夜班", "M01", started_at=self.clock.t)
        self.svc.issue("K-001", "E03", night)
        self.clock.t = self.now
        with self.assertRaises(KnifeError) as cm:
            self.svc.close_shift(night)
        self.assertEqual(cm.exception.code, "outstanding")
        # 归还后收班成功，并标记跨午夜
        self.svc.return_knife("K-001", "E03", "A-01", "clean")
        info = self.svc.close_shift(night)
        self.assertTrue(info["cross_day"])
        shift = self.conn.execute("SELECT * FROM shifts WHERE id=?", (night,)).fetchone()
        self.assertIn("cross_day", shift["note"])

    def test_handover_between_shifts_then_both_close(self):
        # 昨日夜班
        self.clock.t = self.now - timedelta(days=1, hours=3)
        night = self.svc.start_shift("夜班", "M01", started_at=self.clock.t)
        self.svc.issue("K-001", "E03", night)
        # 今日早班开班并交接
        self.clock.t = self.now
        morning = self.svc.start_shift("早班", "M01", started_at=self.clock.t)
        r = self.svc.handover("K-001", "E01", morning, "E03")
        self.assertEqual(r, {"from": "E03", "to": "E01"})

        # 夜班已无占用，可以收班（跨日标记）
        info = self.svc.close_shift(night)
        self.assertTrue(info["cross_day"])
        # 刀仍在 E01 手里且只属于新班次
        k = self.conn.execute("SELECT * FROM knives WHERE id='K-001'").fetchone()
        self.assertEqual(k["current_holder_id"], "E01")
        self.assertEqual(k["issued_shift_id"], morning)
        # 交接事件记录了双方与跨班
        ev = self.conn.execute(
            "SELECT * FROM events WHERE knife_id='K-001' AND type='handover'").fetchone()
        self.assertIsNotNone(ev)
        # 早班未还前不能收
        with self.assertRaises(KnifeError):
            self.svc.close_shift(morning)
        self.svc.return_knife("K-001", "E01", "A-01", "clean")
        self.svc.close_shift(morning)

    def test_handover_rejects_same_person_and_closed_shift(self):
        self.clock.t = self.now - timedelta(days=1, hours=2)
        night = self.svc.start_shift("夜班", "M01", started_at=self.clock.t)
        self.svc.issue("K-001", "E01", night)
        self.clock.t = self.now
        morning = self.svc.start_shift("早班", "M01")
        with self.assertRaises(KnifeError):
            self.svc.handover("K-001", "E01", morning, "E01")
        self.svc.close_shift(morning)
        with self.assertRaises(KnifeError) as cm:
            self.svc.handover("K-001", "E02", morning, "E01")
        self.assertEqual(cm.exception.code, "shift_closed")


class TestReinspectPermission(BaseCase):
    """磨刀者不能批准自己的复检。"""

    def _sharpen_done(self, kid="K-001"):
        s = self.svc.start_shift("早班", "M01")
        self.svc.send_sharpen(kid, "M01")
        self.svc.sharpen_done(kid, "S01")
        return s

    def test_sharpener_cannot_self_approve(self):
        self._sharpen_done()
        with self.assertRaises(KnifeError) as cm:
            self.svc.reinspect("K-001", "S01", True)
        self.assertEqual(cm.exception.code, "self_reinspect")
        # 被自己拒绝后仍是待复检，没有回架
        k = self.conn.execute("SELECT * FROM knives WHERE id='K-001'").fetchone()
        self.assertTrue(k["pending_reinspect"])
        self.assertEqual(k["status"], "sharpening")

    def test_other_inspector_approves_and_reschedules(self):
        self._sharpen_done()
        r = self.svc.reinspect("K-001", "S02", True)
        self.assertEqual(r["result"], "in_rack")
        k = self.conn.execute("SELECT * FROM knives WHERE id='K-001'").fetchone()
        self.assertEqual(k["status"], "in_rack")
        self.assertFalse(k["pending_reinspect"])
        self.assertIsNone(k["sharpened_by"])
        # 周期被顺延
        self.assertTrue(k["next_sharpen_due"] > iso(self.now))

    def test_manager_who_sharpened_cannot_self_approve(self):
        self.svc.send_sharpen("K-001", "M01")
        self.svc.sharpen_done("K-001", "M01")  # 经理兼磨刀
        with self.assertRaises(KnifeError):
            self.svc.reinspect("K-001", "M01", True)
        self.svc.reinspect("K-001", "S02", True)

    def test_chef_cannot_reinspect_and_fail_goes_quarantine(self):
        self._sharpen_done()
        with self.assertRaises(KnifeError) as cm:
            self.svc.reinspect("K-001", "E01", True)
        self.assertEqual(cm.exception.code, "not_inspector")
        r = self.svc.reinspect("K-001", "S02", False, note="刃线仍有毛刺")
        self.assertEqual(r["result"], "quarantine")
        # 不合格不能发放
        with self.assertRaises(KnifeError):
            self.svc.issue("K-001", "E01", self.svc.start_shift("晚班", "M01"))

    def test_repair_and_found_self_review_also_blocked(self):
        s = self.svc.start_shift("早班", "M01")
        # 维修：S01 登记修复，S01 不能复检
        self.svc.issue("K-002", "E02", s)
        self.svc.return_knife("K-002", "E02", None, "loose")
        self.svc.send_repair("K-002", "M01")
        self.svc.repair_done("K-002", "S01")
        with self.assertRaises(KnifeError) as cm:
            self.svc.reinspect_repair("K-002", "S01", True)
        self.assertEqual(cm.exception.code, "self_reinspect")
        self.svc.reinspect_repair("K-002", "S02", True)
        # 找回：E03 登记找回，E03 不能复检（即便他是经理也不行；这里用 M01 找回）
        self.svc.issue("K-003", "E03", s)
        self.svc.report_lost("K-003", "E03")
        self.svc.report_found("K-003", "M01")
        with self.assertRaises(KnifeError):
            self.svc.reinspect_found("K-003", "M01", True)
        self.svc.reinspect_found("K-003", "S02", True)


class TestLostFreeze(BaseCase):
    """遗失冻结、找回隔离与复检放行。"""

    def test_lost_knife_frozen_until_reinspected(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        self.svc.report_lost("K-001", "E01", note="刀架核对缺失")
        # 任何状态下都不能发放
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-001", "E02", s)
        self.assertEqual(cm.exception.code, "frozen_lost")
        # 找回先进隔离，仍然不能发
        self.svc.report_found("K-001", "E03")
        with self.assertRaises(KnifeError) as cm:
            self.svc.issue("K-001", "E02", s)
        self.assertEqual(cm.exception.code, "frozen_quarantine")
        # 复检通过才解冻
        self.svc.reinspect_found("K-001", "S02", True)
        self.svc.issue("K-001", "E02", s)
        self.assertEqual(
            self.conn.execute("SELECT current_holder_id FROM knives WHERE id='K-001'")
            .fetchone()["current_holder_id"], "E02")

    def test_found_inspect_fail_goes_repair(self):
        self.svc.report_lost("K-001", "E01")
        self.svc.report_found("K-001", "E02")
        r = self.svc.reinspect_found("K-001", "S02", False)
        self.assertEqual(r["result"], "repair")
        self.assertEqual(
            self.conn.execute("SELECT status FROM knives WHERE id='K-001'").fetchone()["status"],
            "repair")


class TestAnomalyFlowAndTrace(BaseCase):
    """缺口、错放、归还核对、最后接触人追溯。"""

    def test_chipped_return_quarantines_and_blocks_issue(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        r = self.svc.return_knife("K-001", "E01", None, "chipped")
        self.assertEqual(r, {"result": "quarantine", "reason": "chipped"})
        with self.assertRaises(KnifeError):
            self.svc.issue("K-001", "E02", s)
        # 隔离 → 送修 → 修复 → 他人复检 → 回架
        self.svc.send_repair("K-001", "M01")
        self.svc.repair_done("K-001", "S01")
        with self.assertRaises(KnifeError):
            self.svc.reinspect_repair("K-001", "S01", True)
        self.svc.reinspect_repair("K-001", "S02", True)
        self.svc.issue("K-001", "E02", s)

    def test_wrong_slot_quarantine_then_relocate(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        r = self.svc.return_knife("K-001", "E01", "A-02", "clean")  # 应回 A-01
        self.assertEqual(r["result"], "quarantine")
        self.assertEqual(r["reason"], "misplaced")
        # 归错的槽位不允许确认
        with self.assertRaises(KnifeError):
            self.svc.relocate_home("K-001", "M01", "A-02")
        self.svc.relocate_home("K-001", "M01", "A-01")
        self.assertEqual(
            self.conn.execute("SELECT status FROM knives WHERE id='K-001'").fetchone()["status"],
            "in_rack")

    def test_other_chef_cannot_return_and_clean_needs_slot(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        with self.assertRaises(KnifeError) as cm:
            self.svc.return_knife("K-001", "E02", "A-01", "clean")
        self.assertEqual(cm.exception.code, "not_holder")
        with self.assertRaises(KnifeError) as cm:
            self.svc.return_knife("K-001", "E01", None, "clean")
        self.assertEqual(cm.exception.code, "slot_required")

    def test_last_contact_traceable(self):
        s = self.svc.start_shift("早班", "M01")
        self.svc.issue("K-001", "E01", s)
        contact = self.svc.last_contact("K-001")
        self.assertEqual(contact["staff_id"], "E01")
        self.svc.return_knife("K-001", "E01", "A-01", "clean")
        contact = self.svc.last_contact("K-001")
        self.assertEqual(contact["staff_id"], "E01")
        types = [e["type"] for e in self.svc.timeline("K-001")]
        self.assertEqual(types, ["issue", "return_ok"])


class TestRemindersAndScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        from knifectl.service import utcnow
        self.now = utcnow()

        def clock():
            return self.now

        self.conn = connect(self.base / "t.db")
        init_db(self.conn)
        c = self.conn
        c.execute("INSERT INTO staff(id,name,role) VALUES ('M01','冯班','manager')")
        c.execute("INSERT INTO stations(id,name,zone) VALUES ('st','工位','热厨')")
        c.execute("INSERT INTO slots(id,station_id,rack,slot_row,slot_col) VALUES ('A-01','st','R1',1,1)")
        c.execute("INSERT INTO knives(id,type,home_slot_id,next_sharpen_due,status,pending_reinspect)"
                  " VALUES ('K-001','chef','A-01',?,'in_rack',0)",
                  (iso(self.now - timedelta(days=2)),))
        c.execute("INSERT INTO knives(id,type,home_slot_id,next_sharpen_due,status,pending_reinspect)"
                  " VALUES ('K-002','chef','A-01',?,'in_rack',1)",
                  (iso(self.now + timedelta(days=10)),))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_scan_flags_overdue_and_pending(self):
        eng = ReminderEngine(lambda: self.conn, interval_sec=1)
        items = eng.scan(self.now)
        kinds = {it["type"] for it in items}
        self.assertIn("overdue_sharpen", kinds)
        self.assertIn("pending_reinspect", kinds)
        overdue = [i for i in items if i["type"] == "overdue_sharpen"][0]
        self.assertEqual(overdue["knife_id"], "K-001")

    def test_scanner_adapter_queue(self):
        sc = MockScanner()
        sc.emit("K-001")
        sc.emit("A-01")
        sc.emit("E02")
        sc.emit("XYZ")
        evs = sc.drain()
        self.assertEqual([e["kind"] for e in evs], ["knife", "slot", "staff", "unknown"])
        self.assertEqual(sc.pending(), 0)


if __name__ == "__main__":
    unittest.main()
