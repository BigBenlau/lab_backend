#!/bin/sh
# 單一部署入口：只呼叫 Docker，不安裝套件、不改主機 Python／systemd。
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LAB_HTTP_MODE=false
if [ "${1:-}" = http ]; then
    # 顯式選擇官網及管理 HTTP 入口；不把測試模式寫進正式 .env。
    LAB_HTTP_MODE=true
    shift
fi

check_context() {
    # 所有映像共用根目錄白名單；缺少時停止，避免把機密或初始化內容送入builder。
    if [ ! -r "$PROJECT_ROOT/.dockerignore" ] || [ ! -s "$PROJECT_ROOT/.dockerignore" ]; then
        echo '根目錄 .dockerignore 缺失、空白或不可讀；請同步完整專案建置設定。' >&2
        return 1
    fi
}

check_web() {
    # 共用gateway建置前檢查頁面與設定；只讀檔案狀態，不讀初始化資料。
    check_context
    for web_file in deploy/Dockerfile.gateway deploy/nginx.conf.template deploy/gateway-entrypoint.sh \
        admin_frontend/admin/index.html admin_frontend/admin/app.js admin_frontend/admin/style.css \
        public_frontend/dist/lab.html public_frontend/dist/lab.js public_frontend/dist/api.js \
        public_frontend/dist/lab.css public_frontend/dist/gold.css; do
        if [ ! -r "$PROJECT_ROOT/$web_file" ] || [ ! -s "$PROJECT_ROOT/$web_file" ]; then
            echo "入口建置檔案缺失、空白或不可讀：$web_file" >&2
            echo '請同步完整 public_frontend 程式；若從 Git 取得，確認它已由舊 gitlink 轉為一般檔案。詳見 docs/OPERATIONS.md。' >&2
            return 1
        fi
    done
}

check_build() {
    # 未指定服務代表建置全部；只建後端時不要求官網檔案存在。
    check_context
    web_build=false
    named_service=false
    for build_arg in "$@"; do
        case "$build_arg" in
            gateway) web_build=true; named_service=true ;;
            public-api|admin-api|tools) named_service=true ;;
        esac
    done
    if [ "$web_build" = true ] || [ "$named_service" = false ]; then
        check_web
    fi
}

compose() {
    # 固定已使用的專案名稱／volume；不讀取.env，避免舊Host或端口繼續影響部署。
    if [ "$LAB_HTTP_MODE" = true ]; then
        set -- -f "$PROJECT_ROOT/deploy/compose.http.yml" "$@"
    fi
    docker compose --project-name lab-backend --env-file /dev/null -f "$PROJECT_ROOT/deploy/compose.yml" "$@"
}

case "${1:-help}" in
    check-web)
        check_web
        echo '官網建置檔案完整；未呼叫 Docker。'
        ;;
    build)
        check_build "$@"
        compose "$@"
        ;;
    init)
        # init 拒絕非空 volume；失敗即停止，不自動清空資料或更改既有帳戶。
        compose run --rm --no-deps tools python -m lab_backend.cli init
        compose run --rm --no-deps tools python -m lab_backend.cli create-admin
        ;;
    cli)
        shift
        compose run --rm --no-deps tools python -m lab_backend.cli "$@"
        ;;
    seed)
        shift
        # 只在明確執行時掛來源唯讀，不把 data/assets 加入任何映像或常駐服務。
        compose run --rm --no-deps \
            --volume "$PROJECT_ROOT/public_frontend/dist:/seed:ro" \
            tools python scripts/prepare_initial_data.py \
            --source /seed --output /data/private/initial "$@"
        ;;
    maintenance)
        shift
        compose run --rm --no-deps tools python scripts/maintenance.py "$@"
        ;;
    watch)
        # 可選監測容器；不掛 Docker socket，也不需要主機排程。
        compose --profile maintenance up -d --no-deps tools
        ;;
    admin-info)
        # 僅列實際映射、監聽與登入模式，不列整份環境或帳戶資料。
        compose ps gateway admin-api
        compose exec -T gateway sh -c '
            printf "gateway_http_test=%s\n" "$LAB_HTTP_TEST"
            cat /tmp/admin-transport.conf
        '
        compose exec -T admin-api python -c 'from lab_backend.config import Config; c = Config.load(); print("api_http_test=" + str(c.http_test)); print("origin_scheme=" + ("https" if c.secure_cookie else "http")); print("session_cookie=" + c.session_cookie)'
        ;;
    help)
        echo '用法：sh deploy/lab.sh build --pull | init | up -d | ps | logs'
        echo 'HTTP 測試：sh deploy/lab.sh http <命令>；官網預設80，管理預設27181，詳見 docs/OPERATIONS.md。'
        echo '管理入口核對：admin-info（只列執行中的端口、模式與Cookie名稱；不需要.env）'
        echo '建置前檢查：check-web（只檢查官網程式檔案，不呼叫 Docker）'
        echo '首次內容：seed（預覽）| seed --apply（建立 CSV 與檔案 bundle）'
        echo '維護：cli <命令> | maintenance <命令> | watch'
        echo '其餘參數原樣交給 docker compose；停止容器用 down，勿用 down -v 刪資料。'
        ;;
    *) compose "$@" ;;
esac
