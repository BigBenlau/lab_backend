#!/bin/sh
set -eu

invalid() {
    # 設定錯誤一律停止，不輸出設定值。
    echo 'Invalid gateway settings: check ADMIN_HOST, mode and selected admin port' >&2
    exit 1
}

# 對外端口與 Compose Origin 共用設定；443 留給獨立官網。
case "${ADMIN_HOST:-}" in ''|*[!a-z0-9.-]*) invalid;; esac
case "${LAB_HTTP_TEST:-false}" in
    false) admin_port=${ADMIN_HTTPS_PORT:-27182} ;;
    true) admin_port=${ADMIN_HTTP_PORT:-27181} ;;
    *) invalid ;;
esac
case "$admin_port" in ''|0*|*[!0-9]*) invalid;; esac
[ "${#admin_port}" -le 5 ] || invalid
[ "$admin_port" -ge 1024 ] && [ "$admin_port" -le 65535 ] || invalid

umask 077
# 只生成固定指令，使用者不能注入 Nginx 設定；兩模式共用其餘路由與限制。
if [ "${LAB_HTTP_TEST:-false}" = true ]; then
    echo 'listen 27182 default_server;' > /tmp/transport.conf
else
    cat > /tmp/transport.conf <<'TLS'
listen 27182 ssl default_server;
ssl_certificate /run/tls/cert.pem;
ssl_certificate_key /run/tls/key.pem;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_session_tickets off;
TLS
fi
# 僅替換部署變數，不替換 Nginx 的 $host 等變數；先檢查，再開 listener。
envsubst '${ADMIN_HOST}' < /etc/lab/nginx.conf.template > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
