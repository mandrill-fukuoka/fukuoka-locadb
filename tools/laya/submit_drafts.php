<?php
declare(strict_types=1);
mb_internal_encoding('UTF-8');
// 使い方：
//   php submit_drafts.php adopted_XXXX.json                     … 内容の表示だけ（送らない）
//   php submit_drafts.php adopted_XXXX.json --n=3 --go          … 先頭3件だけ送る
//   php submit_drafts.php adopted_XXXX.json --skip=7775,9118 --offset=3 --n=200 --go
// 送るのは保存済みの文だけ（再生成しない）。キーは環境変数 SUPABASE_URL / SUPABASE_KEY。
$file = $argv[1] ?? '';
if ($file === '' || !is_file($file)) { fwrite(STDERR, "採用文のJSONを指定してください\n"); exit(1); }
$opt = ['n' => 3, 'offset' => 0, 'skip' => [], 'go' => false];
foreach (array_slice($argv, 2) as $a) {
    if ($a === '--go') $opt['go'] = true;
    elseif (preg_match('/^--n=(\d+)$/', $a, $m)) $opt['n'] = (int)$m[1];
    elseif (preg_match('/^--offset=(\d+)$/', $a, $m)) $opt['offset'] = (int)$m[1];
    elseif (preg_match('/^--skip=([\d,]+)$/', $a, $m)) $opt['skip'] = array_map('intval', explode(',', $m[1]));
}
$opt['n'] = max(1, min(200, $opt['n']));
$data = json_decode((string)file_get_contents($file), true);
$items = array_values(array_filter($data['items'] ?? [], fn($i) => !in_array($i['spot_id'], $opt['skip'], true)));
$batch = array_slice($items, $opt['offset'], $opt['n']);
$payloadItems = [];
foreach ($batch as $i) {
    $len = mb_strlen($i['summary']);
    if ($len < 1 || $len > 800) { echo "除外（字数 {$len}）：{$i['spot_id']}\n"; continue; }
    $payloadItems[] = ['spot_id' => $i['spot_id'], 'summary' => $i['summary']];
    echo "{$i['spot_id']} {$i['name']}\n  {$i['summary']}\n";
}
echo "\n対象 " . count($payloadItems) . " 件（全 " . count($items) . " 件中、offset {$opt['offset']}）／ model {$data['model']} ／ 版 {$data['prompt_version']}\n";
if (!$opt['go']) { echo "（表示のみ。送るには --go を付ける）\n"; exit(0); }
$url = getenv('SUPABASE_URL') ?: ''; $key = getenv('SUPABASE_KEY') ?: '';
if ($url === '' || $key === '') { fwrite(STDERR, "SUPABASE_URL と SUPABASE_KEY が必要です\n"); exit(1); }
$body = json_encode([
    'p_source' => 'laya', 'p_model' => $data['model'], 'p_prompt_version' => $data['prompt_version'],
    'p_items' => $payloadItems,
], JSON_UNESCAPED_UNICODE);
$ch = curl_init(rtrim($url, '/') . '/rest/v1/rpc/ai_submit');
curl_setopt_array($ch, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 60,
    CURLOPT_HTTPHEADER => ["apikey: {$key}", "Authorization: Bearer {$key}", 'Content-Type: application/json']]);
$res = curl_exec($ch); $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
echo "HTTP {$code}\n{$res}\n";
