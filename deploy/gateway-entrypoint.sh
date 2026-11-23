#!/bin/sh
set -eu

# 只有兩種固定部署模式；主機映射及此處listener由基本檢查一起核對。
case "${LAB_HTTP_TEST:-false}" in
    false) admin_port=27182; PUBLIC_LISTEN_PORT=8443; tls_suffix=' ssl' ;;
    true) admin_port=27181; PUBLIC_LISTEN_PORT=8080; tls_suffix='' ;;
    *) echo 'LAB_HTTP_TEST must be true or false' >&2; exit 1 ;;
esac

write_transport() {
    # 路徑及端口皆為固定值；HTTP完全不生成憑證指令。
    printf 'listen %s%s default_server;\n' "$2" "$tls_suffix" > "$1"
    if [ "${LAB_HTTP_TEST:-false}" = false ]; then
        printf 'ssl_certificate %s/cert.pem;\nssl_certificate_key %s/key.pem;\n' "$3" "$3" >> "$1"
        printf 'ssl_protocols TLSv1.2 TLSv1.3;\nssl_session_tickets off;\n' >> "$1"
        # 497 表示明文誤入 TLS 端口。只引導瀏覽器重新開頁，不重送登入或寫入請求。
        cat >> "$1" <<NGINX
error_page 497 = @https_navigation;
location @https_navigation {
    if (\$request_method !~ ^(GET|HEAD)$) { return 400; }
    if (\$redirect_host = "") { return 400; }
    if (\$request_uri !~ ^/) { return 400; }
    return 302 https://\$redirect_host${4}\$request_uri;
}
NGINX
    fi
}

umask 077
write_transport /tmp/public-transport.conf "$PUBLIC_LISTEN_PORT" /run/public-tls ""
write_transport /tmp/admin-transport.conf "$admin_port" /run/tls ":$admin_port"
export PUBLIC_LISTEN_PORT
# 僅替換由模式決定的公開端口，不展開Nginx的$host、$uri等變數。
envsubst '${PUBLIC_LISTEN_PORT}' < /etc/lab/nginx.conf.template > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
