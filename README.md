# FUKUOKA LOCATIONS（福岡ロケーションデータベース）v2

脚本のシーン要件から、福岡のロケ地候補と「景観が切り替わる組み合わせ（流れ）」を逆引きするWebデータベース。
画面は heteml の PHP、データは Supabase（PostgreSQL＋PostGIS）、収集と反映は GitHub Actions。

## v2 で変わったこと（2026-10）

| | 旧版 | v2 |
|---|---|---|
| OSMの取得 | Overpass API に区×種別で約200回問い合わせ。混雑時の空応答を「0件」と誤認 | Geofabrik の九州全体ファイルを1回取得し MD5 で照合。全部取れたか失敗かのどちらか |
| 地点の更新 | 一度登録したら位置・名前・区は更新しない | 未確認の地点は最新に更新。人が確認した地点は自動で変えず「要確認」 |
| 川・線路など | 線上の何番目か（@0, @1…）で識別。線が変わると別の場所を指す | 格子に固定した位置で識別。延長・短縮・分割・向きの反転・ID変更でも同じ地点 |
| 位置 | 川・線路の中心線の上 | 近くの歩ける道（川沿いの道・橋など）に寄せる |
| 消えた地点 | 消さない・気づかない | 2回続けて見つからなければ対象外（理由つき）。OSMに戻れば自動で戻す。確認済みは要確認 |
| 区 | 最初に収集した区のまま | 区の境界（ポリゴン）で位置から判定 |
| 記録 | なし | すべての変化を spot_history に。収集ごとに差分レポート（data_runs） |
| 安全装置 | なし | 試し実行（dry_run）、異常な減少の検知、大量の対象外で停止、反映前のバックアップ |
| 住所 | 空 | 国土地理院の逆ジオコーダーで町丁目まで |
| 画面 | 場所を探す・流れで探す・おすすめの流れ | ＋地点の詳細（共有URL・ストリートビュー・光の目安・相談先の目安）、ロケハンリスト（CSV・ルート・共有）、収集の記録 |

## 地点の手入力（2026-10 タスクA）

- OSMにない場所は、ログインして「＋地点を追加」（web/public/edit.php）から地図で位置を指定して追加する。手入力の地点（origin=manual、コード FUK-<区>-M0001）は自動収集では動かず、消えない
- OSMの地点も、地点ページの「位置・名前・種別を直す」から直せる。直すと位置固定（locked）になり、自動では変わらない
- 人が確認・修正した地点（保護された地点）は、収集が「前回見たOSMの値」（spots.osm_seen）と比べ、OSMが変わったときだけ要確認にする。要確認を解消した地点や、人が直した地点が毎週ぶり返すことはない
- 撮影実務（許可の要否・窓口・制約・撮影実績・メモ）は地点ページで入力する（spot_practicals）。写真は未対応（保存先は heteml の予定）
- 関数は sql/api/30_manual.sql、表の変更は sql/migrations/110_manual_spots.sql

## 地形・標高（2026-10 タスクF）

- 自動収集の一工程（locadb/terrain.py）。国土地理院の標高タイル（PNG）から、地点ごとに中心と周囲16点を読む
  - elevation_m 標高（m）／ relief_m 高台度＝標高 − 半径150mの8方向の平均 ／ slope_pct 最大の傾き＝半径30mの8方向で最も急な勾配（%）
  - データは 5m・レーザ（dem5a）→ 5m・写真測量（dem5b）→ 10m（dem10b）の順。中心に値がない（海上など）は terrain_status=no_data
  - 対象は未取得の地点と、取得後に動いた地点（手入力の地点も含む）。全件の取り直しは `python -m locadb.terrain --all`
  - 建物を除いた地面の高さ。ビルの屋上・展望台の高さは出ない。出典「国土地理院 標高タイル」を地点ページに表示
- 初回・臨時の取得は Actions「pipeline」の mode=terrain_only（地形だけ）
- ビュー v_fukuoka_spots（別ラインのAI・LAYAが service_role で読む）は sql/api/40_terrain.sql で毎回作り直す。先頭6列は変えない。対象は掲載中の地点すべて
- 表の変更は sql/migrations/160_terrain.sql（旧方式で SQL Editor から作った elevation_m を引き継ぎ、-1 は空に戻した）
- 坂道（locadb/slopes.py）：収集の中で、OSMの道路・階段に沿って10mごとに標高を読み、50m区間の勾配が10%以上で50m以上続く区間（階段は高低差5m以上）を種別「坂道・階段坂」（景観「坂道・高低差」）の地点にする。同じ種類・名前でつながる道は1本にしてから測り、近い坂（60m以内）は1つにまとめる。キーは位置の格子（80m）。測定値は slope_len_m / slope_grade_pct / slope_rise_m / slope_bearing / slope_kind（DB変更162）
  - 試し実行では常に検出し、候補の一覧 slopes.csv（Actions の成果物 pipeline-log）を出す。本番で登録するのは slopes.ENABLED_IN_APPLY が True のときだけ（候補の確認が済むまで False）
  - 標高タイルが1枚でも取れなかった回は、坂道を消失扱いにしない
