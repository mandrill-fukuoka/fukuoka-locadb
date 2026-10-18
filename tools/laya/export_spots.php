<?php
// 試作用：種別ごとに地点を N 件ずつ読み出して、collect_web.py の入力（地点ID・地点名・住所・種別）にする。読むだけ（書き込まない）。
// 使い方: ./run.sh export_spots.php 'housing_estate:3:県営,housing_estate:3:UR,port:5:ふ頭,eatery_street:3:市場' > spots.tsv
//   指定は「種別コード:件数[:名前に含む語]」をカンマでつなぐ。語を付けると、名前にその語を含む地点だけを id 順に取る。
//   名前のない地点（「（無名）…」）と名前が空の地点は除く。DRY=1 を付けると、読まずに問い合わせのURLだけ表示する。
error_reporting(E_ALL & ~E_DEPRECATED);   // PHP のバージョン差の警告が標準出力（TSV）に混ざらないように
ini_set('display_errors', 'stderr');

function build_query(string $base, string $cat, int $n, string $word): string
{
    $conds = ['name.not.ilike.' . rawurlencode('（無名）*'), 'name.not.is.null'];
    if ($word !== '') $conds[] = 'name.ilike.' . rawurlencode('*' . $word . '*');
    return $base . '/rest/v1/v_fukuoka_spots?select=id,name,address,category&category=eq.' . rawurlencode($cat)
        . '&and=(' . implode(',', $conds) . ')&order=id.asc&limit=' . $n;
}

$url = getenv('SUPABASE_URL'); $key = getenv('SUPABASE_KEY'); $dry = getenv('DRY') === '1';
if ((!$url || !$key) && !$dry) { fwrite(STDERR, "SUPABASE_URL / SUPABASE_KEY が必要（run.sh 経由で実行）\n"); exit(1); }
$spec = $argv[1] ?? '';
if ($spec === '') { fwrite(STDERR, "引数: 種別コード:件数[:語],… 例 housing_estate:3:県営,port:5:ふ頭\n"); exit(1); }
echo "地点ID\t地点名\t住所\t種別\n";
foreach (explode(',', $spec) as $p) {
    [$cat, $n, $word] = array_pad(explode(':', $p, 3), 3, '');
    $n = (int)($n === '' ? 5 : $n);
    $q = build_query((string)$url, trim($cat), $n, trim($word));
    if ($dry) { fwrite(STDERR, $q . "\n"); continue; }
    $ch = curl_init($q);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 30,
        CURLOPT_HTTPHEADER => ["apikey: $key", "Authorization: Bearer $key"]]);
    $resp = curl_exec($ch); $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $rows = json_decode((string)$resp, true);
    if ($code !== 200 || !is_array($rows)) { fwrite(STDERR, "$cat: 取得失敗 HTTP $code\n"); continue; }
    if (!$rows) fwrite(STDERR, "{$cat}" . ($word !== '' ? "（{$word}）" : '') . ": 該当なし\n");
    foreach ($rows as $r) {
        echo implode("\t", [$r['id'], str_replace("\t", ' ', (string)$r['name']), str_replace("\t", ' ', (string)($r['address'] ?? '')), $cat]) . "\n";
    }
}
