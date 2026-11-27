#!/bin/sh
# 容器入口：等数据库 → 跑迁移 → 交棒给 CMD。
#
# 为什么不把 alembic 写进 CMD：迁移失败必须让容器直接退出（而不是带着半张表
# 启动），这样 compose / k8s 的重启策略才有意义。应用侧 AUTO_APPLY_SCHEMA
# 保持 false，避免多副本同时 DDL。
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  if [ "${RUN_MIGRATIONS:-1}" != "0" ]; then
    echo "[entrypoint] waiting for database ..."
    python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlsplit

url = urlsplit(os.environ["DATABASE_URL"])
host = url.hostname or "127.0.0.1"
port = url.port or 5432
timeout = float(os.environ.get("DB_WAIT_TIMEOUT", "60"))
deadline = time.monotonic() + timeout

while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] database {host}:{port} reachable", flush=True)
            sys.exit(0)
    except OSError:
        time.sleep(1)

print(f"[entrypoint] database {host}:{port} unreachable after {timeout:.0f}s", file=sys.stderr, flush=True)
sys.exit(1)
PY

    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
  fi
fi

exec "$@"