- 場所を探すに「地形：高台（周りより10m以上高い）」の絞り込み（search_spots の p_relief_min）

## 構成

```
locadb/categories.py   種別と、OSMタグから種別を決める規則（収集条件はここだけ）
locadb/osm_source.py   OSMファイルの取得・切り出し・読み取り（点・面・線、区の境界、立てる位置への寄せ）
locadb/sync.py         読み取った結果をDBに反映（履歴・要確認・対象外・付け替え・異常検知・レポート）
locadb/pipeline.py     一括実行（--dry-run / --apply）
locadb/address.py      住所（国土地理院）
locadb/terrain.py      地形（標高・高台度・傾き。国土地理院 標高タイル）
locadb/slopes.py       坂道の検出（道路×標高）
locadb/build_clusters.py, clustering.py   流れの自動生成
locadb/classify.py     AI分類（ANTHROPIC_API_KEY があるときだけ）
locadb/monitor_risk.py 変化リスク監視（config/watch_pages.txt）
sql/migrations/        表の作成・変更（1回だけ。schema_migrations に記録）
sql/api/               画面から呼ぶ関数（毎回作り直す。30_manual.sql は手入力・修正・撮影実務、40_terrain.sql はビュー v_fukuoka_spots）
sql/grants.sql         権限・RLS（毎回）
sql/legacy/            旧版のSQL（参照用。実行しない）
tools/migrate.py       DBの変更を適用
tools/import_legacy.py 旧版で人が確認した地点・保存した流れを引き継ぐ
tests/                 回帰テスト（架空の地図で、取得失敗・線の変化・履歴・保護・橋・検索を確認）
web/                   画面（public → heteml の web/locadb/、app/lib.php → web/locadb/_app/）
```

## GitHub Actions（すべて Actions タブ → 名前を選ぶ → Run workflow）

| 名前 | いつ | 何をする |
|---|---|---|
| pipeline | 毎週月曜6:00（本番）／手動 | 取得→反映→住所→地形→流れ→AI分類→変化リスク。手動は mode=dry_run（試し）か apply、terrain_only（地形だけ）。本番・地形だけはバックアップを先に取る |
| db-migrate | 手動 | DBの変更。status / dry_run / apply（バックアップしてから） |
| deploy-web | 手動 | 画面を heteml へ。probe（フォルダ確認だけ）/ deploy |
| diagnose | 手動 | データの診断（読み取りのみ） |
| ci | push のたび | 回帰テストとPHPの構文チェック |
| keepalive | 木・土 | Supabase 無料枠の休止防止 |

Secrets：`DATABASE_URL`、`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`、`FTP_HOST`、`FTP_USER`、`FTP_PASSWORD`（任意 `FTP_DIR`、`ANTHROPIC_API_KEY`）

## 反映が止まったとき

pipeline が「停止（反映せず）」になったら、Actions のログ末尾または画面「収集の記録」でレポートを読む。
- 「異常な減少」：OSM側で大量の削除・タグ変更があった可能性。内容が正しければ allow_large_change を付けて再実行
- 「対象外にする地点が多すぎる」：収集条件の変更などで大量に外れる。意図どおりなら allow_large_change で再実行

## 手元での確認

```
createdb locadb && DATABASE_URL=postgresql://.../locadb python tools/migrate.py
python -m locadb.pipeline --apply --pbf fukuoka.osm.pbf --skip-extras
TEST_PG=postgresql://user:pass@localhost:5432 pytest -q tests
```

## 注意

- 地点の位置は OpenStreetMap のデータに基づく手がかりで、撮影の許可・立入の可否・撮影できることを保証しない
- 移動時間は直線距離×1.3からの目安（実際の道のりの所要時間ではない）
- OSM データは ODbL。出典は地点ページと画面下部に表示
- 秘密情報（キー・パスワード）はチャットやリポジトリに置かない。config.php はサーバーにだけ置く
