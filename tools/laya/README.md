# tools/laya — 説明文の下書き（LAYA）の生成と送信

利用者のPCで動かす道具。ローカルの LLM（Ollama の qwen2.5:14b、費用ゼロ）が、**人が承認した事実だけ**から説明文の下書きを作る。
LAYA が書き込めるのは下書き（ai_submit）まで。採用は人が確認画面「説明文の下書き」でする（docs/laya_api.md）。

| ファイル | 役割 |
|---|---|
| batch_knowledge.php | 生成（ドライラン固定。ai_submit は送らない）。採用文を `laya_batch/adopted_日時.json` に保存。版名 `laya-summary-v10`。使い方：`./run.sh batch_knowledge.php 0 - id1,id2,…` |
| submit_drafts.php | 保存した文だけを ai_submit で送る。`--skip`、`--offset`、`--n`（200まで）、`--go` を付けたときだけ送る。**本番への書き込みなので、事前に了承を取る** |
| knowledge_wiki.php | 承認済みの材料（Wikipedia由来の所在・244地点）。人が採用したものだけ入れる |
| knowledge_location.php | 承認済みの材料（所在・259地点、先に承認したもの） |
| wiki_addr.php | 住所の突き合わせ用（本文に住所を書かせず、DBの住所に置き換えるため） |
| collect_wiki.py | OSMの wikipedia/wikidata タグから Wikipedia の候補文を集める。1秒間隔、並列化しない。利用者のPCで実行する |
| build_kw.py | 承認済みの候補から knowledge_wiki.php を作る |
| run.sh | `~/laya/.env`（SUPABASE_URL／SUPABASE_KEY）を読んで php を起動する |

## 守ること
- 材料は人が承認したものだけ。参考文献・出典の一覧、事故・死亡などの出来事、方角・距離の推測は書かせない。
- 同じ地点の新しい下書きは前の LAYA の下書きを置き換える（履歴は残る）。プロンプトを変えたら版名を変える。
- 最初の2〜3件を送って確認画面を見てから、全件に広げる。
- 既知の問題：モデルが「鉄」を崩して書く（`{鉄}` `{�铁}` など）。batch_knowledge.php が「鉄」に直す。島の標高ラベル（「周りより低い」）が矛盾する地点は送らない。
- `laya_batch/` は生成物なので入れない（.gitignore）。
