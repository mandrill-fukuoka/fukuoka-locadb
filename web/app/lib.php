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
// 地点名の表示：名前のない地点（「（無名）種別（町丁目）」）は「町丁目の種別」と表示する
function nm($name): string {
    $name = (string)$name;
    if (preg_match('/^（無名）(.+?)（(.+)）$/u', $name, $m)) return $m[2] . 'の' . $m[1];
    if (preg_match('/^（無名）(.+)$/u', $name, $m)) return $m[1] . '（名称なし）';
    return $name;
}

// ---------- 説明文の公開 ----------
// spots.summary には人が書いた・採用した説明文しか入らない（DB変更130の制約：summary_by が必須）。
// AIの文章は spot_ai_notes の下書きにあり、確認画面で人が採用したときだけ spots.summary に入る。だから説明文はそのまま出す
function summary_html(array $s, string $tag, string $class): string {
    $text = trim((string)($s['summary'] ?? ''));
    if ($text === '') return '';
    return '<' . $tag . ' class="' . h($class) . '">' . h($text) . '</' . $tag . '>';
}
const AI_SOURCE_LABEL = ['laya' => 'LAYA', 'claude' => 'Claude'];
// 事実カード（DB変更180、タスクH）：項目は固定。承認した事実だけを出す
const FACT_ITEMS = ['管理者・所有者', '創設・開設年', '規模', '現況', '指定・登録', '人口'];
const FACT_SOURCE_LABEL = ['wikipedia' => 'Wikipedia', 'web' => '公式ページ', 'osm' => 'OpenStreetMap', 'manual' => '人が入力'];

// ---------- ログイン ----------
function current_user(): ?string { return $_SESSION['user'] ?? null; }

function require_login(): void {
    if (!current_user()) { header('Location: login.php'); exit; }
}
// ログインしていない人が編集操作を送ってきたとき
function deny_unless_logged_in(): void {
    if (!current_user()) { http_response_code(403); exit('この操作にはログインが必要です'); }
}

// ---------- 利用回数の制限（ログインなしの検索・AI解釈用） ----------
// $perIp=false にすると全員の合計で数える
function rate_limit_ok(string $bucket, int $max, int $windowSec, bool $perIp = true): bool {
    $who = $perIp ? ($_SERVER['REMOTE_ADDR'] ?? 'x') : 'all';
    $file = sys_get_temp_dir() . '/locadb_rl_' . hash('sha256', $bucket . '|' . $who . '|' . (string)cfg('supabase_url', ''));
    $fp = @fopen($file, 'c+');
    if (!$fp) return true;
    flock($fp, LOCK_EX);
    $now = time();
    $ts = json_decode((string)stream_get_contents($fp), true);
    $ts = array_values(array_filter(is_array($ts) ? $ts : [], fn($t) => is_int($t) && $t > $now - $windowSec));
    $ok = count($ts) < $max;
    if ($ok) $ts[] = $now;
    ftruncate($fp, 0); rewind($fp); fwrite($fp, json_encode($ts));
    flock($fp, LOCK_UN); fclose($fp);
    return $ok;
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
    $headers = ['apikey: ' . $key, 'Content-Type: application/json', 'User-Agent: locadb-server/1.0'];
    // 旧形式（eyJ...のJWT）のみ Authorization にも入れる。新形式 sb_secret_... は apikey ヘッダーのみ
    if (strncmp($key, 'sb_', 3) !== 0) $headers[] = 'Authorization: Bearer ' . $key;
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
        . "sequence はシーン内で景観が移り変わる順（1〜4個）。川は river、海・海辺・港の水際は sea、砂浜は beach、池・湖・お堀は lake を使い分け、"
        . "どの水辺でもよい場合だけ water を使う。tags の重みは0〜1。";
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
        'port_industrial' => '倉庫|工場|コンテナ|ふ頭|埠頭|港湾',
        'river' => '川|河川|河原|河川敷|運河|川沿い|川べり',
        'sea' => '海(?!水浴)|海辺|海岸|岸壁|堤防|防波堤|漁港|干潟|湾|港',
        'beach' => '砂浜|ビーチ|浜辺|海水浴|波打ち際',
        'lake' => '池|湖|ため池|溜池|貯水池|ダム湖|お堀|堀端',
        'water' => '水辺|水際',
        'green' => '公園|山|丘|森|緑', 'historic' => '神社|寺|歴史|城', 'vacant' => '廃墟|空き|廃校|閉鎖',
        'slope' => '坂道|坂の|坂を|急な坂|上り坂|下り坂|階段|石段|高低差|高台|見下ろ|見晴らし',
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
                      'verified' => '確認済み'];
