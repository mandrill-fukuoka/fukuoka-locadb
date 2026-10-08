# 福岡ロケーションデータベース（fukuoka-locadb）

脚本のシーン要件から、福岡のロケ地候補と「ロケーションクラスター」（5〜15分圏の景観の組み合わせ）を逆引きするためのDBと自動化パイプライン。

## 構成
| 処理 | ファイル | 自動/人 |
|---|---|---|
| 1. 候補収集（OpenStreetMap） | `locadb/collect_osm.py` | 自動 |
| 2. 重複統合 → 地点登録 | `locadb/merge.py` | 自動 |
| 3. 種別・時代感・雰囲気タグ推定（Claude API） | `locadb/classify.py` | 自動（AI推定として記録） |
| 4. クラスター生成・切り替わりスコア | `locadb/clustering.py`, `build_clusters.py` | 自動 |
| 5. 変化リスク監視（公式ページの更新検知） | `locadb/monitor_risk.py` | 自動（要確認として記録） |
| 6. シーン検索 | `locadb/search_scene.py` | 自動 |
| ロケハン・許可確認・確度更新 | Supabase管理画面 / ビュー | **人** |

## セットアップ（Supabase）
1. Supabaseで新規プロジェクトを作成
2. SQL Editor で `sql/001_schema.sql` → `002_seed.sql` → `003_views.sql` → `004_api.sql` の順に実行
3. ローカルで:
   ```
   pip install -r requirements.txt
   cp .env.example .env   # DATABASE_URL と ANTHROPIC_API_KEY を記入
   python -m locadb.pipeline --area fuk-chuo      # まず中央区
   python -m locadb.search_scene "30代の男性が深夜、一人で歩く。都会だが少し寂しい。歩いているうちに海が見える"
   ```
4. 定期実行: GitHubに非公開リポジトリを作り、Secretsに `DATABASE_URL` `ANTHROPIC_API_KEY` `SUPABASE_URL` `SUPABASE_SERVICE_KEY` を登録
   - `weekly.yml`：毎週月曜に全7区を更新し、DBのバックアップを90日保存
   - `keepalive.yml`：木・土に接続し、無料枠の一時停止を防止
5. Web画面: `web/README.md` の手順でヘテムルに設置

## 確認作業（人）
- `v_review_queue` … AI分類済みで未確認の地点（地図リンク付き）
- `v_clusters` … 生成されたクラスター（スコア順）
- `v_risks_to_check` … 変化リスク「要確認」一覧
- 現地確認したら `spots.confidence='field_checked'`、`status='verified'` に更新。人が付けたタグは `spot_tags.origin='human'`（AI再実行で上書きされない）

## データの信頼性ルール
- `confidence`: unverified → ai_estimated → desk_checked → field_checked
- 変化リスクは自動では「確認済み」にしない（`verified=false` = 要確認）
- 検索結果は組み合わせの提案であり、撮影可能性を保証しない

## 今後の拡張
- 福岡市オープンデータ（公共施設・公園等）の取り込み → `raw_candidates` に `source='opendata'` で追加
- FFC掲載地との照合（利用規約の確認後）
- 実ルート所要時間（OSRM等）への置き換え：`clustering.travel_min()`
- 自社撮影写真の登録（`spot_photos`）と画像からのタグ推定
- 都市圏・県内への拡張：`collect_osm.AREA_NAMES` と `areas` に追加

## 注意
- OSMデータは ODbL。出典（`sources` テーブル）を保持し、公開時は "© OpenStreetMap contributors" を表記
- Googleマップ/ストリートビューの画像・データはDBに保存しない
- Overpass APIは公共サービスのため、収集は週1回程度・区単位で実行
