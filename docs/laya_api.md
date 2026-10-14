# LAYA（別ラインのAI）からの書き込み仕様

2026-10-14 利用者と合意した規則（DB変更130）。**AIは下書きまで。公開の説明文は人が採用したときだけ変わる。**

## 規則

| 対象 | LAYA ができること |
|---|---|
| 説明文 | 下書きを出す（`ai_submit`）。公開の説明文（`spots.summary`）は直接書けない（DBの制約で拒否される） |
| 雰囲気タグ | 下の語彙（27種）から付ける。ログインなしの画面には出ない（ログイン時だけ「AIの推定」と表示。2026-10-14 から）。流れの探索の順位づけには使われる。人が付けたタグは変わらない |
| 種別・景観・位置・名前・確認の状態・確度 | 書かない（OSMの規則と人の確認で決まる） |
| 表の直接の書き換え（PATCH・INSERT） | しない。書き込みは `ai_submit` だけ |

## 読む：ビュー `v_fukuoka_spots`

```
GET {SUPABASE_URL}/rest/v1/v_fukuoka_spots?select=id,name,address,lat,lng,elevation_m,category,relief_m,slope_pct,summary,laya_draft,laya_last_at&laya_draft=eq.false&limit=200
apikey: {service_role キー}
Authorization: Bearer {service_role キー}
```

- 対象は掲載中の地点すべて（対象外を除く）。先頭6列（id, name, address, lat, lng, elevation_m）は変わらない
- `summary`：公開中の説明文（人が書いた・採用したもの。ないことが多い）
- `laya_draft`：LAYA の採用待ちの下書きがあるか。`false` の地点だけを書けば重複しない
- `laya_last_at`：LAYA が最後に下書きを出した日時
- 地点の種別名・景観などがほしいときは `spot_categories`・`landscape_groups` を読む

## 書く：関数 `ai_submit`

```
POST {SUPABASE_URL}/rest/v1/rpc/ai_submit
apikey: {service_role キー}
Authorization: Bearer {service_role キー}
Content-Type: application/json

{
  "p_source": "laya",
  "p_model": "qwen2.5:14b-instruct",
  "p_prompt_version": "laya-summary-v1",
  "p_items": [
    {"spot_id": 7001, "summary": "城南区の大学病院。…（800字まで）",
     "tags": [{"code": "high_traffic", "score": 0.7}, {"code": "inorganic", "score": 0.5}]},
    {"spot_id": 7002, "summary": "…"},
    {"spot_id": 7003, "tags": [{"code": "nostalgic", "score": 0.8}]}
  ]
}
```

返り値：

```json
{"summaries": 2, "tags": 3, "rejected": [{"spot_id": 9999, "reason": "地点がない、または対象外"}]}
```

- 1回に **200件まで**。それより多いとエラー（何も書かれない）。順に何回でも呼んでよい
- `summary`：1〜800字。同じ地点に LAYA の前の下書きがあれば、新しい方に置き換わる（前のものは履歴として残る）
- `tags`：`code` は下の語彙だけ。`score` は 0〜1（省略は 0.5）。`tags` を書いた地点は、その地点の LAYA のタグがその内容に置き換わる（`[]` で消せる）。`tags` を省いた地点のタグには触れない
- `p_model`・`p_prompt_version`：必ず入れる。プロンプトを変えたら版を上げる（後で「どの版の文章か」を追えるように）
- 弾かれた項目は `rejected` に理由が返る。ほかの項目は受け付けられる
- キーはリポジトリ・ログ・チャットに出さない

## 書いてはいけないこと（説明文）

公開されうる文章なので、次は書かない。人が採用の前に確認するが、下書きの段階から避ける。

- 確かめていない事実（治安・営業時間・混雑・撮影許可の有無など）。「〜である」と言い切らない
- 個人・店名の評判、口コミの言い換え
- 撮影できることの保証（このDBは撮影の可否を保証しない）

## タグの語彙（tags 表の27種）

| 分類 | code（表示名） |
|---|---|
| 雰囲気 mood | calm（穏やか）、futuristic（近未来的）、gritty（荒々しい）、inorganic（無機質）、lively（活気）、lonely（寂しい）、nostalgic（懐かしい）、romantic（ロマンチック）、tense（緊張感） |
| 時間帯 time | dawn_ok（早朝映え）、night_ok（夜間映え）、sunset_ok（夕景映え） |
| 時代感 era | heisei（平成感）、reiwa（現代的）、showa（昭和感） |
| 人の多さ density | high_traffic（人通り多）、low_traffic（人通り少） |
| 特徴 feature | greenery（緑）、industrial_structure（クレーン・工業構造物）、narrow_street（狭い道）、neon（ネオン・看板）、river_view（川が見える）、sea_view（海が見える）、skyline（高層ビル群）、slope_stairs（坂・階段）、streetlight_warm（暖色の街灯）、wide_open（開けた空間） |

新しいタグが必要なときは、DB変更（番号帯130〜139）で語彙に足してから使う。

## 2026-10-14 の取り消し（DB変更131）

版 `laya-summary-v2` の一括処理は、全地点に同じタグ（nostalgic）を付け、説明文を定型文に置き換えていたため取り消した（タグは削除、v2 の下書きは却下、元の下書きを採用待ちに戻した）。再開するときは次を守る。

- 文章は LAYA に地点の情報を渡して作らせる。決まった文に値を差し込むだけの文は送らない。文章が返らなかった地点は送らずに飛ばす
- タグは推定したものだけを送る。推定できない地点は `tags` を省く
- 最初の1回は `ai_submit` を呼ばずに生成文を表示だけして確かめ、次に2〜3件だけ送り、確認画面で中身を見てから全件に広げる
- 新しい版名（例 `laya-summary-v3`）を付ける
- キーはコードに書かず環境変数から読む

## その後

- 人は確認画面「説明文の下書き」タブ、または地点ページで、下書きを採用（そのまま／直して）・却下する
- 採用された説明文は `v_fukuoka_spots.summary` に出る。却下された下書きは使われない
- 2026-10-14 に、LAYA がそれまで `spots.summary` に直接書いていた説明文 4,579件を、出どころ LAYA・版 `before-2026-10-14` の下書きに移した