const DATA_STATUS_LABEL = ['current' => '掲載中', 'needs_review' => '要確認', 'archived' => '対象外'];
const POSITION_LABEL = ['point' => 'OSMの地点の位置', 'area' => '敷地・範囲の代表点（実際の撮影位置とは限りません）',
                        'line_sample' => '線（川・線路など）の上の位置', 'standpoint' => '線（川・線路など）に近い道の上の位置',
                        'manual' => '手入力の位置'];
const TERRAIN_SRC_LABEL = ['dem5a' => '国土地理院 5mメッシュ・レーザ測量', 'dem5b' => '国土地理院 5mメッシュ・写真測量',
                           'dem10b' => '国土地理院 10mメッシュ'];
const CONF_LABEL = ['unverified' => '未確認', 'ai_estimated' => 'AI推定', 'desk_checked' => '資料確認済み',
                    'field_checked' => '現地確認済み'];
// 確認状態の段階（一覧・流れ・地点ページ共通）：現地確認済み／資料確認済み／事実あり／機械取得のみ
function conf_stage(array $s): string {
    $c = (string)($s['confidence'] ?? '');
    if ($c === 'field_checked') return '現地確認済み';
    if ($c === 'desk_checked') return '資料確認済み';
    if ((int)($s['fact_count'] ?? 0) > 0) return '事実あり';
    return $c === 'ai_estimated' ? 'AI推定' : '機械取得のみ';
}
// 写真の出典表示（作者・ライセンス・出典ページ。Wikimedia Commons の再利用の決まり：作者表示とライセンスへのリンク）
// 写真の状態（ログイン時だけ出す。has＝採用済みは写真そのものが出るので表示しない）
const PHOTO_STATE_LABEL = ['has' => '写真あり', 'cand' => '写真候補あり（未確認）', 'none' => '写真候補なし（探索済み）', 'todo' => '写真は未探索'];
function photo_state_chip(array $s): string {
    if (!current_user()) return '';
    $st = (string)($s['photo_state'] ?? '');
    return isset(PHOTO_STATE_LABEL[$st]) ? '<span class="chip dim">' . h(PHOTO_STATE_LABEL[$st]) . '</span>' : '';
}
function photo_state_filter(): string {
    $v = (string)($_GET['photo'] ?? '');
    return current_user() && isset(PHOTO_STATE_LABEL[$v]) ? $v : '';
}

function photo_credit_html(array $p): string {
    $by = trim((string)($p['author'] ?? $p['by'] ?? ''));
    $lic = (string)($p['license'] ?? '');
    $out = '写真：' . ($by !== '' ? h($by) : '作者不明');
    if ($lic !== '' && $lic !== 'own') {
        $lu = (string)($p['license_url'] ?? '');
        $out .= '・' . ($lu !== '' && strpos($lu, 'https://') === 0
            ? '<a href="' . h($lu) . '" target="_blank" rel="noopener">' . h($lic) . '</a>' : h($lic));
    }
    $src = (string)($p['source_url'] ?? '');
    if ($src !== '' && strpos($src, 'https://') === 0) {
        $out .= '・<a href="' . h($src) . '" target="_blank" rel="noopener">'
              . (strpos($src, 'https://www.mapillary.com/') === 0 ? 'Mapillary' : 'Wikimedia Commons') . '</a>';
    }
    return $out;
}
const RISK_LABEL = ['redevelopment' => '再開発', 'demolition' => '解体', 'closure' => '閉店・閉館',
                    'use_change' => '用途変更', 'none' => 'なし'];
