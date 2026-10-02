"""python -m knifectl [--port 8077] [--host 127.0.0.1] [--data ./data] [--no-reseed]"""
import argparse

from .web import serve


def main() -> None:
    ap = argparse.ArgumentParser(description="后厨刀具管控台（一条命令启动，自带缺口案例）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--data", default="./data")
    ap.add_argument("--no-reseed", action="store_true", help="保留现有数据库，不重建案例")
    args = ap.parse_args()
    serve(base_dir=args.data, host=args.host, port=args.port, reseed=not args.no_reseed)


if __name__ == "__main__":
    main()
