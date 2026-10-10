<?php
// このファイルを config.php にコピーして値を入れる。公開ディレクトリの外に置くこと。
return [
    // Supabase: Project Settings → API Keys
    'supabase_url'         => 'https://xxxxx.supabase.co',
    'supabase_service_key' => 'sb_secret_...（secret キー。旧形式の service_role キー eyJ... でも可。絶対に公開しない）',

    // 空欄ならキーワードによる簡易解釈で動作
    'anthropic_api_key'    => '',
    'claude_model'         => 'claude-sonnet-5-5',

    // Googleマップ埋め込み（Maps Embed API のキー。空欄なら OpenStreetMap の地図を表示）
    'google_maps_embed_key' => '',

    // ログインなしで使う人への制限（「流れで探す」の検索回数／1人1時間、AI解釈の回数／全員合計1日）
    'public_search_per_hour' => 30,
    'public_ai_per_day'      => 300,
    // 地点ページの「福岡フィルムコミッション」に付けるリンク（任意）
    'ffc_url'                => '',
    // 検索エンジンに載せる場合は true（公開準備が整うまでは false のまま）
    'allow_indexing'         => false,

    // ログインユーザー（地点の確認・変化リスク・おすすめの保存ができる人）（パスワードは php app/make_password.php で生成したハッシュ）
    'users' => [
        // 'hirata' => '$2y$10$....',
    ],
];
