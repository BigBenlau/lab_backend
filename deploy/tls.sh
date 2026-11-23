#!/bin/bash
# 在 SAS 主機協調 Docker 與兩個唯讀 TLS 掛載。ACME 帳戶和站點私鑰只存私人目錄。
set -euo pipefail
umask 077
state=/var/lib/lab-tls
image=goacme/lego:v5.6.0
validator=lab-tls-validation
trust=/etc/ssl/certs/ca-certificates.crt
die() { echo "$*" >&2; exit 1; }
[[ $EUID == 0 ]] || die '請以部署管理權限執行'
cd "$state"
exec 9>lock
flock -w 10 9 || die '另一個 TLS 維護任務正在執行'
project=$(<project)
cd "$project"
[[ -f deploy/compose.yml ]] || die '找不到專案 Compose 設定'
lab() { sh deploy/lab.sh "$@"; }

# 先檢查完整設定和兩組 PEM，再讓舊 worker 完成手上的連線。
reload_gateway() {
  local attempt
  for attempt in {1..10}; do
    if lab exec -T gateway sh -c 'test -s /tmp/nginx.pid && nginx -t -c /tmp/nginx.conf && nginx -s reload -c /tmp/nginx.conf'; then return 0; fi
    sleep 1
  done
  return 1
}

# 私鑰暫存於同一個私有目錄。rename 同時讓 Nginx 在 reload 時看見新 inode。
replace_pem() {
  local source=$1 target=$2 staging=${2%/*}/.lab-tls-next
  install -o 10001 -g 10001 -m 0600 -- "$source" "$staging"
  mv -fT -- "$staging" "$target"
}

# pending 記錄跨兩個目錄的安裝進度，讓中斷後的排程能一起還原。
pending=$state/pending
dirs=(secrets/public-tls secrets/tls)
for dir in "${dirs[@]}"; do
  for path in secrets "$dir" "$dir/cert.pem" "$dir/key.pem"; do
    [[ ! -L $path ]] || die 'TLS 安裝目錄及 PEM 必須是實體路徑'
  done
done
recover() (
  set -e
  if [[ $(docker inspect -f '{{index .Config.Labels "lab.tls"}}' "$validator" 2>/dev/null) == validation ]]; then
    docker rm -f "$validator" >/dev/null
  fi
  if [[ -f $pending/preparing ]]; then
    # 驗證尚未動過線上 PEM 或模式，不必觸碰已在服務的 Nginx。
    rm -rf -- "$pending"
    exit 0
  fi
  for dir in "${dirs[@]}"; do
    for file in cert.pem key.pem; do
      old="$pending/${dir##*/}-$file"
      if [[ -f $old ]]; then
        replace_pem "$old" "$dir/$file"
      else
        rm -f -- "$dir/$file"
      fi
    done
    rm -f -- "$dir/.lab-tls-next"
  done
  if [[ -f $pending/active ]]; then
    cp "$pending/active" "$state/active.tmp"
    mv "$state/active.tmp" "$state/active"
  else
    rm -f "$state/active"
  fi
  if [[ -f $pending/was-running ]]; then
    # 相容舊腳本因停站後中斷而留下的 pending。
    if [[ -z $(lab ps --status running -q gateway) ]]; then lab start gateway; fi
    reload_gateway
  elif [[ -f $pending/reload ]]; then
    reload_gateway
  fi
  rm -rf -- "$state/restored"
  mv "$pending" "$state/restored"
)
if [[ -d $pending ]]; then
  echo '恢復上一次中斷的 TLS 操作' >&2
  recover
fi
mode=${1:-}
if [[ $mode == renew ]]; then
  [[ -f $state/active ]] || die '請先執行 lab-tls sites 安裝網站憑證'
  mode=$(<"$state/active")
fi
case "$mode" in sites|ip|domain|dual) ;; *) die '用法：lab-tls sites | renew（舊部署亦支援 ip、domain、dual）' ;; esac
ip=''
identities=()
days=14
if [[ $mode == sites ]]; then
  identities=(casino.ye.wang crab.ye.wang)
  # 獨立儲存名稱，切換失敗時舊模式仍能使用原有憑證續期。
  cert_name=lab-sites
