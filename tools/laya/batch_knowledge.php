<?php
declare(strict_types=1);
mb_internal_encoding('UTF-8');

// ===== 接続情報（環境変数から読む）=====
$supabaseUrl = getenv('SUPABASE_URL') ?: '';
$supabaseKey = getenv('SUPABASE_KEY') ?: '';
if ($supabaseUrl === '' || $supabaseKey === '') {
    fwrite(STDERR, "SUPABASE_URL と SUPABASE_KEY を環境変数で設定してください\n");
    exit(1);
}

// ===== 設定 =====
$maxTry         = 8;                        // v6：3→5（置換文字・住所直書きでの全滅が66/259件あったため）
$runsPerSpot    = 1;                        // 地点ごとの生成回数（本番は1回で採否を決める）
$temperatures   = [0.2, 0.5, 0.7, 0.3, 0.6, 0.1, 0.4, 0.8];
$ollamaEndpoint = 'http://localhost:11434/api/chat';
$model          = 'qwen2.5:14b';
$promptVersion  = 'laya-summary-v10';        // 本番の版（ai_submit 時に記録する）
$dryRun         = true;                     // true：ai_submit は送らない（画面に出すだけ）
$saveDir        = __DIR__ . '/laya_batch';  // 公開領域の外に置くこと
$stamp          = date('Ymd_His');
$placeholder    = '{住所}';

$forbiddenWords = [
'一望', '最適', '治安', '必ず', 'デザイン', '適して', '良好', '中心', '特徴的',
'魅力', '人気', 'おすすめ', '絶景', '名所',
'重要な', '知られ', '価値の', '溶け込', '佇', '調和',
'歴史的', '歴史ある', '伝統的', '特徴',   // v6追加：事実行にない性格づけを防ぐ
'近隷', '貢献', '役割', 'ます。', 'です。', 'ました', 'でした', 'ません',   // v6追加：誤字・常体違反・事実にない効果の記述
'歴史的建築', '建築・遺跡', '建築や遺跡', '建築物や遺跡', '機能し', '恵まれ', '便が良', '容易', '結節点', '注意が必要',
'丘陵', '方向', '方角', '北西', '北東', '南西', '南東',   // v6追加：事実行にない地形・方角の混入を防ぐ
];
$allowedComparisons = ['周りより低い', '周りより高い', '周りとほぼ同じ高さ', '周りよりやや高い', '標高'];
$phraseFixes = [
'ことで知られている'         => 'ことである',
'ことで知られる'             => 'ことである',
'見どころとして知られている' => '見どころである',
'見どころとして知られる'     => '見どころである',
'知られており、' => 'あり、', 'として知られた' => 'である', '知られた' => 'ある',
'されました。' => 'された。', 'ました。' => 'た。',
'知られている'               => 'ある',
'知られる'                   => 'ある',
'しています。' => 'している。', 'ています。' => 'ている。', 'あります。' => 'ある。', 'います。' => 'いる。',
'します。' => 'する。', 'れます。' => 'れる。', 'できます。' => 'できる。', 'なります。' => 'なる。',
'でした。' => 'であった。', 'です。' => 'である。',   // v7：丁寧体を常体に直す（これで直らない「ます。」は禁止語で再生成）
'あるである' => 'ある', 'いるである' => 'いる', 'するである' => 'する', 'なるである' => 'なる', 'れるである' => 'れる',   // 常体指示で出る重複の補正
];

// ===== 材料（承認済みの事実だけ）=====
$knowledge = [
7796 => [
'博多の氏神・総鎮守として古くから信仰されている神社',
'7月の博多祇園山笠や10月の博多おくんちなどの祭事が行われる',
'境内の入り口付近には「櫛田の銀杏（ぎんなん）」と呼ばれる樹齢約1000年の銀杏がある',
],
7133 => [
'博多津に人魚が出現した際、これを寺内に埋葬して寺号を龍宮寺と改めた',
'浄土宗鎮西義の寺院。山号は冷泉山。本尊は聖観音',
],
7081 => [
'日本三百名山及び、九州百名山の一つ',
'山頂には航空自衛隊、アメリカ軍のレーダーサイト（背振山分屯基地）がある',
'山の上部（標高800m付近以上）にはブナやアカガシの天然林がある',
],
6939 => [
'浄土真宗本願寺派の寺院',
'境内は日中開放されている',
'重要文化財：木造阿弥陀如来立像（快成作）',
],
7420 => [
'丸隈山古墳は、高祖山のふもとに位置する前方後円墳',
],
7425 => [
'山全体が今山遺跡（いまやまいせき）として国指定史跡となっている',
'今山の山内にある弥生時代の石斧製作所の遺跡',
],
7467 => [
'天福寺（てんぶくじ）は、臨済宗妙心寺派に属する寺',
],
];