const HISTORY_LABEL = ['created' => '新規登録', 'moved' => '位置の更新', 'renamed' => '名前の更新', 'recategorized' => '種別の更新',
    'linked' => '同じ場所のデータを統合', 'relinked' => 'OSMデータの付け替え', 'missing' => 'OSMで見つからず', 'archived' => '対象外（自動）',
    'restored' => '復帰（自動）', 'needs_review' => '要確認', 'reviewed' => '確認', 'area' => '区の更新', 'reclassify' => 'AI分類のやり直し',
    'address' => '住所', 'resolve' => '要確認を解消', 'archive' => '対象外にした', 'restore' => '掲載に戻した', 'lock' => '位置を固定',
    'unlock' => '固定を外した', 'merged' => '重複を統合', 'merged_into' => '別の地点に統合',
    'summary_to_draft' => '説明文を下書きへ移した', 'summary_accepted' => '説明文の下書きを採用', 'summary_rejected' => '説明文の下書きを却下', 'practicals' => '撮影実務の更新', 'check_dismissed' => '自動チェックの指摘を「問題なし」で閉じた'];
const PERMIT_REQUIRED_LABEL = ['required' => '許可が必要', 'consult' => '事前の相談が必要', 'not_required' => '許可は不要（公道からの撮影など）',
                               'unknown' => '不明'];
const SOURCE_KIND_LABEL = ['official' => '公式の情報', 'news' => '報道', 'field' => '現地で確認', 'review' => '確認メモ', 'legacy' => '旧版のデータ'];

// ---------- 住所（国土地理院の逆ジオコーダー。無料・キー不要）。取れなければ null（次の収集で入る） ----------
const GSI_MUNI = ['40131' => '福岡市東区', '40132' => '福岡市博多区', '40133' => '福岡市中央区', '40134' => '福岡市南区',
                  '40135' => '福岡市西区', '40136' => '福岡市城南区', '40137' => '福岡市早良区'];
function gsi_address(float $lat, float $lon): ?string {
    $ch = curl_init('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?'
                    . http_build_query(['lat' => sprintf('%.6f', $lat), 'lon' => sprintf('%.6f', $lon)]));
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 8,
                            CURLOPT_HTTPHEADER => ['User-Agent: fukuoka-locadb/2.0 (location research)']]);
    $res = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($res === false || $code !== 200) return null;
    $r = json_decode((string)$res, true)['results'] ?? null;
    if (!is_array($r)) return null;
    $addr = (GSI_MUNI[$r['muniCd'] ?? ''] ?? '') . str_replace('－', '', (string)($r['lv01Nm'] ?? ''));
    return $addr !== '' ? $addr : null;
}
const MODE_LABEL = ['walk' => '徒歩', 'car' => '車', 'mixed' => '徒歩＋車', 'any' => '指定なし'];
const AREA_LABEL = ['fuk-chuo' => '中央区', 'fuk-hakata' => '博多区', 'fuk-higashi' => '東区',
                    'fuk-sawara' => '早良区', 'fuk-nishi' => '西区', 'fuk-minami' => '南区', 'fuk-jonan' => '城南区'];

function page_header(string $title, string $active = ''): void {
    $nav = ['index.php' => '場所を探す', 'flow.php' => '流れで探す', 'flows.php' => 'おすすめの流れ', 'list.php' => 'ロケハンリスト'];
    if (current_user()) $nav += ['spots.php' => '地点の確認', 'risks.php' => '変化リスク', 'runs.php' => '収集の記録',
                                 'edit.php' => '＋地点を追加'];
    echo '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
       . '<meta name="viewport" content="width=device-width,initial-scale=1">'
       . (cfg('allow_indexing', false) ? '' : '<meta name="robots" content="noindex,nofollow">')
       . '<title>' . h($title) . ' | FUKUOKA LOCATIONS</title>'
       . '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
       . '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Noto+Sans+JP:wght@400;500&family=Shippori+Mincho:wght@600;700&display=swap">'
       . '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
       . '<link rel="stylesheet" href="assets/style.css"></head><body>'
       . '<header class="top"><a class="brand" href="index.php">FUKUOKA LOCATIONS</a>';
    echo '<nav class="mainnav">';
    foreach ($nav as $href => $label) echo '<a href="' . $href . '"' . ($active === $href ? ' class="on" aria-current="page"' : '') . '>' . $label . '</a>';
    echo current_user()
        ? '<a href="logout.php" class="out">ログアウト</a>'
        : '<a href="login.php" class="out' . ($active === 'login.php' ? ' on' : '') . '">ログイン</a>';
    echo '</nav>';
    echo '</header><main>';
}

