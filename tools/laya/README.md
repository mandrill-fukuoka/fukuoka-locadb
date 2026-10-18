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

## Web情報収集（試作・2026-10-18）
| ファイル | 役割 |
|---|---|
| collect_web.py | 公式ページから「事実の候補」を集める。許可リスト内のURLだけ読む。1秒間隔・直列・robots確認。手元のLLM（Ollama）が事実と原文の引用を抜き、引用が本文にあるか機械照合する（なければ捨てる）。DBには触れない。出力は collect_wiki.py と同じ列＋原文の引用・確度・取得日 |
| web_sources.txt | 取得してよいドメインの許可リスト。個人ブログ・口コミサイトは入れない |
| export_spots.php | 試作用に種別ごとの地点を読み出す（読むだけ） |

- 検索（Tavily）は **URL探しだけ**に使う（`TAVILY_API_KEY`、任意）。検索結果の本文・要約は使わない・保存しない。URLを直接指定（`--urls`）すれば検索なしで動く。
- 確度：2つ以上のドメインで一致した事実は「確度高」、1つは「要確認」。住所の区・町が本文にない地点は「保留」（同名の別の場所の疑い）。
- 使い方：`./run.sh export_spots.php housing_estate:5,port:5 > spots.tsv` → `python3 collect_web.py spots.tsv web_candidates_review.tsv --urls urls.tsv`
- 撮影の可否・評判・口コミは抜かない。承認は人（採否の列）。承認済みの事実だけが材料になる。

### 表のページは機械で読む（2026-10-18）
- 見出しに「団地名」「施設名」などの列がある表のページ（県営住宅一覧など）は、AIに読ませず、地点名と一致する行だけを列の見出しに従って読む（`table_facts`）。同名の行が複数あるときは住所の区でしぼり、決まらなければ出さない。備考に「表の列『…』を機械的に読んだ」と出る。
- 表でないページは従来どおりAI＋照合。加えて「業務開始 昭和30年6月21日」「敷地面積 120,400m2」のような『項目 値』は機械で拾う（`label_value_facts`）。
- 照合には「引用が地点名の近くにあるか」（短い引用200字／長い文800字）を追加。
- 許可リストに新天町（shintencho.or.jp）、川端通商店街（kawabatadori.com）、福岡市漁協（fukuokashigyokyo.com）を追加。銀天町・みのしま・吉塚は公式サイトを確認できず未追加。

### 承認済みのWeb候補を材料にする（2026-10-18）
- `build_kw_web.py`：採否=採 の行から `knowledge_web.php`（地点ID => 材料の文）を作る。xlsx／TSVどちらも可。文は引用の項目名と値だけ（言い換えなし）。団地全体の値は「団地全体の」を付ける。使い方：`python3 build_kw_web.py web_candidates_review.xlsx`
- `knowledge_web.php`：2026-10-18 承認分（5地点・9文）。まだ `batch_knowledge.php` には繋いでいない（繋ぐのは別の確認のあと）。
