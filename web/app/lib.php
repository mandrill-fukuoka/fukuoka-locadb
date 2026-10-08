<?php
// 共通処理：設定・ログイン・Supabase呼び出し・Claude呼び出し
declare(strict_types=1);

$CONFIG = require __DIR__ . '/config.php';

ini_set('session.cookie_httponly', '1');
ini_set('session.use_strict_mode', '1');
$isHttps = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
    || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https') || (($_SERVER['SERVER_PORT'] ?? '') == 443);
if ($isHttps) ini_set('session.cookie_secure', '1');
ini_set('session.cookie_samesite', 'Lax');
session_set_cookie_params(['path' => rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? '/'), '/') . '/']);
session_name('locadb');
session_start();

function cfg(string $key, $default = null) { global $CONFIG; return $CONFIG[$key] ?? $default; }
function h($s): string { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

// ---------- ログイン ----------
function current_user(): ?string { return $_SESSION['user'] ?? null; }

function require_login(): void {
    if (!current_user()) { header('Location: login.php'); exit; }
}

function try_login(string $user, string $pass): bool {
    $users = cfg('users', []);
    if (isset($users[$user]) && password_verify($pass, $users[$user])) {
        session_regenerate_id(true);
        $_SESSION['user'] = $user;
        return true;
    }
    usleep(800000); // 総当たり対策
    return false;
}

function csrf_token(): string {
    if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(16));
    return $_SESSION['csrf'];
}
function csrf_field(): string { return '<input type="hidden" name="csrf" value="' . h(csrf_token()) . '">'; }
function check_csrf(): void {
    if (!hash_equals($_SESSION['csrf'] ?? '', $_POST['csrf'] ?? '')) { http_response_code(400); exit('不正なリクエストです'); }
}

// ---------- Supabase（REST / RPC） ----------
function sb_request(string $method, string $path, $body = null) {
    $url = rtrim(cfg('supabase_url'), '/') . '/rest/v1/' . ltrim($path, '/');
    $key = cfg('supabase_service_key');
    $ch = curl_init($url);
    $headers = ['apikey: ' . $key, 'Authorization: Bearer ' . $key, 'Content-Type: application/json'];
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_HTTPHEADER => $headers,
    ]);
    if ($body !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body, JSON_UNESCAPED_UNICODE));
    $res = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false || $code >= 400) {
        error_log("Supabase $method $path → $code $err $res");
        throw new RuntimeException('データベースに接続できませんでした（' . $code . '）');
    }
    return $res === '' ? null : json_decode($res, true);
}
function sb_rpc(string $fn, array $args = []) { return sb_request('POST', 'rpc/' . $fn, (object)$args); }
function sb_get(string $pathWithQuery) { return sb_request('GET', $pathWithQuery); }

// ---------- シーン文 → 検索条件 ----------
function interpret_scene(string $scene, array $tags, array $groups): array {
    $key = cfg('anthropic_api_key');
    if (!$key) return interpret_by_keywords($scene);

    $prompt = "映像のシーン要件をロケ地検索条件に変換してください。JSONのみ出力。\n\n# シーン\n$scene\n\n"
        . "# 景観グループ\n" . json_encode($groups, JSON_UNESCAPED_UNICODE) . "\n\n"
        . "# タグ\n" . json_encode($tags, JSON_UNESCAPED_UNICODE) . "\n\n"
        . "# 出力形式\n{\"sequence\": [\"commercial\",\"water\"], \"tags\": {\"lonely\": 1.0}, "
        . "\"travel_mode\": \"walk|car|any\", \"notes\": \"解釈の要点を1文で\"}\n"
        . "sequence はシーン内で景観が移り変わる順（1〜4個）。tags の重みは0〜1。";
    $ch = curl_init('https://api.anthropic.com/v1/messages');
    curl_setopt_array($ch, [
        CURLOPT_POST => true, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 60,
        CURLOPT_HTTPHEADER => ['x-api-key: ' . $key, 'anthropic-version: 2023-06-01', 'content-type: application/json'],
        CURLOPT_POSTFIELDS => json_encode([
            'model' => cfg('claude_model', 'claude-sonnet-5-5'),
            'max_tokens' => 1500,
            'messages' => [['role' => 'user', 'content' => $prompt]],
        ], JSON_UNESCAPED_UNICODE),
    ]);
    $res = json_decode((string)curl_exec($ch), true);
    curl_close($ch);
    $text = '';
    foreach (($res['content'] ?? []) as $b) if (($b['type'] ?? '') === 'text') $text .= $b['text'];
    $s = strpos($text, '{'); $e = strrpos($text, '}');
    $q = ($s !== false && $e !== false) ? json_decode(substr($text, $s, $e - $s + 1), true) : null;
    if (!is_array($q)) { $q = interpret_by_keywords($scene); $q['notes'] = '（AI解釈に失敗したため簡易解釈）' . $q['notes']; }
    return $q;
}

