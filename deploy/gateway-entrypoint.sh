#!/bin/sh
set -eu

# 只有兩種固定部署模式；主機映射及此處listener由基本檢查一起核對。
case "${LAB_HTTP_TEST:-false}" in
    false)
        admin_port=27182; PUBLIC_LISTEN_PORT=8444; tls_suffix=' ssl'
        public_listener="127.0.0.1:$PUBLIC_LISTEN_PORT proxy_protocol"
        ACME_STREAM_INCLUDE='include /etc/lab/acme-stream.conf;'
        HTTPS_REDIRECT_INCLUDE='include /etc/lab/https-redirect.conf;' ;;
    true)
        admin_port=27181; PUBLIC_LISTEN_PORT=8080; tls_suffix=''
        public_listener=$PUBLIC_LISTEN_PORT; ACME_STREAM_INCLUDE=''; HTTPS_REDIRECT_INCLUDE='' ;;
    *) echo 'LAB_HTTP_TEST must be true or false' >&2; exit 1 ;;
esac

write_names() {
    # 只接受憑證中逐項列出的網站名稱與 IPv4，避免將任意 Host 或 SAN 寫入轉址設定。
    sans=$(openssl x509 -in "$1/cert.pem" -noout -ext subjectAltName)
    printf '%s\n' "$sans" | tr ',' '\n' | sed -nE \
        -e 's/^[[:space:]]*DNS:(casino\.ye\.wang|crab\.ye\.wang|ye\.wang)[[:space:]]*$/\1 \1;/p' \
        -e 's/^[[:space:]]*IP Address:(([0-9]{1,3}\.){3}[0-9]{1,3})[[:space:]]*$/\1 \1;/p' > "$2.next"
    mv "$2.next" "$2"
}

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
    if ($5 = "") { return 400; }
    if (\$request_uri !~ ^/) { return 400; }
    return 302 https://${5}${4}\$request_uri;
}
NGINX
    fi
}

umask 077
if [ "${LAB_HTTP_TEST:-false}" = false ]; then
    write_names /run/public-tls /tmp/public-names.map
    write_names /run/tls /tmp/admin-names.map
fi
# 續期與回復都先更新允許轉址的名稱，再由維護腳本平滑重載既有 master。
if [ "${1:-}" = refresh-redirects ]; then exit 0; fi
write_transport /tmp/public-transport.conf "$public_listener" /run/public-tls "" '$public_redirect_host'
write_transport /tmp/admin-transport.conf "$admin_port" /run/tls ":$admin_port" '$admin_redirect_host'
export PUBLIC_LISTEN_PORT ACME_STREAM_INCLUDE HTTPS_REDIRECT_INCLUDE
# 僅替換模式指定的設定，不展開 Nginx 的 $host、$uri 等變數。
envsubst '${PUBLIC_LISTEN_PORT} ${ACME_STREAM_INCLUDE} ${HTTPS_REDIRECT_INCLUDE}' < /etc/lab/nginx.conf.template > /tmp/nginx.conf
nginx -t -c /tmp/nginx.conf
if [ "${1:-}" = check-config ]; then exit 0; fi
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
