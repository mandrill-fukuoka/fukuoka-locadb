# Web画面（heteml）

配置（GitHub Actions「deploy-web」が自動で行う）
```
web/locadb/          ← web/public/ の中身
web/locadb/_app/     ← web/app/lib.php と .htaccess（外から読めない）。config.php はサーバーにだけ置く
```
config.php の項目は app/config.sample.php を参照。ログイン用パスワードのハッシュは `php tools/make_hash.php`（手元）か
app/make_password.php で作る。

ページ
- index.php 場所を探す ／ spot.php 地点の詳細 ／ flow.php 流れで探す ／ flows.php おすすめの流れ ／ list.php ロケハンリスト
- ログイン後：spots.php 地点の確認（要確認・未確認・手入力・人が直した・重複の疑い・対象外）／ risks.php 変化リスク ／ runs.php 収集の記録 ／ edit.php 地点の追加・修正（地図で位置を指定）
