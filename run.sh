#!/usr/bin/env bash
# 一条命令启动后厨刀具管控台（自动重建带缺口案例的数据库）。
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8077}"
exec python3 -m knifectl --host 0.0.0.0 --port "$PORT" --data ./data
