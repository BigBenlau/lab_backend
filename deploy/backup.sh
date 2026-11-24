#!/bin/sh
# 主機只負責排程、私密檔及 volume。所有資料處理仍在非 root 容器內完成。
set -eu
umask 077
state=/var/lib/lab-tls
versions=/var/backups/lab/versions
[ "$(id -u)" = 0 ] || { echo '請用 root 執行' >&2; exit 1; }
if [ "${1:-}" = run ]; then
    # 即使缺密碼、缺專案路徑或另一項操作佔鎖，cron 也能留下失敗紀錄。
    trap 'rc=$?; if [ "$rc" = 0 ]; then logger -t lab-backup OK; else logger -p daemon.err -t lab-backup FAILED; fi' EXIT
fi
cd -- "$(cat "$state/project")"
# 不讓另一次 cron 或切換動作同時處理資料卷。
exec 9>"$state/backup.lock"
flock -w 60 9
case "${1:-}" in
    run)
        [ "$#" = 1 ] || exit 2
        sh deploy/lab.sh snapshot
        ;;
    verify|rebuild)
        [ "$#" = 2 ] || { echo '請指定一個備份版本' >&2; exit 2; }
        case "$2" in ''|.*|*[!a-zA-Z0-9_-]*) echo '版本名稱無效' >&2; exit 2;; esac
        bundle=$versions/$2
        [ -d "$bundle" ] && [ ! -L "$bundle" ] || exit 1
        for file in site.json database.enc public.zip; do
            [ -f "$bundle/$file" ] && [ ! -L "$bundle/$file" ] || { echo '版本檔案不齊' >&2; exit 1; }
        done
        # 使用全新的本機卷演練。只清理本次建立的暫存卷，永遠不碰線上卷。
        target=lab-restore-$(cat /proc/sys/kernel/random/uuid)
        docker volume create --driver local "$target" >/dev/null
        keep=false
        trap 'if [ "$keep" = false ]; then docker volume rm "$target" >/dev/null; fi' EXIT
        mode=$1
        set -- python scripts/maintenance.py rebuild /input/public.zip /input/database.enc --site /input/site.json --expected-version "$2"
        if [ "$mode" = rebuild ]; then set -- "$@" --apply; fi
        docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
            --user 10001:10001 --env LAB_STORAGE=/data --mount "type=volume,src=$target,dst=/data" \
            --mount "type=bind,src=$bundle,dst=/input,readonly" lab-api:demo "$@" < "$state/backup-password"
        if [ "$mode" = rebuild ]; then
            keep=true
            echo "volume=$target"
        fi
        ;;
    activate)
        [ "$#" = 2 ] || exit 2
        case "$2" in *[!a-z0-9_-]*|'') exit 2;; lab-restore-?*|lab-backend_lab_data) ;; *) exit 2;; esac
        lab() { sh deploy/lab.sh "$@"; }
        # 與自動續期互斥；備份和切卷亦不能交錯。
        exec 8>"$state/lock"
        flock -w 360 8
        pointer=secrets/active-volume
        previous=lab-backend_lab_data
        if [ -e "$pointer" ]; then previous=$(cat "$pointer"); fi
        [ "$previous" != "$2" ] || { echo '已選用此資料卷'; exit 0; }
        docker volume inspect "$2" >/dev/null
        docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
            --user 10001:10001 --env LAB_STORAGE=/data --mount "type=volume,src=$2,dst=/data,readonly" \
            lab-api:demo python scripts/maintenance.py check-files /data/public
        select_volume() {
            printf '%s\n' "$1" > "$pointer.next" && mv -f -- "$pointer.next" "$pointer"
        }
        start_site() {
            lab up -d --wait --wait-timeout 100 --force-recreate public-api admin-api gateway &&
            lab exec -T gateway nginx -t -c /tmp/nginx.conf &&
            lab exec -T public-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)" &&
            lab exec -T admin-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)"
        }
        watch=$(lab ps --status running -q tools)
        printf '%s\n' "$previous" > secrets/previous-volume
        if lab stop tools gateway admin-api public-api && select_volume "$2" && start_site; then
            if [ -n "$watch" ]; then lab watch; fi
            echo "已啟用 $2；原資料卷 $previous 已保留"
        else
            echo '新卷啟動失敗，正在切回先前資料卷' >&2
            select_volume "$previous"
            start_site
            if [ -n "$watch" ]; then lab watch; fi
            exit 1
        fi
        ;;
    *) echo '用法：lab-backup run | verify 版本 | rebuild 版本 | activate 卷名' >&2; exit 2;;
esac
