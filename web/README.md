# Web画面（ヘテムル設置用）

## 配置
ヘテムルのドメイン（例：locadb.lab751.com）の公開フォルダに `public/` の中身を、
**公開フォルダの外**に `app/` を置く。

```
/home/users/.../
├── locadb-app/          ← app/ の中身（config.php を含む・非公開）
└── web/locadb.lab751.com/ ← public/ の中身（公開）
```
`public/bootstrap.php` の `$APP_DIR` を実際の `locadb-app` のパスに変更する。

## 設定
1. `app/config.sample.php` を `config.php` にコピー
2. Supabase の Project Settings → API から URL と service_role キーを記入
3. Anthropic APIキーを記入（空欄でもキーワード検索で動作）
4. ログインユーザーのパスワードハッシュを `php make_password.php` で作成して `users` に記入

## 画面
- シーン検索（index.php）：シーン文 → 候補クラスターを地図・ルート付きで表示
- 地点の確認（spots.php）：AI分類済みの地点を確認し、タグ・状態・確度を保存
- 変化リスク（risks.php）：要確認のリスクを「確認済み／該当なし」に処理

## セキュリティ
- service_role キーはサーバー側（config.php）だけで使用し、ブラウザには出さない
- Supabase の anon キーからはデータにアクセスできない設定（sql/004_api.sql）
- 全画面ログイン必須、CSRF対策、HTTPS転送、検索エンジン非表示
- 必要PHP拡張：curl, json, mbstring（PHP 8.1以上）