// ---------- 景観グループの色（色ドット・ピンに使う） ----------
const GROUP_COLOR = [
    'commercial' => '#C8423A', 'old_town' => '#D27A2C', 'urban_new' => '#7A5BA6', 'residential' => '#B9547F',
    'institution' => '#4F5DA8', 'transport' => '#2F8DA0', 'port_industrial' => '#6B6F72', 'river' => '#3478C6',
    'sea' => '#1F4E8C', 'beach' => '#C49A45', 'lake' => '#2E8C7E', 'water' => '#3478C6', 'green' => '#4F8A4E',
    'historic' => '#8C6A4F', 'vacant' => '#9C978E', 'slope' => '#8A8A2E',
];
const HIGH_GROUND_M = 10;     // 「高台」：周り（半径150m）の平均より何m以上高いか（タスクF。実データの上位約5%）
// 周辺の特徴（spatial_view の段階）を1行の言葉にする。段階の境界は sql/api/10_spots.sql にある
const WATER_KIND_LABEL = ['river' => '川', 'coast' => '海', 'pond' => '池', 'beach' => '砂浜'];
function spatial_line(array $sp): string {
    $kind = WATER_KIND_LABEL[$sp['water_kind'] ?? ''] ?? '水辺';
    $w = $sp['water'] ?? 'なし';
    if ($w === 'すぐそば') $water = $kind . 'がすぐそば';
    elseif ($w === '近い') $water = $kind . 'が近い';
    else $water = '150m以内に水辺なし';
    $parts = ['開放感 ' . ($sp['open'] ?? ''), '建物 ' . ($sp['built'] ?? ''), $water];
    if (!empty($sp['road'])) $parts[] = $sp['road'];
    return implode('・', $parts);
}
// ロジ情報・同録の騒音の目安（logistics_view）。OSMに載っていない施設は「近くにない」ではなく「見つからず」とする
function logistics_line(array $lg): string {
    $parts = [];
    foreach (['toilet_m' => 'トイレ', 'convenience_m' => 'コンビニ', 'parking_m' => '駐車場'] as $k => $n) {
        if (!isset($lg[$k])) continue;
        $m = (int)$lg[$k];
        if ($m > 2000) continue;
        $parts[] = $n . ' 約' . ($m >= 1000 ? number_format($m / 1000, 1) . 'km' : $m . 'm');
    }
    return implode('・', $parts);
}
// 潮位（tide_view）：今日・明日の満潮・干潮。気象庁 潮位表 博多（QF）の予報
function tide_lines(array $tide): array {
    $out = [];
    foreach ($tide['days'] ?? [] as $i => $d) {
        $fmt = function (array $xs): string {
            return $xs ? implode('・', array_map(fn($x) => $x['t'] . '（' . (int)$x['cm'] . 'cm）', $xs)) : 'なし';
        };
        $out[] = ($i === 0 ? '今日' : '明日') . '　満潮 ' . $fmt($d['highs'] ?? []) . '　干潮 ' . $fmt($d['lows'] ?? []);
    }
    return $out;
}
const BEARING_LABEL = ['北', '北東', '東', '南東', '南', '南西', '西', '北西'];
function bearing_label(int $deg): string { return BEARING_LABEL[(int)round(($deg % 360) / 45) % 8]; }
// 景観グループ名（表示名）→ 色。表示名しか持たないデータ（ルートの地点など）用
function group_color(string $nameOrCode): string {
    static $byName = null;
    if (isset(GROUP_COLOR[$nameOrCode])) return GROUP_COLOR[$nameOrCode];
    if ($byName === null) {
        $byName = [];
        try {
            foreach (sb_get('landscape_groups?select=code,name') ?? [] as $g) $byName[$g['name']] = GROUP_COLOR[$g['code']] ?? '';
        } catch (Throwable $e) {}
    }
    return $byName[$nameOrCode] ?? '#9C978E';
}
function dot(string $nameOrCode): string {
    return '<i class="dot" style="--c:' . h(group_color($nameOrCode)) . '" aria-hidden="true"></i>';
}
// ---------- Googleマップ：地点1か所の埋め込みURL ----------
// 名前のある地点は「名前＋区名」で検索させ、Googleの施設情報（写真・住所など）を表示する。
// 無名の地点や道路名などは座標で表示する。
function place_embed_url(array $s, string $areaCode = ''): string {
    $name = (string)($s['name'] ?? '');
    $useName = $name !== '' && strpos($name, '（無名）') !== 0
        && !preg_match('/(号線|線路|道路|通り)$/u', $name);
    $q = $useName ? $name . ' 福岡市' . (AREA_LABEL[$areaCode ?: ($s['area_code'] ?? '')] ?? '')
                  : $s['lat'] . ',' . $s['lon'];
    return 'https://www.google.com/maps/embed/v1/place?' . http_build_query([
        'key' => cfg('google_maps_embed_key'), 'q' => $q,
        'center' => $s['lat'] . ',' . $s['lon'], 'zoom' => 17, 'language' => 'ja', 'region' => 'JP']);
}
// 周辺地図（小）：座標を中心にした地図＋中央にピン。押すとGoogleマップで開く
// Googleキーがあれば Embed API（view モード・無料）、なければ OpenStreetMap
function minimap_html(array $s, int $zoom = 16): string {
    $lat = (float)$s['lat']; $lon = (float)$s['lon'];
    $link = place_link_url($s);
    $style = !empty($s['grp_code']) ? ' style="--c:' . h(group_color((string)$s['grp_code'])) . '"' : '';
    $key = (string)cfg('google_maps_embed_key', '');
    if ($key !== '') {
        $src = 'https://www.google.com/maps/embed/v1/view?' . http_build_query([
            'key' => $key, 'center' => $lat . ',' . $lon, 'zoom' => $zoom, 'maptype' => 'roadmap', 'language' => 'ja', 'region' => 'JP']);
        return '<a class="minimap"' . $style . ' href="' . h($link) . '" target="_blank" rel="noopener" title="Googleマップで開く">'
             . '<iframe loading="lazy" tabindex="-1" referrerpolicy="no-referrer-when-downgrade" src="' . h($src) . '"></iframe>'
             . '<span class="pin" aria-hidden="true"></span><span class="open">Googleマップで開く ↗</span></a>';
    }
    return '<a class="minimap"' . $style . ' href="' . h($link) . '" target="_blank" rel="noopener" title="地図で開く">'
         . '<span class="osm" data-lat="' . $lat . '" data-lon="' . $lon . '" data-zoom="' . $zoom . '"></span>'
         . '<span class="pin" aria-hidden="true"></span><span class="open">地図で開く ↗</span></a>';
}
function place_link_url(array $s): string {
    return 'https://www.google.com/maps/search/?' . http_build_query(['api' => 1, 'query' => $s['lat'] . ',' . $s['lon']]);
}