$originalIds = array_keys($knowledge);        // 送信済みの7地点（Web由来の材料でも触らない）

// ===== 所在の承認済み材料（knowledge_wiki_v2.php）を追加 =====
// 既存の材料（上の7地点）は送信済みなので、今回の対象は所在の材料だけの地点にする
$locKnowledge = require __DIR__ . '/knowledge_wiki.php';
$targetIds = [];
foreach ($locKnowledge as $id => $lines) {
    if (isset($knowledge[$id])) continue;      // 既存の材料がある地点は触らない
    $knowledge[$id] = $lines;
    $targetIds[] = $id;
}
// ===== Web（公式ページ）由来の承認済み材料（knowledge_web.php）を追加 =====
// 送信済みの地点は触らない。所在の材料がある地点には文を足し、材料のない地点は対象に加える
$webKnowledge = require __DIR__ . '/knowledge_web.php';
foreach ($webKnowledge as $id => $lines) {
    if (in_array($id, $originalIds, true)) continue;
    if (isset($knowledge[$id])) {
        $knowledge[$id] = array_values(array_unique(array_merge($knowledge[$id], $lines)));
    } else {
        $knowledge[$id] = $lines;
        $targetIds[] = $id;
    }
}
// 使い方：php batch_knowledge_v2.php [件数]  （例：5 で先頭5件だけ試す。省略で全件）
$wikiAddr = require __DIR__ . '/wiki_addr.php';
$limit = (int)($argv[1] ?? 0);
if ($limit > 0) $targetIds = array_slice($targetIds, 0, $limit);
// 地点を指定して試す：php batch_knowledge_v7.php 0 v 7940,7230
if (!empty($argv[3])) {
    $want = array_map('intval', explode(',', $argv[3]));
    $targetIds = array_values(array_filter($want, fn($i) => isset($knowledge[$i])));
}

if (!is_dir($saveDir)) {
    mkdir($saveDir, 0700, true);
}

// ===== 関数（前回の版と同じ）=====
function curl_call(string $url, array $headers, ?string $body = null, int $timeout = 30): array
{
    $ch = curl_init($url);
    $opts = [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT        => $timeout,
    CURLOPT_HTTPHEADER     => $headers,
    ];
    if ($body !== null) {
        $opts[CURLOPT_POST]       = true;
        $opts[CURLOPT_POSTFIELDS] = $body;
    }
    curl_setopt_array($ch, $opts);
    $resp = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    return [$code, $resp === false ? '' : (string)$resp, $err];
}

function display_name(string $n): string
{
    if (preg_match('/^（無名）(.+?)（(.+)）$/u', $n, $m)) return $m[2] . 'の' . $m[1];
    if (preg_match('/^（無名）(.+)$/u', $n, $m)) return '名前のない' . $m[1];
    return $n;
}

function quality_problem(string $s): ?string
{
    if ($s === '') return '空';
    if (!mb_check_encoding($s, 'UTF-8')) return '不正なUTF-8';
    if (mb_strpos($s, "\u{FFFD}") !== false) return '置換文字（�）';
    if (strpos($s, '??') !== false) return '「??」の混入';
    if (mb_strpos($s, '尔') !== false) return '誤字の混入（尔）';
    return null;
}

