#!/bin/sh
# 使い方: ./run.sh <php ファイル> [引数...]
# キーは ~/laya/.env（SUPABASE_URL / SUPABASE_KEY）から読む。リポジトリ・ログ・チャットには出さない。
set -a
. "${LAYA_ENV:-$HOME/laya/.env}"
set +a
exec php "$@"