// ---------- ルートカード（流れで探す・おすすめの流れで共通） ----------
// $opt: save_key（保存ボタン）, saved（保存済み表示）, archive_id（外すボタン）, extra（メタ情報の追記）
function render_route_card(array $r, int $i, array $opt = []): void {
    $spots = $r['spots'] ?? [];
    if (!$spots) return;
    $pts = array_map(fn($s) => $s['lat'] . ',' . $s['lon'], $spots);
    $gmode = ($r['travel_mode'] ?? '') === 'car' ? 'driving' : 'walking';
    $dir = ['origin' => $pts[0], 'destination' => end($pts)];
    if (count($pts) > 2) $dir['waypoints'] = implode('|', array_slice($pts, 1, -1));
    $routeUrl = 'https://www.google.com/maps/dir/?' . http_build_query(['api' => 1] + $dir + ['travelmode' => $gmode]);
    $embedKey = cfg('google_maps_embed_key');
    $modeLabel = MODE_LABEL[$r['travel_mode'] ?? ''] ?? '';
    ?>
    <article class="route">
      <div class="route-head">
        <div class="grow">
          <div class="route-no">ROUTE <?= sprintf('%02d', $i + 1) ?></div>
          <h3 class="route-title">
            <?php foreach ($spots as $k => $s): ?><?php if ($k): ?><span class="arr" aria-label="から">→</span><?php endif; ?><span class="g"><?= dot((string)($s['grp_code'] ?? $s['group'] ?? '')) ?><?= h($s['group'] ?? '') ?></span><?php endforeach; ?>
          </h3>
          <p class="meta">
            <span><?= h(AREA_LABEL[$r['area_code'] ?? ''] ?? '') ?></span>
            <span><?= h($modeLabel) ?><?php if (count($spots) > 1): ?> 約<?= h(round((float)($r['total_minutes'] ?? 0))) ?>分<span class="muted">（直線距離からの目安）</span><?php else: ?>（単独の地点）<?php endif; ?></span>
            <?php if (!empty($r['needs_review'])): ?><span class="chip warn" title="<?= h($r['review_note'] ?? '') ?>">地点に変化あり・要再確認</span><?php endif; ?>
            <?php if (isset($r['match'])): ?><span>適合度 <?= h(round((float)$r['match'] * 100)) ?>%</span><?php endif; ?>
            <?= $opt['extra'] ?? '' ?></p>
        </div>
        <div class="head-actions">
        <?php if (!empty($opt['saved'])): ?>
          <span class="chip ok">★ 保存済み</span>
        <?php elseif (!empty($opt['save_key'])): ?>
          <form method="post" class="inline"><?= csrf_field() ?>
            <input type="hidden" name="action" value="save"><input type="hidden" name="route_key" value="<?= h($opt['save_key']) ?>">
            <button class="secondary small-btn">☆ おすすめに保存</button></form>
        <?php endif; ?>
        <?php if (!empty($opt['archive_id'])): ?>
          <form method="post" class="inline"><?= csrf_field() ?>
            <input type="hidden" name="action" value="archive"><input type="hidden" name="id" value="<?= (int)$opt['archive_id'] ?>">
            <button class="secondary small-btn">外す</button></form>
        <?php endif; ?>
        </div>
      </div>
      <ol class="cuts" style="--n:<?= max(count($spots), 3) ?>">
        <?php foreach ($spots as $k => $s): $c = group_color((string)($s['grp_code'] ?? $s['group'] ?? '')); ?>
          <li class="cut" style="--c:<?= h($c) ?>">
            <div class="shot">
              <?php if (!empty($s['photo_url'])): ?>
                <img src="<?= h($s['photo_url']) ?>" alt="<?= h(nm($s['name'])) ?>" loading="lazy">
              <?php else: ?>
                <?= minimap_html($s, 16) ?>
              <?php endif; ?>
              <span class="cutno">CUT <?= $k + 1 ?></span>
            </div>
            <?php if ($k > 0): ?><span class="hop"><?= h($modeLabel) ?> <?= h($s['minutes_from_prev'] ?? '') ?>分</span><?php endif; ?>
            <div class="cap">
              <span class="grp"><i class="dot" aria-hidden="true"></i><?= h($s['group'] ?? '') ?></span>
              <strong class="name"><?php if (!empty($s['id'])): ?><a class="plain" href="spot.php?id=<?= (int)$s['id'] ?>"><?= h(nm($s['name'])) ?></a><?php else: ?><?= h(nm($s['name'])) ?><?php endif; ?></strong>
              <span class="flags"><?php if (!empty($s['risk'])): ?><span class="chip warn">変化リスク</span><?php endif; ?><?php if (($s['data_status'] ?? '') === 'archived'): ?><span class="chip warn">対象外</span><?php endif; ?><?php if (($s['confidence'] ?? '') !== 'field_checked'): ?><span class="chip dim"><?= h(conf_stage($s)) ?></span><?php endif; ?></span>
              <?= summary_html($s, 'span', 'desc') ?>
              <?php if (!empty($s['id'])): ?><span class="links"><a href="spot.php?id=<?= (int)$s['id'] ?>">詳細</a><a href="index.php?near=<?= (int)$s['id'] ?>">周辺の景観</a><a href="#" class="addlist" data-id="<?= (int)$s['id'] ?>">＋リスト</a></span><?php endif; ?>
            </div>
          </li>
        <?php endforeach; ?>
      </ol>
      <?php if ($embedKey && count($spots) === 1): ?>
        <iframe class="map" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen
          src="<?= h(place_embed_url($spots[0], (string)($r['area_code'] ?? ''))) ?>"></iframe>
      <?php elseif ($embedKey): ?>
        <iframe class="map" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen
          src="<?= h('https://www.google.com/maps/embed/v1/directions?' . http_build_query(['key' => $embedKey] + $dir
            + ['mode' => $gmode, 'language' => 'ja', 'region' => 'JP'])) ?>"></iframe>
      <?php else: ?>
        <div class="map" data-spots='<?= h(json_encode($spots, JSON_UNESCAPED_UNICODE)) ?>'></div>
      <?php endif; ?>
      <?php if (count($spots) > 1): ?>
        <p class="card-foot"><a class="gmaps-btn" target="_blank" rel="noopener" href="<?= h($routeUrl) ?>">Googleマップでルートを開く</a></p>
      <?php else: ?>
        <p class="card-foot"><a class="gmaps-btn" target="_blank" rel="noopener" href="<?= h(place_link_url($spots[0])) ?>">Googleマップで開く</a>
          <span class="muted small">景観が1つだけの検索です。流れにするには、景観を2つ以上指定してください。</span></p>
      <?php endif; ?>
    </article>
    <?php
}

