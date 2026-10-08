<?php
// app（設定・共通処理）の場所を探す。
// 1) 公開フォルダの外（推奨）: web/ と同じ階層の locadb-app/
// 2) 公開フォルダ内の _app/（.htaccess でアクセス禁止）
$candidates = [
    getenv('LOCADB_APP_DIR') ?: null,
    rtrim((string)($_SERVER['DOCUMENT_ROOT'] ?? ''), '/') . '/../locadb-app',
    __DIR__ . '/_app',
];
foreach ($candidates as $dir) {
    if ($dir && is_file($dir . '/config.php')) { require $dir . '/lib.php'; return; }
}
http_response_code(500);
exit('設定ファイル（config.php）が見つかりません。設置手順を確認してください。');
