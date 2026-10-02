#!/usr/bin/env python3
"""一条命令启动后厨刀具流转系统。

用法：
    python3 run.py                # 初始化（含缺口等异常案例）并启动
    python3 run.py --reset        # 清空后重新播种
    python3 run.py --port 9000
"""
from __future__ import annotations

import argparse
from pathlib import Path

from kitchen_knives.db import DB
from kitchen_knives.seed import seed
from kitchen_knives.web import serve

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def main() -> None:
    ap = argparse.ArgumentParser(description="后厨刀具流转管理系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reset", action="store_true", help="清空数据库并重新播种")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    db_path = DATA / "knives.db"
    photos = DATA / "photos"
    db = DB(db_path)
    seed(db, photos, reset=args.reset)
    db.conn.close()

    print("=" * 56)
    print("  后厨刀具流转系统 · 已载入演示案例")
    print("  · D-003 砍刀：刃口缺口（待修隔离，禁止发放）")
    print("  · D-004 水果刀：刀柄松动（待修）")
    print("  · D-008 片刀：错放工位（热菜/凉菜串位）")
    print("  · D-009 水果刀：遗失冻结")
    print("  · D-002 / D-015：磨刀超期；D-011：磨刀后未复检")
    print("  · D-010 主厨刀：王强领用未还（刀架空槽=缺件）")
    print("=" * 56)
    serve(db_path, photos, host=args.host, port=args.port,
          open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