elif [[ $mode != ip ]]; then
  domain=$(<"$state/domain")
  [[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+$ ]] || die '請填寫完整域名'
  identities+=("$domain")
  cert_name=$domain
fi
if [[ $mode == ip || $mode == dual ]]; then
  ip=$(<"$state/ip")
  [[ $ip =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die '請填寫 SAS 的公網 IPv4'
  IFS=. read -ra octets <<< "$ip"
  for part in "${octets[@]}"; do
    [[ $part == 0 || $part != 0* ]] && (( 10#$part <= 255 )) || die 'IPv4 各段須為 0 至 255'
  done
  days=3
  identities+=("$ip")
  if [[ $mode == ip ]]; then cert_name=$ip; else cert_name=lab-dual; fi
fi
request=()
for name in "${identities[@]}"; do request+=(--domains "$name"); done
if [[ -n $ip ]]; then request+=(--profile shortlived); fi
if [[ $mode == dual || $mode == sites ]]; then
  request+=(--cert.name "$cert_name" --force-cert-domains)
fi
email=$(<"$state/email")
cert="$state/lego/certificates/$cert_name.crt"
key="$state/lego/certificates/$cert_name.key"
window=$((days * 86400))
install -d -m 0700 -o 10001 -g 10001 "$state/lego"

# 核對實際送出的葉憑證、身份、信任鏈。連到本機映射，公網防火牆另從外部核對。
fingerprint() { openssl x509 "$@" -noout -fingerprint -sha256; }
served_identity() {
  local port=$1 expected=$2 seen
  shift 2
  # 讀完整握手回應，僅把公開憑證交給 x509。
  seen=$(timeout 8 openssl s_client -connect "127.0.0.1:$port" "$@" \
    -verify_return_error -CAfile "$trust" </dev/null 2>/dev/null |
    sed -n '/^-----BEGIN CERTIFICATE-----$/,/^-----END CERTIFICATE-----$/p' |
    fingerprint) || return 1
  [[ $seen == "$expected" ]]
}
serving() {
  local expected port name
  expected=$(fingerprint -in "$cert") || return 1
  for port in 443 27182; do
    # 每個網址在兩個入口都要送出同一張憑證。只有舊模式的裸 IP 不傳 SNI。
    for name in "${identities[@]}"; do
      if [[ $name == "$ip" ]]; then
        served_identity "$port" "$expected" -noservername -verify_ip "$ip" || return 1
      else
        served_identity "$port" "$expected" -servername "$name" -verify_hostname "$name" || return 1
      fi
    done
  done
}
verify_identities() {
  # 分別檢查 DNS 與 IP，快取、簽發後及到期窗口一律套用同一組身份規則。
  local name actual expected flag
  local verify=(openssl verify "$@" -purpose sslserver -CAfile "$trust" -untrusted "$cert")
  if [[ $mode == sites ]]; then
    # verify_hostname 也接受萬用字元。正式憑證的 SAN 須精確列出兩個網站，可任意排序。
    actual=$(openssl x509 -in "$cert" -noout -ext subjectAltName | sed '1d' | tr ',' '\n' |
      sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | LC_ALL=C sort) || return 1
    expected=$(printf 'DNS:%s\n' "${identities[@]}" | LC_ALL=C sort) || return 1
    [[ $actual == "$expected" ]] || return 1
  fi
  for name in "${identities[@]}"; do
    flag=-verify_hostname
    if [[ $name == "$ip" ]]; then flag=-verify_ip; fi
    "${verify[@]}" "$flag" "$name" "$cert" || return 1
  done
}
fresh() {
  [[ -s $cert && ! -L $cert ]] || return 1
  # 在續期門檻的未來時間驗證整條鏈，直接用 verify 的退出碼判斷。
  local deadline
  deadline=$(( $(date +%s) + window ))
  verify_identities -attime "$deadline" >/dev/null 2>&1
}
verify_pair() {
  [[ -f $cert && ! -L $cert && -f $key && ! -L $key ]] || die '憑證及私鑰必須為普通檔案'
  verify_identities
  openssl x509 -in "$cert" -noout -pubkey > "$tmp/cert.pub"
  openssl pkey -in "$key" -pubout > "$tmp/key.pub"
  cmp -s "$tmp/cert.pub" "$tmp/key.pub"
}

running=$(lab ps --status running -q gateway)
if [[ -n $running ]]; then
  lab exec -T gateway sh -c 'test "$LAB_HTTP_TEST" = false' || die '先完成 HTTP 測試入口切換'
  lab exec -T gateway grep -Fxq 'include /etc/lab/acme-stream.conf;' /tmp/nginx.conf ||
    die '請先依部署手冊更新支援線上續期的 gateway'
elif [[ ( $mode != sites && $mode != ip ) || ${1:-} == renew || ( -e $state/active && $(<"$state/active") != "$mode" ) ]]; then
  die 'gateway 未運行，請檢查服務'
fi

tmp=$(mktemp -d "$state/work.XXXXXXXX")
finish() {
  rc=$?
  trap - EXIT HUP INT TERM
  rm -rf -- "$tmp"
  if [[ -d $pending ]]; then
    # 還原失敗會以非零狀態退出，保留 pending 供人工或下次重試。
    recover
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 1' HUP INT TERM

issue=true
if fresh; then
  verify_pair
  issue=false
  if [[ -n $running && -f $state/active && $(<"$state/active") == "$mode" ]] &&
     cmp -s "$cert" secrets/public-tls/cert.pem && cmp -s "$key" secrets/public-tls/key.pem &&
     cmp -s "$cert" secrets/tls/cert.pem && cmp -s "$key" secrets/tls/key.pem && serving; then
    echo '兩個端口已使用有效版本'
    exit 0
  fi
else
  docker image inspect "$image" >/dev/null || die '請先下載手冊指定的 lego 映像'
fi

mkdir "$tmp/backup"
for dir in "${dirs[@]}"; do
  install -d -o 10001 -g 10001 -m 0700 "$dir"
  for file in cert.pem key.pem; do
    if [[ -f $dir/$file ]]; then cp -p -- "$dir/$file" "$tmp/backup/${dir##*/}-$file"; fi
  done
done
if [[ -f $state/active ]]; then cp "$state/active" "$tmp/backup/active"; fi
touch "$tmp/backup/preparing"
if [[ -n $running ]]; then touch "$tmp/backup/reload"; fi
mv "$tmp/backup" "$pending"

if "$issue"; then
  # 網站上線後由 gateway 分流。首次上線前才由 lego 直接綁定公網 443。
  if [[ -n $running ]]; then
    network=(--network lab-backend_edge)
  else
    network=(-p 0.0.0.0:443:8443)
  fi
  timeout -k 15s 5m docker run --rm --name "$validator" --label lab.tls=validation \
    --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user 10001:10001 --tmpfs /tmp:rw,nosuid,nodev,size=16m \
    "${network[@]}" --mount "type=bind,src=$state/lego,dst=/state" \
    "$image" run --accept-tos --email "$email" --path /state "${request[@]}" \
    --tls --tls.address :8443 --ari-disable --renew-days "$((days + 1))" --no-random-sleep
  fresh || die '未取得新的有效期，保留上次部署'
  verify_pair
fi

rm -- "$pending/preparing"
for dir in "${dirs[@]}"; do
  replace_pem "$cert" "$dir/cert.pem"
  replace_pem "$key" "$dir/key.pem"
done
if [[ -n $running ]]; then
  reload_gateway
  for attempt in {1..10}; do
    if serving; then break; fi
    sleep 1
  done
  serving || die '兩端口握手或指紋檢查失敗'
fi
printf '%s\n' "$mode" > "$state/active.tmp"
mv "$state/active.tmp" "$state/active"
# 先保留上一套，最後一次 rename 表示本次提交成功。
rm -rf -- "$state/previous"
mv "$pending" "$state/previous"
echo "已安裝 $mode 憑證"
