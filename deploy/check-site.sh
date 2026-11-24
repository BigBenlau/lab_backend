#!/bin/sh
# 從管理員獲准的外網執行。curl 使用正常 DNS、憑證名稱與系統信任鏈。
set -eu
[ "$#" = 1 ] || { echo '用法：sh deploy/check-site.sh SAS公網IPv4' >&2; exit 2; }
case "$1" in ''|*[!0-9.]*) echo '請提供 IPv4' >&2; exit 2;; esac
check() {
    actual=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 12 -o /dev/null -w '%{http_code} %{redirect_url}' "$1")
    if [ "$actual" != "$2" ]; then
        echo "失敗 $1（收到 $actual，預期 $2）" >&2
        exit 1
    fi
}
for host in casino.ye.wang crab.ye.wang ye.wang "$1"; do
    path='/api/home?check=1'
    check "http://$host$path" "301 https://$host$path"
    check "http://$host:443$path" "302 https://$host$path"
    check "http://$host:27182/" "302 https://$host:27182/"
    check "https://$host/" '200 '
    check "https://$host$path" '200 '
    check "https://$host:27182/" '200 '
    check "https://$host:27182/api/v1/auth/session" '401 '
    echo "$host 通過"
done
