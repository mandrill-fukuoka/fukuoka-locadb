<?php
// このファイルを config.php にコピーして値を入れる。公開ディレクトリの外に置くこと。
return [
    // Supabase: Project Settings → API
    'supabase_url'         => 'https://xxxxx.supabase.co',
    'supabase_service_key' => 'eyJ...（service_role キー。絶対に公開しない）',

    // 空欄ならキーワードによる簡易解釈で動作
    'anthropic_api_key'    => '',
    'claude_model'         => 'claude-sonnet-5-5',

    // ログインユーザー（パスワードは php app/make_password.php で生成したハッシュ）
    'users' => [
        // 'hirata' => '$2y$10$....',
    ],
];
