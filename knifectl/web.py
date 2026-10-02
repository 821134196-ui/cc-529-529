"""HTTP 层（标准库 http.server）：页面路由、动作提交、扫码适配器 API、照片静态服务。"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ui
from .db import connect
from .reminders import ReminderEngine
from .scanner import MockScanner
from .service import (EDGE_LABELS, KNIFE_TYPES, REASON_LABELS, KnifeError,
                      KnifeService)
from .seed import seed

# 表单里复用的默认操作人（演示用；真实环境以工牌扫码覆盖）
DEFAULT_ACTOR = {"chef": "E01", "sharpener": "S01", "inspector": "S02", "manager": "M01"}


class AppState:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.db_path = str(self.base_dir / "knives.db")
        self.lock = threading.Lock()  # 写操作串行化；DB 内仍有原子 UPDATE 兜底并发
        self.scanner = MockScanner()
        self.reminders = ReminderEngine(self._conn, interval_sec=15)

    def _conn(self):
        return connect(self.db_path)

    def service(self):
        conn = self._conn()
        return conn, KnifeService(conn)

    def all_data(self, svc: KnifeService):
        staff = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM staff WHERE active=1 ORDER BY id").fetchall()]
        shifts = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC").fetchall()]
        knives = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM knives ORDER BY id").fetchall()]
        slots = [dict(r) for r in svc.conn.execute(
            "SELECT * FROM slots ORDER BY station_id, rack, slot_row, slot_col").fetchall()]
        return staff, shifts, knives, slots


def make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KnifeCtl/1.0"

        def log_message(self, fmt, *args):
            pass  # 安静模式；错误仍会打到 stderr

        # ---- 工具 ----
        def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, s: str, code: int = 200):
            self._send(code, s.encode("utf-8"))

        def _redirect(self, path: str):
            self.send_response(303)
            self.send_header("Location", path)
            self.end_headers()

        def _qs(self):
            parsed = urllib.parse.urlparse(self.path)
            return parsed.path, urllib.parse.parse_qs(parsed.query)

        def _form(self) -> dict[str, str]:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        # ---- GET ----
        def do_GET(self):
            path, qs = self._qs()
            try:
                if path == "/":
                    return self._rack()
                if path == "/scan":
                    return self._scan(flash=qs.get("m", [""])[0], kind=qs.get("k", [""])[0])
                if path == "/maintenance":
                    return self._maintenance(qs.get("m", [""])[0], qs.get("k", [""])[0])
                if path == "/reinspect":
                    return self._reinspect_form(qs.get("id", [""])[0],
                                                qs.get("kind", ["sharpen"])[0],
                                                qs.get("m", [""])[0], qs.get("k", [""])[0])
                if path == "/quarantine":
                    return self._quarantine(qs.get("m", [""])[0], qs.get("k", [""])[0])
                if path == "/trace":
                    return self._trace(qs.get("id", [""])[0])
                if path == "/api/scan/drain":
                    return self._json({"events": state.scanner.drain(),
                                       "pending": state.scanner.pending()})
                if path.startswith("/photos/"):
                    return self._photo(path)
                self._html("<h1>404</h1>", 404)
            except KnifeError as e:
                self._html(ui.shell("错误", "", ui.flash("err", str(e))), 400)

        def _photo(self, path: str):
            # 防目录穿越
            rel = Path(path.lstrip("/"))
            target = (state.base_dir / rel).resolve()
            root = (state.base_dir / "photos").resolve()
            if root not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, target.read_bytes(), "image/png")

        def _flash_html(self, m: str, k: str) -> str:
            if not m:
                return ""
            kind = {"e": "err", "o": "ok", "i": "info"}.get(k, "info")
            return ui.flash(kind, urllib.parse.unquote(m))

        def _rack(self):
            conn, svc = state.service()
            page = ui.rack_page(svc.rack_view(), state.reminders.latest())
            conn.close()
            self._html(page)

        def _base_ctx(self, svc, flash_msg="", flash_kind=""):
            staff, shifts, knives, slots = state.all_data(svc)
            open_shift = svc.open_shift()
            return {
                "staff": staff, "shifts": shifts, "knives": knives, "slots": slots,
                "actor_id": DEFAULT_ACTOR["chef"],
                "shift_id": open_shift["id"] if open_shift else (shifts[0]["id"] if shifts else None),
                "reminders": state.reminders.latest(),
                "flash": self._flash_html(flash_msg, flash_kind),
            }

        def _scan(self, flash: str, kind: str):
            conn, svc = state.service()
            ctx = self._base_ctx(svc, flash, kind)
            conn.close()
            self._html(ui.scan_page(ctx))

        def _maintenance(self, flash: str, kind: str):
            conn, svc = state.service()
            ctx = self._base_ctx(svc, flash, kind)
            ctx["actor_id"] = DEFAULT_ACTOR["sharpener"]
            ctx["now_iso"] = svc._now().isoformat()
            conn.close()
            self._html(ui.maintenance_page(ctx))

        def _reinspect_form(self, kid: str, kind: str, flash: str, fkind: str):
            conn, svc = state.service()
            k = svc.knife_detail(kid)
            staff = [dict(r) for r in svc.conn.execute(
                "SELECT * FROM staff WHERE active=1 AND role IN ('inspector','manager') ORDER BY id"
            ).fetchall()]
            done_by = None
            if kind == "repair":
                row = svc.conn.execute(
                    "SELECT actor_id FROM events WHERE knife_id=? AND type='repair_done' "
                    "ORDER BY id DESC LIMIT 1", (kid,)).fetchone()
                done_by = row["actor_id"] if row else None
            ctx = {"knife": k, "inspectors": staff, "kind": kind,
                   "done_by": done_by, "flash": self._flash_html(flash, fkind)}
            conn.close()
            self._html(ui.reinspect_page(ctx))

        def _quarantine(self, flash: str, kind: str):
            conn, svc = state.service()
            ctx = self._base_ctx(svc, flash, kind)
            ctx["actor_id"] = DEFAULT_ACTOR["manager"]
            ctx["quarantine"] = [k for k in ctx["knives"] if k["status"] == "quarantine"]
            ctx["repair"] = [k for k in ctx["knives"] if k["status"] == "repair"]
            ctx["lost"] = [k for k in ctx["knives"] if k["status"] == "lost"]
            conn.close()
            self._html(ui.quarantine_page(ctx))

        def _trace(self, kid: str):
            conn, svc = state.service()
            detail = svc.knife_detail(kid)
            contact = svc.last_contact(kid)
            conn.close()
            self._html(ui.trace_page(detail, contact))

        # ---- POST ----
        def do_POST(self):
            path, _ = self._qs()
            try:
                if path == "/api/scan/emit":
                    n = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(n) or b"{}")
                    ev = state.scanner.emit(data.get("code", ""))
                    return self._json({"ok": True, "event": ev.__dict__})
                form = self._form()
                with state.lock:
                    result = self._action(path, form)
                if result is None:
                    result = ("/scan", "操作完成", "o")
                loc, msg, kind = result
                sep = "&" if "?" in loc else "?"
                q = urllib.parse.urlencode({"m": msg, "k": kind})
                self._redirect(f"{loc}{sep}{q}")
            except KnifeError as e:
                back = self.headers.get("Referer", "/scan").split("?")[0]
                # 回到来源页并带错误
                loc = back if back.startswith("/") else "/scan"
                q = urllib.parse.urlencode({"m": str(e), "k": "e"})
                self._redirect(f"{loc}?{q}")

        def _action(self, path: str, f: dict[str, str]):
            conn, svc = state.service()
            try:
                actor = f.get("actor_id") or DEFAULT_ACTOR["manager"]

                if path == "/shift/start":
                    sid = svc.start_shift(f["name"], f.get("leader_id") or "M01")
                    return ("/scan", f"已开班 #{sid}：{f['name']}", "o")

                if path == "/scan/action":
                    return self._scan_action(svc, f, actor)

                if path == "/sharpen/send":
                    svc.send_sharpen(f["knife_id"], actor)
                    return ("/maintenance", f'{f["knife_id"]} 已送磨', "o")
                if path == "/sharpen/done":
                    svc.sharpen_done(f["knife_id"], f["sharpener_id"])
                    return ("/reinspect?id=" + urllib.parse.quote(f["knife_id"]) + "&kind=sharpen",
                            "磨刀完成，等待另一位复检员检查", "i")
                if path == "/reinspect":
                    return self._do_reinspect(svc, f)
                if path == "/repair/send":
                    svc.send_repair(f["knife_id"], actor)
                    return ("/quarantine", f'{f["knife_id"]} 已送修，修复前冻结发放', "i")
                if path == "/repair/done":
                    svc.repair_done(f["knife_id"], f["repairer_id"])
                    return ("/reinspect?id=" + urllib.parse.quote(f["knife_id"]) + "&kind=repair",
                            "维修完成，等待另一位复检员检查刀身完整性", "i")
                if path == "/found":
                    svc.report_found(f["knife_id"], f["finder_id"])
                    return ("/reinspect?id=" + urllib.parse.quote(f["knife_id"]) + "&kind=found",
                            "找回登记完成，须复检完整性后回架", "i")
                if path == "/relocate":
                    r = svc.relocate_home(f["knife_id"], actor, f["slot_id"])
                    return ("/quarantine", f'{f["knife_id"]} 已核对归位 {r["slot"]}', "o")
                raise KnifeError("bad_action", f"未知操作 {path}")
            finally:
                conn.close()

        def _scan_action(self, svc: KnifeService, f: dict[str, str], actor: str):
            op = f.get("op")
            kid = f["knife_id"].strip().upper() if f.get("knife_id") else ""
            shift_id = int(f["shift_id"]) if f.get("shift_id") else None

            if op == "issue":
                svc.issue(kid, f["actor_id"], shift_id)
                return ("/scan", f"{kid} 已由 {f['actor_id']} 独占领用", "o")

            if op == "return_clean":
                slot = (f.get("slot_id") or "").strip().upper() or None
                r = svc.return_knife(kid, actor, slot, "clean")
                if r["result"] == "quarantine":
                    return ("/scan", f"{kid} 扫入错误槽位 {r['slot']}，已按错放工位隔离", "e")
                return ("/scan", f"{kid} 清洗核对完毕，归位 {r['slot']}", "o")

            if op in ("return_dull", "return_loose", "return_chipped"):
                cond = {"return_dull": "dull", "return_loose": "loose",
                        "return_chipped": "chipped"}[op]
                r = svc.return_knife(kid, actor, None, cond)
                if r["result"] == "quarantine":
                    reason = REASON_LABELS[r["reason"]]
                    return ("/quarantine", f"{kid} 已隔离（{reason}），待处理", "i")
                return ("/maintenance", f"{kid} 刃钝，已转入磨刀流程", "i")

            if op == "report_lost":
                svc.report_lost(kid, actor)
                return ("/quarantine", f"{kid} 已报遗失并冻结发放", "e")

            if op == "close_shift":
                info = svc.close_shift(shift_id)
                extra = "（该班次跨午夜，已标注跨日）" if info["cross_day"] else ""
                return ("/scan", f"班次 #{shift_id} 逐件核对完毕，收班成功{extra}", "o")

            if op == "handover":
                r = svc.handover(kid, f["to_chef"].strip().upper(),
                                 int(f["to_shift"]), actor)
                return ("/scan", f'{kid} 已交接 {r["from"]} → {r["to"]}', "o")

            raise KnifeError("bad_op", f"未知领还操作 {op}")

        def _do_reinspect(self, svc: KnifeService, f: dict[str, str]):
            kid, inspector = f["knife_id"], f["inspector_id"]
            passed = f.get("passed") == "1"
            kind = f.get("kind", "sharpen")
            if kind == "sharpen":
                r = svc.reinspect(kid, inspector, passed)
            elif kind == "repair":
                r = svc.reinspect_repair(kid, inspector, passed)
            else:
                r = svc.reinspect_found(kid, inspector, passed)
            if r["result"] == "in_rack":
                return ("/", f"{kid} 复检通过（锋利度与刀身完整性合格），已回架", "o")
            return ("/quarantine", f"{kid} 复检未过，已退回隔离/维修", "e")

    return Handler


def serve(base_dir: str = "./data", host: str = "127.0.0.1", port: int = 8077,
          reseed: bool = True) -> None:
    from .service import utcnow
    state = AppState(base_dir)
    if reseed or not Path(state.db_path).exists():
        summary = seed(state.db_path, str(state.base_dir), utcnow())
        print("种子案例：", json.dumps(
            {k: v for k, v in summary.items() if k != "knife_types"}, ensure_ascii=False))
    state.reminders.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"后厨刀具管控台已启动：http://{host}:{port}")
    print("  / 刀架图  /scan 领还扫码  /maintenance 保养排程  /quarantine 异常隔离")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        state.reminders.stop()
        httpd.server_close()
