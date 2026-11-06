#!/bin/sh
set -eu
# 僅替換部署變數，不替換 Nginx 的 $host 等變數；拒絕設定注入。
case "${ADMIN_HOST:?}" in ''|*[!a-zA-Z0-9.-]*) echo 'Invalid hostname' >&2; exit 1;; esac
umask 077
envsubst '${ADMIN_HOST}' < /etc/lab/nginx.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
