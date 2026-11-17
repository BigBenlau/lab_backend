#!/bin/sh
set -eu

invalid() {
    # 設定錯誤一律停止，不輸出設定值。
    echo 'Invalid gateway settings: check ADMIN_HOST and ADMIN_HTTPS_PORT' >&2
    exit 1
}

# 對外端口與 Compose Origin 共用設定；443 留給獨立官網。
case "${ADMIN_HOST:-}" in ''|*[!a-z0-9.-]*) invalid;; esac
ADMIN_HTTPS_PORT=${ADMIN_HTTPS_PORT:-27182}
case "$ADMIN_HTTPS_PORT" in ''|0*|*[!0-9]*) invalid;; esac
[ "${#ADMIN_HTTPS_PORT}" -le 5 ] || invalid
[ "$ADMIN_HTTPS_PORT" -ge 1024 ] && [ "$ADMIN_HTTPS_PORT" -le 65535 ] || invalid

umask 077
# 僅替換部署變數，不替換 Nginx 的 $host 等變數；先檢查，再開 listener。
envsubst '${ADMIN_HOST}' < /etc/lab/nginx.conf.template > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
