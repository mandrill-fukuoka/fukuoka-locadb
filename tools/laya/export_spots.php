<?php
// 試作用：種別ごとに地点を N 件ずつ読み出して、collect_web.py の入力（地点ID・地点名・住所・種別）にする。読むだけ（書き込まない）。
// 使い方: ./run.sh export_spots.php housing_estate:5,port:5,fishing_port:3,eatery_street:2,shotengai:5 > spots.tsv
// 名前のある地点を id 順に取る。試作の地点は、出力を見て人が差し替えてよい。
$url = getenv('SUPABASE_URL'); $key = getenv('SUPABASE_KEY');
if (!$url || !$key) { fwrite(STDERR, "SUPABASE_URL / SUPABASE_KEY が必要（run.sh 経由で実行）\n"); exit(1); }
$spec = $argv[1] ?? '';
if ($spec === '') { fwrite(STDERR, "引数: 種別コード:件数,… 例 housing_estate:5,port:5\n"); exit(1); }
echo "地点ID\t地点名\t住所\t種別\n";
foreach (explode(',', $spec) as $p) {
    [$cat, $n] = array_pad(explode(':', $p), 2, '5');
    $q = $url . '/rest/v1/v_fukuoka_spots?select=id,name,address,category&category=eq.' . rawurlencode($cat)
       . '&name=not.is.null&order=id.asc&limit=' . (int)$n;
    $ch = curl_init($q);
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 30,
        CURLOPT_HTTPHEADER => ["apikey: $key", "Authorization: Bearer $key"]]);
    $resp = curl_exec($ch); $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);   // curl_close は PHP 8.0 から効果がなく、8.5 で非推奨
    $rows = json_decode((string)$resp, true);
    if ($code !== 200 || !is_array($rows)) { fwrite(STDERR, "$cat: 取得失敗 HTTP $code\n"); continue; }
    foreach ($rows as $r) {
        echo implode("\t", [$r['id'], str_replace("\t", ' ', (string)$r['name']), str_replace("\t", ' ', (string)($r['address'] ?? '')), $cat]) . "\n";
    }
}
