# データのライセンスと出典

プログラムのコードは MIT ライセンスです（LICENSE）。
このリポジトリに含まれる次のデータは、MIT ではなく、それぞれの元のライセンスに従います。

| ファイル | 内容 | ライセンス・出典 |
| --- | --- | --- |
| `data/facts/*.json`（`source_kind` が `wikipedia` の行） | Wikipedia の記事から抜き出した事実と、その根拠の原文（`quote`） | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ja)。各行の `source_title`（記事名）と `source_url` が出典 |
| `tools/laya/knowledge_wiki.php` | Wikipedia の記事から抜き出した説明文の材料 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ja)。出典は日本語版 Wikipedia の各記事（地点ごとのコメントに地点名） |
| `data/facts/*.json`（`source_kind` が `official` の行）、`tools/laya/knowledge_web.php` | 自治体・運営主体の公式ページから読み取った事実（数値・年など）。原文は含まない | 各行の `source_url` が出典 |

これらのファイルを再利用するときは、出典を表示し、Wikipedia 由来の部分は同じ CC BY-SA 4.0 で公開してください。

## このリポジトリに含まれないデータ

地点のデータ本体はデータベースにあり、このリポジトリには含まれません。

- 地点の位置・名前など：© OpenStreetMap contributors（[ODbL](https://www.openstreetmap.org/copyright)）
- 住所：国土地理院 逆ジオコーダー
- 標高・地形：国土地理院 標高タイル

## 注意

FUKUOKA LOCATIONS の情報は撮影の手がかりです。撮影の許可・立入の可否・撮影できることを保証するものではありません。
