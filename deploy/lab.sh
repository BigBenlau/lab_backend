#!/bin/sh
# 單一部署入口：只呼叫 Docker，不安裝套件、不改主機 Python／systemd。
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

compose() {
    # 固定設定檔位置，不用 source 執行 .env 內容。
    docker compose --env-file "$PROJECT_ROOT/.env" -f "$PROJECT_ROOT/deploy/compose.yml" "$@"
}

case "${1:-help}" in
    init)
        # init 拒絕非空 volume；失敗即停止，不自動清空資料或更改既有帳戶。
        compose run --rm --no-deps tools python -m lab_backend.cli init
        compose run --rm --no-deps tools python -m lab_backend.cli create-admin
        ;;
    cli)
        shift
        compose run --rm --no-deps tools python -m lab_backend.cli "$@"
        ;;
    maintenance)
        shift
        compose run --rm --no-deps tools python scripts/maintenance.py "$@"
        ;;
    watch)
        # 可選監測容器；不掛 Docker socket，也不需要主機排程。
        compose --profile maintenance up -d --no-deps tools
        ;;
    help)
        echo '用法：sh deploy/lab.sh build --pull | init | up -d | ps | logs'
        echo '維護：cli <命令> | maintenance <命令> | watch'
        echo '其餘參數原樣交給 docker compose；停止容器用 down，勿用 down -v 刪資料。'
        ;;
    *) compose "$@" ;;
esac