// OpenStreetMap表示（Googleキー未設定時、または複数地点の一覧地図）
function leaflet_script(bool $line = true): void { ?>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
document.querySelectorAll('div.map[data-spots]').forEach(el => {
  const spots = JSON.parse(el.dataset.spots);
  if (!spots.length) return;
  const map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    { attribution: '© OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
  const pts = spots.map(s => [s.lat, s.lon]);
  spots.forEach((s, i) => {
    const label = document.createElement('span');
    label.textContent = (i + 1) + '. ' + s.name;
    L.marker([s.lat, s.lon]).addTo(map).bindTooltip(label);
  });
  <?php if ($line): ?>if (el.dataset.line !== '0') L.polyline(pts, { weight: 4, opacity: .7 }).addTo(map);<?php endif; ?>
  if (pts.length === 1) map.setView(pts[0], 16); else map.fitBounds(pts, { padding: [24, 24] });
});
document.querySelectorAll('.minimap .osm').forEach(el => {
  if (el.dataset.ready) return; el.dataset.ready = '1';
  const m = L.map(el, { zoomControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false, boxZoom: false,
    keyboard: false, touchZoom: false, attributionControl: false })
   .setView([+el.dataset.lat, +el.dataset.lon], +el.dataset.zoom || 16);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(m);
});
</script>
<?php }

// ---------- 全体地図（複数地点にピン） ----------
// Googleマップ（Maps JavaScript API）で表示。使えない場合は自動で OpenStreetMap に切り替える。
// $points: [['lat','lon','name','sub'], ...]  一覧の項目は id="{$id}-item-{番号}" にしておく。
function overview_map(array $points, string $id = 'ovmap', string $class = 'map tall'): void {
    $pts = [];
    foreach (array_values($points) as $i => $p) {
        $pts[] = ['lat' => (float)$p['lat'], 'lon' => (float)$p['lon'], 'name' => (string)($p['name'] ?? ''),
                  'sub' => (string)($p['sub'] ?? ''), 'n' => $i + 1,
                  'c' => !empty($p['grp']) ? group_color((string)$p['grp']) : '#1B1B1B'];
    }
    echo '<div id="' . h($id) . '" class="' . h($class) . ' ovmap" data-points=\'' . h(json_encode($pts, JSON_UNESCAPED_UNICODE)) . '\'></div>';
}
function overview_map_script(): void {
    $key = (string)cfg('google_maps_embed_key', '');
    $useGoogle = $key !== '' && cfg('google_maps_js', true) !== false;
    ?>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
(function () {
  const maps = {};
  const els = () => Array.from(document.querySelectorAll('div.ovmap'));
  const ptsOf = el => JSON.parse(el.dataset.points || '[]');
  window.ovFocus = (id, n) => { if (maps[id]) maps[id].focus(n); };

  function onPick(id, n) {
    document.querySelectorAll('.picked').forEach(e => e.classList.remove('picked'));
    const li = document.getElementById(id + '-item-' + n);
    if (!li) return;
    li.classList.add('picked');
    const a = li.querySelector('a.showmap');
    const f = document.getElementById('placeMap');
    if (a && f && a.dataset.src) f.src = a.dataset.src;
  }
  function infoNode(p) {           // 名前はテキストとして表示
    const d = document.createElement('div');
    const b = document.createElement('strong'); b.textContent = p.n + '. ' + p.name; d.appendChild(b);
    if (p.sub) { const s = document.createElement('div'); s.style.fontSize = '12px'; s.textContent = p.sub; d.appendChild(s); }
    return d;
  }
  function initLeaflet() {
    els().forEach(el => {
      const pts = ptsOf(el); if (!pts.length || el.dataset.ready) return;
      el.dataset.ready = '1';
      const map = L.map(el, { scrollWheelZoom: false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        { attribution: '© OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
      const mks = pts.map(p => {
        const icon = L.divIcon({ className: 'numpin', html: '<span style="background:' + p.c + '">' + p.n + '</span>', iconSize: [24, 24], iconAnchor: [12, 12] });
        const mk = L.marker([p.lat, p.lon], { icon }).addTo(map).bindPopup(infoNode(p));
        mk.on('click', () => onPick(el.id, p.n));
        return mk;
      });
      if (pts.length === 1) map.setView([pts[0].lat, pts[0].lon], 16);
      else map.fitBounds(pts.map(p => [p.lat, p.lon]), { padding: [30, 30] });
      maps[el.id] = { focus: n => { const mk = mks[n - 1]; if (!mk) return; map.setView(mk.getLatLng(), 17); mk.openPopup(); onPick(el.id, n); } };
    });
  }
  window.initGoogleOv = function () {
    els().forEach(el => {
      const pts = ptsOf(el); if (!pts.length) return;
      const map = new google.maps.Map(el, { mapTypeControl: false, streetViewControl: false, gestureHandling: 'cooperative' });
      const bounds = new google.maps.LatLngBounds();
      const iw = new google.maps.InfoWindow();
      const mks = pts.map(p => {
        const mk = new google.maps.Marker({ position: { lat: p.lat, lng: p.lon }, map, title: p.name,
          icon: { path: google.maps.SymbolPath.CIRCLE, scale: 11, fillColor: p.c, fillOpacity: 1, strokeColor: '#fff', strokeWeight: 2 },
          label: { text: String(p.n), color: '#fff', fontSize: '11px', fontWeight: '700' } });
        mk.addListener('click', () => { iw.setContent(infoNode(p)); iw.open({ anchor: mk, map }); onPick(el.id, p.n); });
        bounds.extend(mk.getPosition());
        return mk;
      });
      if (pts.length === 1) { map.setCenter(bounds.getCenter()); map.setZoom(16); } else map.fitBounds(bounds, 40);
      maps[el.id] = { focus: n => { const mk = mks[n - 1]; if (!mk) return; map.panTo(mk.getPosition()); map.setZoom(17); google.maps.event.trigger(mk, 'click'); } };
    });
  };
  // Google側で使えない（API未有効・キー制限など）ときは OpenStreetMap に切り替え
  window.gm_authFailure = function () {
    els().forEach(el => { const n = el.cloneNode(false); n.removeAttribute('style'); delete n.dataset.ready; el.replaceWith(n); });
    initLeaflet();
  };
  // 周辺地図（小）の OpenStreetMap 版
  const io = 'IntersectionObserver' in window ? new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return; io.unobserve(e.target); makeMini(e.target);
  }), { rootMargin: '200px' }) : null;
  function makeMini(el) {
    if (el.dataset.ready) return; el.dataset.ready = '1';
    const m = L.map(el, { zoomControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
      boxZoom: false, keyboard: false, touchZoom: false, attributionControl: false })
      .setView([+el.dataset.lat, +el.dataset.lon], +el.dataset.zoom || 16);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(m);
  }
  document.querySelectorAll('.minimap .osm').forEach(el => io ? io.observe(el) : makeMini(el));

  <?php if ($useGoogle): ?>
  const sc = document.createElement('script');
  sc.src = 'https://maps.googleapis.com/maps/api/js?' + new URLSearchParams({
    key: <?= json_encode($key) ?>, callback: 'initGoogleOv', language: 'ja', region: 'JP', loading: 'async' });
  sc.async = true; sc.onerror = window.gm_authFailure;
  document.head.appendChild(sc);
  <?php else: ?>
  initLeaflet();
  <?php endif; ?>
})();
</script>
<?php }

function page_footer(): void {
    echo '</main><footer>FUKUOKA LOCATIONS　候補は組み合わせの提案であり、撮影の許可・立入の可否・撮影できることを保証するものではありません。'
       . '地点の位置はOpenStreetMapのデータに基づく手がかりです。移動時間は直線距離からの目安です。'
       . '地図データ © Google / <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>（ODbL）'
       . '・住所 国土地理院</footer>'
       . '<script src="assets/list.js"></script></body></html>';
}
