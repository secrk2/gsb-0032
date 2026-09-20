#!/usr/bin/env bash
# 执钥后端入口：
#   web      等待依赖 -> migrate -> (可选) seed -> uvicorn(ASGI/SSE)
#   checker  在线周期巡检进程
#   shell    透传命令
set -euo pipefail

wait_for() {
  local host="$1" port="$2" retries=60
  until nc -z "$host" "$port" 2>/dev/null; do
    retries=$((retries - 1))
    if [ "$retries" -le 0 ]; then
      echo "[entrypoint] 等待 $host:$port 超时" >&2
      exit 1
    fi
    echo "[entrypoint] 等待 $host:$port ..."
    sleep 2
  done
  echo "[entrypoint] $host:$port 已就绪"
}

ROLE="${1:-web}"

case "$ROLE" in
  web|checker)
    wait_for "${MYSQL_HOST:-mysql}" "${MYSQL_PORT:-3306}"
    wait_for "$(echo "${REDIS_URL:-redis://redis:6379/0}" | sed -E 's#.*://([^:/]+).*#\1#')" 6379
    ;;
esac

case "$ROLE" in
  web)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput >/dev/null 2>&1 || true
    if [ "${SEED_DEMO:-1}" = "1" ]; then
      python manage.py seed_demo
    fi
    exec uvicorn zhiyue.asgi:application \
      --host 0.0.0.0 --port 8000 --workers "${WEB_WORKERS:-2}"
    ;;
  checker)
    # 等 web 完成首次迁移
    sleep 8
    exec python manage.py check_online
    ;;
  shell)
    shift
    exec "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
