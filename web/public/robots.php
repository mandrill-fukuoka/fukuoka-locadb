<?php
// robots.txt（.htaccess の書き換えで /robots.txt として返す）。config の allow_indexing が false の間は全面拒否
require __DIR__ . '/bootstrap.php';
header('Content-Type: text/plain; charset=utf-8');
if (!cfg('allow_indexing', false)) { echo "User-agent: *\nDisallow: /\n"; exit; }
// 載せるのは、トップ・地点ページ・景観/エリアの入口のページ。条件つきの検索結果・流れ検索・ロケハンリスト・ログインは巡回させない（DBを呼ぶため）
echo "User-agent: *\n"
   . "Allow: /\$\n"
   . "Allow: /index.php\$\n"
   . "Allow: /index.php?grp=\n"
   . "Allow: /index.php?area=\n"
   . "Allow: /spot.php?id=\n"
   . "Disallow: /*?\n"
   . "Disallow: /flow.php\nDisallow: /flows.php\nDisallow: /list.php\nDisallow: /login.php\nDisallow: /logout.php\n"
   . "Disallow: /edit.php\nDisallow: /spots.php\nDisallow: /risks.php\nDisallow: /runs.php\nDisallow: /usage.php\nDisallow: /reports.php\nDisallow: /places.php\n"
   . "\nSitemap: " . site_base() . "/sitemap.xml\n";