// APIキー未設定時の簡易解釈
function interpret_by_keywords(string $scene): array {
    $groupWords = [
        'urban_new' => '高層|再開発|オフィス|近代|ビル街|都会', 'old_town' => '路地|下町|古い街|商店街|細い道',
        'commercial' => '繁華街|飲み屋|ネオン|中洲|天神|賑やか', 'residential' => '住宅|団地|マンション|家の前',
        'institution' => '学校|病院|役所|図書館|大学', 'transport' => '駅|線路|高架|歩道橋|橋',
        'port_industrial' => '港|倉庫|工場|コンテナ|ふ頭', 'water' => '海|川|水辺|海岸|干潟|漁港',
        'green' => '公園|山|丘|森|緑', 'historic' => '神社|寺|歴史|城', 'vacant' => '廃墟|空き|廃校|閉鎖',
    ];
    $tagWords = [
        'lonely' => '寂し|孤独|一人|ひとり', 'lively' => '賑や|活気', 'nostalgic' => '懐かし|昭和|レトロ',
        'night_ok' => '夜|深夜', 'dawn_ok' => '早朝|明け方', 'sunset_ok' => '夕方|夕景|夕日',
        'sea_view' => '海', 'river_view' => '川', 'low_traffic' => '人気のない|静か|誰もいない',
        'tense' => '緊張|追われ|逃げ', 'romantic' => '恋|デート|告白', 'futuristic' => '近未来|未来',
    ];
    $hits = [];
    foreach ($groupWords as $g => $re) if (preg_match("/($re)/u", $scene, $m, PREG_OFFSET_CAPTURE)) $hits[$g] = $m[0][1];
    asort($hits);
    $tags = [];
    foreach ($tagWords as $t => $re) if (preg_match("/($re)/u", $scene)) $tags[$t] = 1.0;
    $mode = preg_match('/(車|ドライブ|運転)/u', $scene) ? 'car' : (preg_match('/(歩|散歩)/u', $scene) ? 'walk' : 'any');
    return ['sequence' => array_slice(array_keys($hits), 0, 4), 'tags' => $tags ?: ['lonely' => 0.1],
            'travel_mode' => $mode, 'notes' => 'キーワードから簡易的に解釈しました'];
}

// ---------- 表示用ラベル ----------
const STATUS_LABEL = ['candidate' => '候補', 'ai_tagged' => 'AI分類済み', 'scouted' => 'ロケハン済み',
                      'verified' => '確認済み', 'archived' => '対象外'];
const CONF_LABEL = ['unverified' => '未確認', 'ai_estimated' => 'AI推定', 'desk_checked' => '資料確認済み',
                    'field_checked' => '現地確認済み'];
const RISK_LABEL = ['redevelopment' => '再開発', 'demolition' => '解体', 'closure' => '閉店・閉館',
                    'use_change' => '用途変更', 'none' => 'なし'];
const MODE_LABEL = ['walk' => '徒歩', 'car' => '車', 'mixed' => '徒歩＋車', 'any' => '指定なし'];
const AREA_LABEL = ['fuk-chuo' => '中央区', 'fuk-hakata' => '博多区', 'fuk-higashi' => '東区',
                    'fuk-sawara' => '早良区', 'fuk-nishi' => '西区', 'fuk-minami' => '南区', 'fuk-jonan' => '城南区'];

function page_header(string $title, string $active = ''): void {
    $nav = ['index.php' => 'シーン検索', 'spots.php' => '地点の確認', 'risks.php' => '変化リスク'];
    echo '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
       . '<meta name="viewport" content="width=device-width,initial-scale=1">'
       . '<meta name="robots" content="noindex,nofollow">'
       . '<title>' . h($title) . '｜福岡ロケーションDB</title>'
       . '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
       . '<link rel="stylesheet" href="assets/style.css"></head><body>'
       . '<header class="top"><a class="brand" href="index.php">福岡ロケーションDB</a>';
    if (current_user()) {
        echo '<nav>';
        foreach ($nav as $href => $label) echo '<a href="' . $href . '"' . ($active === $href ? ' class="on"' : '') . '>' . $label . '</a>';
        echo '<a href="logout.php" class="muted">ログアウト</a></nav>';
    }
    echo '</header><main>';
}
function page_footer(): void {
    echo '</main><footer>候補は組み合わせの提案であり、撮影可能性を保証するものではありません。'
       . '地図データ © OpenStreetMap contributors</footer></body></html>';
}
