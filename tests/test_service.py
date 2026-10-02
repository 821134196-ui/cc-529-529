"""领域规则测试：独占领用、跨班交接、复检权限、遗失冻结。

运行：python3 -m unittest discover -s tests -v
"""
import unittest
from datetime import timedelta
from pathlib import Path
import tempfile

from kitchen_knives.db import DB, iso, now
from kitchen_knives.service import DomainError, Service


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "t.db")
        self.db.add_station("ST1", "热菜间", 2, 6)
        self.db.add_station("ST2", "凉菜间", 2, 6)
        t = now() - timedelta(days=1)
        self.db.add_knife(dict(id="K1", name="主厨刀", profile="chef",
                               home_station_id="ST1", slot="A-01", holder="周敏",
                               sharpen_cycle_d=7, last_sharpen_ts=iso(now() - timedelta(days=1)),
                               status="在架", current_user=None, issue_type=None,
                               issue_note=None, issue_ts=None,
                               found_station_id=None, found_slot=None, quarantined=0,
                               created_ts=iso(t)))
        self.db.add_knife(dict(id="K2", name="片刀", profile="slicer",
                               home_station_id="ST1", slot="A-02", holder="陈海",
                               sharpen_cycle_d=5, last_sharpen_ts=iso(now() - timedelta(days=12)),
                               status="在架", current_user=None, issue_type=None,
                               issue_note=None, issue_ts=None,
                               found_station_id=None, found_slot=None, quarantined=0,
                               created_ts=iso(t)))
        self.svc = Service(self.db)
        self.svc.start_shift("测试班")

    def tearDown(self):
        self.tmp.cleanup()

    def _freeze(self, kid):
        self.db.execute("UPDATE knives SET status='遗失', issue_type='遗失', quarantined=1 "
                        "WHERE id=?", (kid,))

    def _isolate(self, kid, issue="缺口"):
        self.db.execute("UPDATE knives SET status='待修', issue_type=?, quarantined=1 "
                        "WHERE id=?", (issue, kid))

    # ---- 1. 独占领用 ----
    def test_exclusive_checkout(self):
        self.svc.checkout("K1", "王强")
        k = self.db.knife("K1")
        self.assertEqual(k["status"], "在用")
        self.assertEqual(k["current_user"], "王强")
        # 第二个人不能同时领用同一把刀
        with self.assertRaisesRegex(DomainError, "不能同时被两人领用"):
            self.svc.checkout("K1", "李娜")
        # 归还后才能被另一人领用
        self.svc.checkin("K1", "王强")
        self.assertIsNone(self.db.knife("K1")["current_user"])
        self.svc.checkout("K1", "李娜")
        self.assertEqual(self.db.knife("K1")["current_user"], "李娜")

    def test_last_handler_traceable(self):
        self.svc.checkout("K1", "王强")
        self.svc.checkin("K1", "王强", note="已清洗")
        self.assertEqual(self.svc.last_handler("K1"), "王强")
        self.svc.report_issue("K1", "缺口", "值班主管", note="复检发现崩口")
        self.assertEqual(self.svc.last_handler("K1"), "值班主管")

    # ---- 2. 跨班交接 ----
    def test_cross_shift_handover(self):
        # 王强在晚市班领刀未还，班次跨日交接给早市班
        self.svc.checkout("K1", "王强")
        old = self.db.open_shift()
        r = self.svc.handover("次日早市班", "值班主管", note="跨日 23:50→00:10")
        self.assertEqual(r["transferred"], ["K1"])
        # 新班进行中，旧班已交接
        new = self.db.open_shift()
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(self.db.one("SELECT status s FROM shifts WHERE id=?",
                                     (old["id"],))["s"], "已交接")
        # 刀仍挂在王强名下，新班内逐刀有交接与接收两条记录
        self.assertEqual(self.db.knife("K1")["current_user"], "王强")
        actions = [m["action"] for m in self.db.q(
            "SELECT action FROM movements WHERE knife_id='K1' ORDER BY id")]
        self.assertIn("跨班交接", actions)
        self.assertIn("跨班接收", actions)

    def test_close_shift_blocked_when_outstanding(self):
        self.svc.checkout("K1", "王强")
        with self.assertRaisesRegex(DomainError, "未归还"):
            self.svc.close_shift("值班主管")
        self.svc.checkin("K1", "王强")
        self.svc.close_shift("值班主管")  # 全部归还后可关班

    # ---- 3. 复检权限 ----
    def test_sharpener_cannot_inspect_own_work(self):
        jid = self.svc.request_sharpen("K2", "陈海")
        self.svc.complete_sharpen(jid, "老李", edge_state="锋利")
        # 磨刀人本人复检 → 拒绝
        with self.assertRaisesRegex(DomainError, "磨刀者不能复检自己"):
            self.svc.check_sharpen(jid, "老李", "通过")
        # 另一个人复检通过 → 归架并刷新磨刀日
        self.svc.check_sharpen(jid, "周敏", "通过")
        k = self.db.knife("K2")
        self.assertEqual(k["status"], "在架")
        self.assertFalse(self.db.sharpen_due(k))

    def test_reject_check_quarantines(self):
        jid = self.svc.request_sharpen("K2", "陈海")
        self.svc.complete_sharpen(jid, "老李", edge_state="偏钝")
        self.svc.check_sharpen(jid, "周敏", "驳回", note="刃口仍有毛刺")
        k = self.db.knife("K2")
        self.assertEqual(k["status"], "待修")
        self.assertEqual(k["quarantined"], 1)
        # 待修刀不能发放
        with self.assertRaisesRegex(DomainError, "不能发放"):
            self.svc.checkout("K2", "陈海")

    def test_unchecked_knife_not_issued(self):
        jid = self.svc.request_sharpen("K2", "陈海")
        self.svc.complete_sharpen(jid, "老李")
        with self.assertRaisesRegex(DomainError, "尚未经他人复检"):
            self.svc.checkout("K2", "陈海")

    # ---- 4. 遗失冻结 ----
    def test_lost_knife_frozen(self):
        self._freeze("K1")
        with self.assertRaisesRegex(DomainError, "遗失"):
            self.svc.checkout("K1", "王强")
        with self.assertRaisesRegex(DomainError, "遗失"):
            self.svc.request_sharpen("K1", "陈海")
        # 遗失期间被他人领用的并发路径同样被挡
        self.db.execute("UPDATE knives SET current_user='王强' WHERE id='K1'")
        with self.assertRaises(DomainError):
            self.svc.checkout("K1", "李娜")

    def test_found_lost_knife_restored(self):
        self._freeze("K1")
        self.svc.restore_to_rack("K1", "值班主管", note="冷库货架缝隙找到")
        k = self.db.knife("K1")
        self.assertEqual(k["status"], "在架")
        self.assertEqual(k["quarantined"], 0)
        self.svc.checkout("K1", "王强")  # 找回登记后可正常发放
        self.assertEqual(k and self.db.knife("K1")["current_user"], "王强")

    def test_chip_and_loose_handle_block_issue(self):
        # 缺口、刀柄松动 → 待修隔离，不能发放
        self.svc.report_issue("K1", "缺口", "老赵", note="4mm 崩口")
        with self.assertRaisesRegex(DomainError, "不能发放"):
            self.svc.checkout("K1", "王强")
        self.svc.report_issue("K2", "松柄", "孙琳")
        with self.assertRaisesRegex(DomainError, "不能发放"):
            self.svc.checkout("K2", "李娜")

    def test_misplaced_station_blocks_checkout(self):
        # K2 归属 ST1，被错放到 ST2：从 ST2 扫码不能领用，必须先归位
        with self.assertRaisesRegex(DomainError, "错放工位"):
            self.svc.checkout("K2", "陈海", station_id="ST2")
        # 登记错放 → 归位 → 正常领用
        self.svc.report_issue("K2", "错放", "林晓", found_station="ST2",
                              note="热菜片刀出现在凉菜间")
        # 即使从归属工位扫，错放未归位前也不能发放
        with self.assertRaisesRegex(DomainError, "未归位前不能发放"):
            self.svc.checkout("K2", "陈海")
        self.svc.restore_to_rack("K2", "林晓", station_id="ST1")
        self.svc.checkout("K2", "陈海")

    def test_in_use_knife_cannot_be_sent_to_sharpen(self):
        self.svc.checkout("K1", "王强")
        with self.assertRaisesRegex(DomainError, "请先归还再送磨"):
            self.svc.request_sharpen("K1", "周敏")
        self.svc.checkin("K1", "王强")
        self.svc.request_sharpen("K1", "周敏")  # 归还后可送磨

    def test_report_lost_clears_holder(self):
        self.svc.checkout("K1", "王强")
        self.svc.report_issue("K1", "遗失", "值班主管", note="收档缺件")
        k = self.db.knife("K1")
        self.assertIsNone(k["current_user"])
        self.assertEqual(k["status"], "遗失")

    def test_overdue_flag(self):
        self.assertTrue(self.db.sharpen_due(self.db.knife("K2")))
        self.assertFalse(self.db.sharpen_due(self.db.knife("K1")))
        kinds = {f["kind"] for f in self.db.knife_flags(self.db.knife("K2"))}
        self.assertIn("超期", kinds)


if __name__ == "__main__":
    unittest.main()