function find_forbidden(string $text, array $forbidden, array $allowed, string $factsText = ''): ?string
{
    $masked = $text;
    foreach ($allowed as $ok) {
        $masked = str_replace($ok, '', $masked);
    }
    foreach ($forbidden as $w) {
        $off = 0;
        while (($pos = mb_strpos($masked, $w, $off)) !== false) {
            // v9：前後3字ごと事実行にある語（例：近隣の種別「歴史的建築・遺跡」）は、事実の引用なので許す
            $win = mb_substr($masked, max(0, $pos - 3), mb_strlen($w) + 6);
            $inFacts = $factsText !== '' && (
                mb_strpos($factsText, $win) !== false
                || mb_strpos($factsText, mb_substr($masked, $pos, mb_strlen($w) + 2)) !== false
                || mb_strpos($factsText, mb_substr($masked, $pos, mb_strlen($w) + 3)) !== false
                || mb_strpos($factsText, mb_substr($masked, max(0, $pos - 2), mb_strlen($w) + 2)) !== false
            );
            if (!$inFacts) return $w;
            $off = $pos + mb_strlen($w);
        }
    }
    return null;
}

/** 出力にある固有名詞（〜線・〜駅・〜寺・〜公園など）が、事実行にあるか照合する。なければその語を返す */
function find_unknown_name(string $text, string $factsText): ?string
{
    $factsText .= ' ' . (preg_replace('/（[^）]*）/u', '', $factsText) ?? '');   // v9：括弧書き（例：「西日本鉄道（西鉄）貝塚線」）を除いた並びも許す
    $factsText .= ' ' . str_replace('西鉄', '西日本鉄道', $factsText) . ' ' . str_replace('西日本鉄道', '西鉄', $factsText);   // 西鉄⇔西日本鉄道の言い換えは許す
    if (preg_match_all('/[\x{4E00}-\x{9FFF}\x{30A1}-\x{30FA}ーA-Za-z0-9々]{2,10}(?:線|駅|寺|神社|公園|川|池|稲荷|学校|病院|橋)/u', $text, $m)) {
        foreach ($m[0] as $w) {
            if (mb_strpos($factsText, $w) === false) return $w;
        }
    }
    return null;
}

/** 「福岡市」の直書きを判定する。施設名の一部（福岡市立〜、事実行にある名称）は除いて判定する */
// v11：文中の数字が、事実行にある数字だけかを調べる（距離・標高・年数の取り違え防止）
function find_unknown_number(string $text, string $factsText): ?string
{
    if (!preg_match_all('/\d+(?:\.\d+)?/u', $text, $m)) return null;
    foreach ($m[0] as $num) {
        if (!preg_match('/(?<![\d.])' . preg_quote($num, '/') . '(?![\d])/u', $factsText)) return $num;
    }
    return null;
}

// v12：事実行にない英字の語（英語名の混入）を検出
function find_unknown_latin(string $text, string $factsText): ?string
{
    if (!preg_match_all('/[A-Za-z]{4,}/u', $text, $m)) return null;
    foreach ($m[0] as $w) {
        if (stripos($factsText, $w) === false) return $w;
    }
    return null;
}

function city_literal(string $text, string $factsText): bool
{
    $masked = str_replace('福岡市立', '', $text);
    $src = preg_replace('/^- 住所：.*$/mu', '', $factsText) ?? $factsText;   // 住所行からは名称を拾わない
    if (preg_match_all('/福岡市[^\s、。（）：「」]{2,14}/u', $src, $m)) {
        usort($m[0], fn($a, $b) => mb_strlen($b) <=> mb_strlen($a));
        foreach ($m[0] as $name) {
            $masked = str_replace($name, '', $masked);
        }
    }
    return mb_strpos($masked, '福岡市') !== false;
}

/** 住所の数字表記ゆれ（全角・算用数字・漢数字）を算用数字にそろえる */
function norm_addr(string $s): string
{
    $s = mb_convert_kana($s, 'n');
    $map = ['一'=>1,'二'=>2,'三'=>3,'四'=>4,'五'=>5,'六'=>6,'七'=>7,'八'=>8,'九'=>9];
    return preg_replace_callback('/([一二三四五六七八九十]+)丁目/u', function ($m) use ($map) {
        $t = $m[1]; $len = mb_strlen($t);
        if ($t === '十') $n = 10;
        elseif ($len === 1) $n = $map[$t];
        elseif (mb_substr($t, 0, 1) === '十') $n = 10 + ($map[mb_substr($t, 1, 1)] ?? 0);
        else $n = ($map[mb_substr($t, 0, 1)] ?? 0) * 10 + ($len > 2 ? ($map[mb_substr($t, 2, 1)] ?? 0) : 0);
        return $n . '丁目';
    }, $s) ?? $s;
}

/**
 * 文字化けの自動修復（鉄→「�鐵」など）。修復後も、固有名詞・禁止語の検査はそのまま通す。
 * 1) 既知のパターン：「�鐵」「鐵」→「鉄」（事実行に旧字体がない場合）
 * 2) 置換文字（�）の前後2字ずつを手がかりに、事実行の同じ並びから1字を補う（候補が1つだけのとき）
 */
function repair_mojibake(string $text, string $factsText): array
{
    $n = 0;
    if (mb_strpos($factsText, '鐵') === false) {
        $text = str_replace(["\u{FFFD}鐵", '鐵'], '鉄', $text, $c1);
        $n += $c1;
    }
    $text = preg_replace_callback('/(.{0,2})(\x{FFFD}+)[A-Za-z]*(.{0,2})/us', function ($m) use ($factsText, &$n) {   // 例：「地下�metrot七隈線」
        [$all, $b, , $a] = $m;
        if ($b === '' && $a === '') return $all;
        $cands = [];
        if (preg_match_all('/' . preg_quote($b, '/') . '(.{1,2}?)' . preg_quote($a, '/') . '/us', $factsText, $mm)) {
            $cands = array_values(array_unique($mm[1]));
        }
        if (count($cands) === 1) {
            $n++;
            return $b . $cands[0] . $a;
        }
        return $all;
    }, $text) ?? $text;
    return [$text, $n];
}

function fix_phrases(string $text, array $fixes): array
{
    $count = 0;
    foreach ($fixes as $from => $to) {
        $n = 0;
        $text = str_replace($from, $to, $text, $n);
        $count += $n;
    }
    return [$text, $count];
}

function generate_clean(
string $endpoint, string $model, string $prompt, int $maxTry, array $temperatures,
string $saveDir, string $tag, string $address, string $placeholder,
array $forbidden, array $allowed, array $fixes, string $factsText = ''
): array {
    $log = [];
    for ($t = 1; $t <= $maxTry; $t++) {
        $temperature = $temperatures[$t - 1] ?? end($temperatures);
        $payload = json_encode([
        'model'    => $model,
        'messages' => [['role' => 'user', 'content' => $prompt]],
        'stream'   => false,
        'options'  => ['temperature' => $temperature],
        ], JSON_UNESCAPED_UNICODE);
        
        [$code, $body, $err] = curl_call($endpoint, ['Content-Type: application/json'], $payload, 240);
        
        $file = "{$saveDir}/{$tag}_try{$t}.json";
        file_put_contents($file, $body);
        
        if ($code !== 200) {
            $log[] = ['try' => $t, 'problem' => "HTTP {$code} {$err}", 'temp' => $temperature];
            continue;
        }
        
        $res     = json_decode($body, true);
        $content = (string)($res['message']['content'] ?? '');
        
        // v15：「鉄」の代用記号の揺れをまとめて直す（{鉄} / {{鉄}} / {�铁} / {�鐵} / 単独の�铁 など）
        $content = preg_replace('/[\{｛]+\s*[\x{FFFD}铁鐵鉄]+\s*[\}｝]+/u', '鉄', $content) ?? $content;
        $content = preg_replace('/\x{FFFD}*[铁鐵]/u', '鉄', $content) ?? $content;
        [$content, $repairedN] = repair_mojibake($content, $factsText);   // v8：文字化けの自動修復
        $content = preg_replace('/福岡市(?:東|博多|中央|南|西|城南|早良)区の|福岡市の/u', '', $content) ?? $content;   // v10：{住所}と重なる「福岡市○区の」を除く
        $problem = quality_problem($content);
        if ($problem === null && substr_count($content, $placeholder) === 0) {
            $problem = '住所の記号なし';
        }
        if ($problem === null && city_literal($content, $factsText)) {
            $problem = '住所の直書き（福岡市）';
        }
        
        $text   = $content;
        $fixedN = 0;
        if ($problem === null) {
            $text = str_replace($placeholder, $address, $content);
            [$text, $fixedN] = fix_phrases($text, $fixes);
            $problem = quality_problem($text);
        }
        if ($problem === null) {
            $hit = find_forbidden($text, $forbidden, $allowed, $factsText);
            if ($hit !== null) {
                $problem = "NG語：{$hit}";
            }
        }
        
        if ($problem === null && $factsText !== '') {
            $unk = find_unknown_name($text, $factsText);
            if ($unk !== null) {
                $problem = "事実行にない固有名詞：{$unk}";
            }
        }
        
        if ($problem === null && $factsText !== '') {
            $badLatin = find_unknown_latin($text, $factsText);
            if ($badLatin !== null) {
                $problem = "事実行にない英字：{$badLatin}";
            }
        }
        if ($problem === null && $factsText !== '') {
            $badNum = find_unknown_number($text, $factsText);
            if ($badNum !== null) {
                $problem = "事実行にない数字：{$badNum}";
            }
        }
        
        $log[] = ['try' => $t, 'problem' => $problem, 'temp' => $temperature];
        
        if ($problem === null) {
            return ['text' => $text, 'log' => $log, 'fixed' => $fixedN];
        }
    }
    return ['text' => null, 'log' => $log, 'fixed' => 0];
}

$sbHeaders = [
"apikey: {$supabaseKey}",
"Authorization: Bearer {$supabaseKey}",
"Content-Type: application/json",
];

// ===== 1. 材料のある地点を順に処理 =====
$results = [];
foreach ($targetIds as $spotId) {
    echo "===== 地点 {$spotId} =====\n";
    
    [$dCode, $dBody, $dErr] = curl_call(
    "{$supabaseUrl}/rest/v1/rpc/spot_detail",
    $sbHeaders,
    json_encode(["p_id" => $spotId]),
    30
    );
    $detail     = ($dCode === 200) ? json_decode($dBody, true) : null;
    $detailData = (is_array($detail) && isset($detail[0])) ? $detail[0] : $detail;
    if (!is_array($detailData)) {
        echo "  spot_detail 取得失敗（HTTP {$dCode}）\n\n";
        $results[] = ['id' => $spotId, 'status' => '取得失敗', 'text' => null];
        continue;
    }
    
    // ----- 事実行（prod_one.php と同じ組み立て）-----
    $spotName     = display_name((string)($detailData['name'] ?? ''));
    $categoryName = $detailData['category_name'] ?? $detailData['category'] ?? '不明';
    $address      = (string)($detailData['address'] ?? '住所不明');
    
    if ($address === '' || $address === '住所不明') {
        echo "  保留（住所がDBにない。手書きまたは住所の補完が先）\n\n";
        $results[] = ['id' => $spotId, 'status' => '不採用', 'text' => null];
        continue;
    }
    if (isset($wikiAddr[$spotId]) && mb_strpos(norm_addr($address), $wikiAddr[$spotId]) === false) {
        echo "  ⚠ 住所の食い違い：Wikipediaは「{$wikiAddr[$spotId]}」、DBは「{$address}」\n";
    }
    $factLines   = [];
    $factLines[] = "- 名称：{$spotName}（種別：{$categoryName}）";
    $factLines[] = "- 住所：{$address}";
    
    $elevation = $detailData['elevation_m'] ?? null;
    $reliefM   = $detailData['relief_m'] ?? null;
    $slopePct  = $detailData['slope_pct'] ?? null;
    
    $topoParts = [];
    if ($elevation !== null) $topoParts[] = "標高 " . round((float)$elevation) . "m";
    if ($reliefM !== null) {
        $r = (float)$reliefM;
        if ($r >= 10)         $topoParts[] = "周りより高い（高台）";
        elseif ($r >= 3)      $topoParts[] = "周りよりやや高い";
        elseif (abs($r) <= 3) $topoParts[] = "周りとほぼ同じ高さ";
        else                  $topoParts[] = "周りより低い";
    }
    if ($slopePct !== null) {
        $s = (float)$slopePct;
        if ($s < 5)       $topoParts[] = "傾き：平坦";
        elseif ($s < 15)  $topoParts[] = "傾き：緩い傾斜がある";
        else              $topoParts[] = "傾き：急な傾斜がある";
    }
    if (!empty($topoParts)) {
        $factLines[] = "- 地形：" . implode('、', $topoParts);
    }
    
    if (!empty($detailData['slope_kind'])) {
        $dirs       = ['北', '北東', '東', '南東', '南', '南西', '西', '北西'];
        $bearingIdx = isset($detailData['slope_bearing'])
        ? ((int)round((float)$detailData['slope_bearing'] / 45)) % 8
        : 0;
        $kindText  = ($detailData['slope_kind'] === 'steps') ? '階段' : '道';
        $lenText   = isset($detailData['slope_len_m'])     ? round((float)$detailData['slope_len_m']) . 'm' : '';
        $gradeText = isset($detailData['slope_grade_pct']) ? round((float)$detailData['slope_grade_pct']) . '%' : '';
        $riseText  = isset($detailData['slope_rise_m'])    ? round((float)$detailData['slope_rise_m']) . 'm' : '';
        $factLines[] = "- 坂：{$kindText}、長さ{$lenText}、平均勾配{$gradeText}、高低差{$riseText}、{$dirs[$bearingIdx]}へ上る";
    }
    
    [$nCode, $nBody] = curl_call(
    "{$supabaseUrl}/rest/v1/rpc/spots_nearby",
    $sbHeaders,
    json_encode(["p_id" => $spotId, "p_radius" => 400]),
    30
    );
    $nearbyItems = ($nCode === 200) ? json_decode($nBody, true) : null;
    $nearbyList  = [];
    if (is_array($nearbyItems)) {
        foreach (array_slice($nearbyItems, 0, 3) as $n) {
            $nName     = display_name((string)($n['name'] ?? '施設'));
            $nCategory = $n['category'] ?? '';
            $nMeters   = isset($n['meters']) ? round((float)$n['meters']) . 'm' : '';
            if (preg_match('/歴史的|丘陵|特徴/u', $nCategory)) $nCategory = '';   // v14：種別名を文に写す癖を防ぐ
            $catPart   = $nCategory ? "{$nCategory}、" : '';
            $nearbyList[] = "{$nName}（{$catPart}{$nMeters}）";
        }
    }
    if (!empty($nearbyList)) {
        $factLines[] = "- 400m以内：" . implode('、', $nearbyList);
    }
    
    $factLines[] = "- 由来・見どころ：" . implode(' ／ ', $knowledge[$spotId]);
    $factsText   = implode("\n", $factLines);
    if (($argv[2] ?? '') === 'v') echo "  [事実行]\n" . preg_replace('/^/m', '    ', $factsText) . "\n";   // 確認用：渡した事実行を表示
    
    $prompt = "次の「事実」だけを使って、ロケ地データベースに載せる説明文を日本語で100〜200字で書いてください。\n"
    . "住所は本文に直接書かず、必ず「{$placeholder}」と書いてください。\n"
    . "説明文の1文目は必ず「{$placeholder}に…」で始めてください（例：「{$placeholder}に位置する寺院で、…」）。\n"
    . "住所の記号「{$placeholder}」は文頭の1回だけ使い、本文の途中や文末では使わないでください。\n"
    . "「位置する」という言葉は1回だけ使い、同じ言い回しを繰り返さないでください。\n"
    . "「由来・見どころ」は「／」で区切られた事実です。その内容だけを使い、足さないでください。事実が多いときは、場所の特徴がわかるものを2〜3個選び、全部を並べず、全体を200字以内にしてください。\n"
    . "場所の情報（「入り口付近」「山頂」「上部」など）は省略せず、本文に残してください。\n"
    . "事実行にない比較や評価は書かないでください（例：「低い」「高い」「価値がある」「知られる」「溶け込む」「調和する」）。\n"
    . "事実にないこと（建物の見た目・デザイン・雰囲気・眺め・治安・混雑・営業時間・撮影許可）は書かないでください。\n"
    . "事実行にない性格づけ（「歴史的」「伝統的」「特徴」など）は書かないでください。近隣の施設は名前と種別だけを書き、性質を足さないでください。\n"
    . "漢字は事実行の表記のまま書き、旧字体や別の字に置き換えないでください（例：「鉄」を「鐵」にしない）。\n"
    . "方角（北西など）の推測は書かないでください。距離は事実行の数値だけを使い、地形の種類（丘陵地など）も事実行にあるものだけを使ってください。\n"
    . "「福岡市」という語は書かないでください（住所は{$placeholder}に任せる）。数字は事実行にあるものだけを、そのまま使ってください。\n"
    . "近隣の施設名の後ろの「（歴史的建築・遺跡、…m）」などは種別名です。「歴史的建築や遺跡がある」のように文に書かず、施設の名前だけを書いてください。評価（「恵まれている」「便が良い」など）や機能の説明も書かないでください。\n"
    . "文末は常体に統一し（「〜である」「〜ある」「〜いる」など）、「です・ます」は使わないでください。「あるである」のような重ね言葉にしないでください。\n"
. "「〜に適しています」「さまざまな作品に」などの決まり文句や宣伝文は使わないでください。説明文の本文だけを出力してください。\n\n"
    . "文字「鉄」は、事実行でも本文でも必ず「{鉄}」と書いてください（例：「地下{鉄}」「西日本{鉄}道」）。\n\n"
    . "事実：\n" . str_replace('鉄', '{鉄}', $factsText);
    
    // ----- 生成と判定 -----
    $tag = "prod_{$spotId}_{$stamp}";
    $gen = generate_clean(
    $ollamaEndpoint, $model, $prompt, $maxTry, $temperatures, $saveDir, $tag,
    $address, $placeholder, $forbiddenWords, $allowedComparisons, $phraseFixes, $factsText
    );
    
    if ($gen['text'] === null) {
        echo "  失敗（全試行で不良）\n";
        foreach ($gen['log'] as $l) {
            echo "    try{$l['try']}（temp {$l['temp']}）：{$l['problem']}\n";
        }
        echo "\n";
        $results[] = ['id' => $spotId, 'status' => '不採用', 'text' => null];
        continue;
    }
    
    $text = preg_replace('/^(説明文|概要|詳細|出力)[：:\s]*/u', '', $gen['text']) ?? $gen['text'];
    $text = preg_replace('/^[\s"\'「」『』]+|[\s"\'「」『』]+$/u', '', $text) ?? $text;
    
    $status = (mb_strlen($text) < 50) ? '不採用' : '採用';
    $tries  = count($gen['log']);
    echo "  {$status}（試行数 {$tries}、後処理 {$gen['fixed']}件）\n";
    echo "  {$text}\n\n";
    
    $results[] = ['id' => $spotId, 'name' => $spotName, 'status' => $status, 'text' => $text, 'tries' => $tries];
}

// ===== 2. 結果の一覧 =====
echo "===== 結果一覧 =====\n";
echo "地点ID | 状態 | 試行数\n";
echo "-------|------|------\n";
foreach ($results as $r) {
    $tries = $r['tries'] ?? '-';
    echo "{$r['id']} | {$r['status']} | {$tries}\n";
}

// ===== 3. 書き込み（ドライランでは送らない）=====
$adopted = array_filter($results, fn($r) => $r['status'] === '採用');
echo "\n採用：" . count($adopted) . " 件\n";
// v11：採用文を保存する。送信はこのファイルの文だけを使う（再生成しない）
$adoptedList = [];
foreach ($adopted as $r) {
    $adoptedList[] = ['spot_id' => $r['id'], 'name' => $r['name'] ?? '', 'summary' => $r['text']];
}
$outFile = $saveDir . "/adopted_{$stamp}.json";
file_put_contents($outFile, json_encode([
    'model' => $model, 'prompt_version' => $promptVersion, 'created' => $stamp, 'items' => $adoptedList,
], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
echo "採用文を保存しました：{$outFile}\n";
if ($dryRun) {
    echo "（ドライラン：ai_submit は送っていません。promptVersion は {$promptVersion}）\n";
} else {
    echo "（本番書き込みは未実装。了承後に ai_submit を追加する）\n";
}