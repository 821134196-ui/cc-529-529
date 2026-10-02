"""HTTP 服务：JSON API + SSE 事件流 + 静态前端 + 本地刃口照片。

零第三方依赖，基于 http.server 的 ThreadingHTTPServer。
所有写操作经同一把锁串行化，保证 SQLite 与领刀独占判定的一致性。
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .db import DB, now
from .events import EventBus, ReminderDaemon, ScannerAdapter
from .service import DomainError, Service

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, db: DB, photos_dir: Path):
        self.db = db
        self.svc = Service(db)
        self.bus = EventBus()
        self.scanner = ScannerAdapter(self.bus)
        self.scanner.on_scan = self._auto_scan
        self.reminder = ReminderDaemon(db, self.bus, interval=20.0)
        self.lock = threading.RLock()
        self.photos = photos_dir
        self.photos.mkdir(parents=True, exist_ok=True)

    def _auto_scan(self, code: str, actor: str) -> None:
        """扫码枪事件 → 领域动作：在架则领出，在用则归还；异常由领域层拦截并上报。"""
        try:
            with self.lock:
                k = self.db.knife(code)
                if not k:
                    raise DomainError(f"扫描到未登记编号 {code}")
                if k["current_user"]:
                    self.svc.checkin(code, actor)
                    msg, kind = f"{code} 已清洗核对归还（{actor}）", "checkin"
                else:
                    self.svc.checkout(code, actor)
                    msg, kind = f"{code} 已发放给 {actor}", "checkout"
            self.bus.publish(kind, {"msg": msg, "code": code})
        except DomainError as e:
            self.bus.publish("scan_rejected", {"msg": str(e), "code": code})

    def state(self) -> dict:
        knives = []
        for k in self.db.all_knives():
            d = dict(k)
            d["flags"] = self.db.knife_flags(k)
            d["last_handler"] = self.svc.last_handler(k["id"])
            knives.append(d)
        stations = [dict(s) for s in self.db.q("SELECT * FROM stations ORDER BY id")]
        sh = self.db.open_shift()
        shifts = [dict(s) for s in self.db.q("SELECT * FROM shifts ORDER BY id DESC LIMIT 10")]
        jobs = [dict(j) for j in self.db.q(
            "SELECT j.*, k.name AS knife_name FROM sharpen_jobs j "
            "JOIN knives k ON k.id=j.knife_id ORDER BY j.id DESC LIMIT 30")]
        recent = [dict(m) for m in self.db.q(
            "SELECT m.* FROM movements m WHERE m.knife_id IS NOT NULL "
            "ORDER BY m.id DESC LIMIT 80")]
        return {"stations": stations, "knives": knives, "shift": dict(sh) if sh else None,
                "shifts": shifts, "jobs": jobs, "recent": recent,
                "scanner_mode": self.scanner.mode}

    def save_photo(self, knife_id: str, data_url: str) -> str:
        header, _, b64 = data_url.partition(",")
        ext = ".png" if "png" in header else (".jpg" if "jpeg" in header else ".bin")
        d = self.photos / knife_id
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
        (d / fname).write_bytes(base64.b64decode(b64))
        return f"{knife_id}/{fname}"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KitchenKnives/1.0"

        def log_message(self, fmt, *args):  # 静默默认访问日志
            pass

        # ---------- 响应工具 ----------
        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _err(self, msg, status=400):
            self._json({"ok": False, "error": msg}, status)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            if not n:
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode())
            except json.JSONDecodeError:
                raise DomainError("请求体不是合法 JSON")

        def _do(self, fn):
            try:
                with app.lock:
                    result = fn()
                payload = result if isinstance(result, dict) else {"ok": True}
                payload.setdefault("ok", True)
                if payload.get("event"):
                    app.bus.publish(payload["event"], payload.get("event_payload", {}))
                self._json(payload)
            except DomainError as e:
                self._err(str(e), 409)
            except FileNotFoundError as e:
                self._err(str(e), 404)

        # ---------- 路由 ----------
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                self._json(app.state())
            elif path.startswith("/api/knife/"):
                kid = path.rsplit("/", 1)[-1]
                with app.lock:
                    k = app.db.knife(kid)
                    if not k:
                        return self._err("刀具不存在", 404)
                    d = dict(k)
                    d["flags"] = app.db.knife_flags(k)
                    d["timeline"] = app.svc.timeline(kid)
                    d["last_handler"] = app.svc.last_handler(kid)
                    d["jobs"] = [dict(j) for j in app.db.q(
                        "SELECT * FROM sharpen_jobs WHERE knife_id=? ORDER BY id", (kid,))]
                self._json(d)
            elif path.startswith("/api/photos/"):
                self._serve_photo(path[len("/api/photos/"):])
            elif path == "/api/events":
                self._sse()
            else:
                self._static(path)

        def do_POST(self):
            path = urlparse(self.path).path
            routes = {
                "/api/shift/start": self._shift_start,
                "/api/shift/handover": self._handover,
                "/api/shift/close": self._close,
                "/api/checkout": self._checkout,
                "/api/checkin": self._checkin,
                "/api/issue": self._issue,
                "/api/restore": self._restore,
                "/api/sharpen/request": self._sharpen_request,
                "/api/sharpen/complete": self._sharpen_complete,
                "/api/sharpen/check": self._sharpen_check,
                "/api/scan": self._scan,
                "/api/scanner/script": self._scanner_script,
            }
            fn = routes.get(path)
            if fn:
                fn()
            else:
                self._err("未知接口", 404)

        # ---------- 业务端点 ----------
        def _shift_start(self):
            b = self._body()
            self._do(lambda: {
                "event": "shift",
                "event_payload": {"msg": f"班次 {b.get('name', '')} 已开始"},
                "shift_id": app.svc.start_shift(b.get("name") or f"班次 {now():%m-%d %H:%M}",
                                                _when(b))})

        def _handover(self):
            b = self._body()
            def run():
                r = app.svc.handover(b["name"], b.get("actor", "值班主管"), _when(b),
                                     b.get("note", ""))
                return {"event": "shift", "event_payload": {"msg": "跨班交接完成"}, **r}
            self._do(run)

        def _close(self):
            b = self._body()
            self._do(lambda: {"event": "shift",
                              "event_payload": {"msg": "班次已关闭"},
                              **app.svc.close_shift(b.get("actor", "值班主管"), _when(b))})

        def _checkout(self):
            b = self._body()
            def run():
                mid = app.svc.checkout(b["knife_id"], b["user"], _when(b),
                                       b.get("station_id"))
                return {"event": "checkout", "movement_id": mid,
                        "event_payload": {"msg": f"{b['knife_id']} 已发给 {b['user']}"}}
            self._do(run)

        def _checkin(self):
            b = self._body()
            def run():
                mid = app.svc.checkin(b["knife_id"], b["user"], _when(b),
                                      washed=b.get("washed", True), note=b.get("note", ""))
                return {"event": "checkin", "movement_id": mid,
                        "event_payload": {"msg": f"{b['knife_id']} 已清洗核对归还"}}
            self._do(run)

        def _issue(self):
            b = self._body()
            def run():
                mid = app.svc.report_issue(b["knife_id"], b["issue_type"], b["actor"],
                                           _when(b), b.get("note", ""),
                                           b.get("found_station"))
                return {"event": "issue", "movement_id": mid,
                        "event_payload": {"msg": f"{b['knife_id']} 登记 {b['issue_type']}，已隔离/冻结"}}
            self._do(run)

        def _restore(self):
            b = self._body()
            def run():
                mid = app.svc.restore_to_rack(b["knife_id"], b["actor"], _when(b),
                                              b.get("note", ""), b.get("station_id"))
                return {"event": "restore", "movement_id": mid,
                        "event_payload": {"msg": f"{b['knife_id']} 已恢复在架"}}
            self._do(run)

        def _sharpen_request(self):
            b = self._body()
            def run():
                jid = app.svc.request_sharpen(b["knife_id"], b["actor"], _when(b),
                                              b.get("note", ""))
                return {"event": "sharpen", "job_id": jid,
                        "event_payload": {"msg": f"{b['knife_id']} 已送磨"}}
            self._do(run)

        def _sharpen_complete(self):
            b = self._body()
            def run():
                photo = None
                if b.get("photo"):
                    photo = app.save_photo(b["knife_id"], b["photo"])
                app.svc.complete_sharpen(int(b["job_id"]), b["actor"],
                                         b.get("edge_state", "锋利"), _when(b),
                                         photo, b.get("note", ""))
                return {"event": "sharpen", "photo_path": photo,
                        "event_payload": {"msg": f"磨刀单 #{b['job_id']} 完成，等待他人复检"}}
            self._do(run)

        def _sharpen_check(self):
            b = self._body()
            def run():
                app.svc.check_sharpen(int(b["job_id"]), b["inspector"], b["result"],
                                      _when(b), b.get("note", ""))
                return {"event": "sharpen",
                        "event_payload": {"msg": f"磨刀单 #{b['job_id']} 复检{b['result']}"}}
            self._do(run)

        def _scan(self):
            b = self._body()
            app.scanner.scan(b["code"], b.get("actor"))
            self._json({"ok": True})

        def _scanner_script(self):
            b = self._body()
            if b.get("stop"):
                app.scanner.stop_script()
            else:
                app.scanner.start_script(b["codes"], b.get("actor", "扫码枪"),
                                         float(b.get("interval", 1.5)))
            self._json({"ok": True, "mode": app.scanner.mode})

        # ---------- SSE ----------
        def _sse(self):
            sid, q = app.bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 5000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        evt = q.get(timeout=15)
                    except Exception:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    data = json.dumps(evt, ensure_ascii=False)
                    self.wfile.write(f"id: {evt['id']}\nevent: {evt['kind']}\ndata: {data}\n\n"
                                     .encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                app.bus.unsubscribe(id(q))

        # ---------- 静态 / 照片 ----------
        def _static(self, path: str):
            if path in ("/", ""):
                path = "/index.html"
            target = (STATIC / path.lstrip("/")).resolve()
            if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(
                target.suffix, "application/octet-stream")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_photo(self, rel: str):
            target = (app.photos / rel).resolve()
            if not str(target).startswith(str(app.photos.resolve())) or not target.is_file():
                return self._err("照片不存在", 404)
            ctype = "image/png" if target.suffix == ".png" else "image/jpeg"
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _when(b: dict):
    from datetime import datetime
    return datetime.strptime(b["ts"], "%Y-%m-%dT%H:%M:%S") if b.get("ts") else None


def serve(db_path: str | Path, photos_dir: str | Path, host: str = "127.0.0.1",
          port: int = 8765, open_browser: bool = True) -> None:
    db = DB(db_path)
    app = App(db, Path(photos_dir))
    app.reminder.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{port}/"
    print(f"◆ 后厨刀具流转系统已启动：{url}")
    print(f"  数据库：{db_path}　刃口照片：{photos_dir}")
    print("  Ctrl+C 退出")
    if open_browser:
        threading.Timer(0.6, lambda: _open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.reminder.stop()
        httpd.server_close()


def _open(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass
