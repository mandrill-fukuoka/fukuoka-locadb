#!/usr/bin/env bash
# バックアップ（pg_dump）の暗号化・復号。パスフレーズは環境変数 BACKUP_PASSPHRASE（GitHub Secrets）から読み、ログに出さない。
#   tools/backup_crypt.sh check            … パスフレーズが登録されているか確かめる（未登録なら失敗。値は出さない）
#   tools/backup_crypt.sh encrypt FILE     … FILE を AES256 で暗号化して FILE.gpg を作り、元の FILE を消す
#   tools/backup_crypt.sh decrypt FILE.gpg OUT … 復号して OUT に書く（復元の前に使う）
set -euo pipefail
cmd="${1:-}"
need_pass() {
  if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
    echo "::error::Secrets の BACKUP_PASSPHRASE が未登録です。暗号化できないので、バックアップも本番への変更も行いません。" >&2
    exit 1
  fi
}
case "$cmd" in
  check)
    need_pass
    echo "BACKUP_PASSPHRASE：設定あり（${#BACKUP_PASSPHRASE}文字）"
    ;;
  encrypt)
    need_pass
    f="${2:?FILE を指定}"
    printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 \
      --symmetric --cipher-algo AES256 -o "$f.gpg" "$f"
    rm -f "$f"
    ;;
  decrypt)
    need_pass
    f="${2:?FILE.gpg を指定}"; out="${3:?出力先を指定}"
    printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 -d -o "$out" "$f"
    ;;
  *) echo "使い方: $0 check | encrypt FILE | decrypt FILE.gpg OUT" >&2; exit 2 ;;
esac
