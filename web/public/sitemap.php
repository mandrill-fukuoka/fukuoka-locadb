<?php
// sitemap.xml（.htaccess の書き換えで /sitemap.xml として返す）。載せるのは、トップ・景観/エリアの入口・材料のある地点ページ（DBの spot_is_indexable と同じ条件）
// 1ファイル最大10,000件に分け、sitemap.xml が目次になる。結果は24時間、サーバーの一時領域に置く（巡回のたびにDBを呼ばない）
require __DIR__ . '/bootstrap.php';
if (!cfg('allow_indexing', false)) { http_response_code(404); header('Content-Type: text/plain; charset=utf-8'); exit("not found\n"); }
header('Content-Type: application/xml; charset=utf-8');
const SITEMAP_PER = 10000;
$base = site_base();
$x = fn(string $s) => htmlspecialchars($s, ENT_XML1 | ENT_QUOTES, 'UTF-8');
$cacheKey = sys_get_temp_dir() . '/locadb_sitemap_' . hash('sha256', $base . '|' . (string)cfg('supabase_url', '') . '|' . ($_GET['p'] ?? 'idx') . '|' . ($_GET['k'] ?? ''));
if (is_file($cacheKey) && filemtime($cacheKey) > time() - 86400) { readfile($cacheKey); exit; }

$out = '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
try {
    $k = (string)($_GET['k'] ?? '');
    if ($k === 'pages') {
        $urls = [$base . '/index.php'];
        foreach (sb_get('landscape_groups?select=code&order=code') ?? [] as $g) if ($g['code'] !== 'water') $urls[] = $base . '/index.php?grp=' . rawurlencode($g['code']);
        foreach (array_keys(AREA_LABEL) as $a) $urls[] = $base . '/index.php?area=' . rawurlencode($a);
        $out .= '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
        foreach ($urls as $u) $out .= '<url><loc>' . $x($u) . '</loc></url>';
        $out .= '</urlset>';
    } elseif ($k === 'spots') {
        $p = max(0, (int)($_GET['p'] ?? 0));
        $rows = sb_rpc('sitemap_spots', ['p_offset' => $p * SITEMAP_PER, 'p_limit' => SITEMAP_PER]) ?? [];
        $out .= '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
        foreach ($rows as $r) $out .= '<url><loc>' . $x($base . '/spot.php?id=' . (int)$r['id']) . '</loc><lastmod>' . $x(substr((string)$r['updated_at'], 0, 10)) . '</lastmod></url>';
        $out .= '</urlset>';
    } else {
        $n = (int)(sb_rpc('sitemap_count') ?? 0);
        $out .= '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>' . $x($base . '/sitemap.php?k=pages') . '</loc></sitemap>';
        for ($i = 0; $i < max(1, (int)ceil($n / SITEMAP_PER)); $i++) $out .= '<sitemap><loc>' . $x($base . '/sitemap.php?k=spots&p=' . $i) . '</loc></sitemap>';
        $out .= '</sitemapindex>';
    }
} catch (Throwable $e) { http_response_code(503); exit('<?xml version="1.0" encoding="UTF-8"?><error/>'); }
@file_put_contents($cacheKey, $out);
echo $out;
