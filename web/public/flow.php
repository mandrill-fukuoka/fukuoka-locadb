<?php
// 流れで探す：シーン文または景観の流れから、その場でルートを組み立てる
require __DIR__ . '/bootstrap.php';
// 閲覧は誰でも可。「おすすめに保存」だけはログインが必要

$isPost = $_SERVER['REQUEST_METHOD'] === 'POST';
$action = $_POST['action'] ?? 'search';
$scene  = trim($_POST['scene'] ?? '');
$area   = $_POST['area'] ?? ($_GET['area'] ?? '');
$mode   = $_POST['mode'] ?? '';
$start  = (int)($_POST['start'] ?? ($_GET['start'] ?? 0)) ?: null;
$startName = $_POST['start_name'] ?? ($_GET['start_name'] ?? '');
$seqIn  = $_POST['seq'] ?? (isset($_GET['seq']) ? explode(',', (string)$_GET['seq']) : []);
$seqIn  = array_values(array_filter(array_map('strval', (array)$seqIn)));
$query = null; $results = []; $error = ''; $msg = '';

try {
    $tags   = sb_get('tags?select=code,name,facet&order=facet,code') ?? [];
    $groups = sb_get('landscape_groups?select=code,name') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $tags = $groups = []; }
$tagNames   = array_column($tags, 'name', 'code');
$groupNames = array_column($groups, 'name', 'code');
$validGroups = array_keys($groupNames);

// 「おすすめに保存」：直前の検索結果から保存
if ($isPost && $action === 'save') {
    check_csrf();
    deny_unless_logged_in();
    $key = (string)($_POST['route_key'] ?? '');
    $route = $_SESSION['last_routes'][$key] ?? null;
    try {
        if ($route) { sb_rpc('upsert_route', ['p_route' => $route, 'p_origin' => 'search', 'p_user' => current_user()]);
                      $_SESSION['saved_keys'][$key] = true; $msg = '「おすすめの流れ」に保存しました'; }
        else { $error = '保存する候補が見つかりません。もう一度検索してください。'; }
    } catch (Throwable $e) { $error = $e->getMessage(); }
    // 直前の結果を再表示
    $results = array_values($_SESSION['last_routes'] ?? []);
    $query = $_SESSION['last_query'] ?? null;
    $scene = $_SESSION['last_scene'] ?? '';
}

// 検索
if ($isPost && $action === 'search' && !$error) {
    check_csrf();
    if ($scene === '' && !$seqIn && !$start) {
        $error = 'シーン文を入力するか、景観の流れを指定してください。';
    } elseif (!current_user() && !rate_limit_ok('search', (int)cfg('public_search_per_hour', 30), 3600)) {
        $error = '短時間に検索が集中したため、一時的に検索を止めています。しばらくしてからもう一度お試しください。';
    } else {
        try {
            // ログインなしのAI解釈は全員合計で1日の上限まで（超えたらキーワードによる簡易解釈）
            if ($scene === '') {
                $query = ['sequence' => [], 'tags' => [], 'travel_mode' => 'any', 'notes' => '指定された流れで検索しました'];
            } elseif (current_user() || !cfg('anthropic_api_key')
                      || rate_limit_ok('ai', (int)cfg('public_ai_per_day', 300), 86400, false)) {
                $query = interpret_scene($scene, $tags, $groups);
            } else {
                $query = interpret_by_keywords($scene);
            }
            $seqDirect = array_values(array_filter($seqIn, fn($g) => in_array($g, $validGroups, true)));
            if ($seqDirect) { $query['sequence'] = $seqDirect; $query['notes'] = ($scene !== '' ? $query['notes'] . '（流れは直接指定を優先）' : $query['notes']); }
            if ($mode !== '') $query['travel_mode'] = $mode;
            $query['sequence'] = array_slice(array_values(array_filter($query['sequence'] ?? [], fn($g) => in_array($g, $validGroups, true))), 0, 4);
            $query['tags'] = array_filter((array)($query['tags'] ?? []), fn($w, $t) => isset($tagNames[$t]), ARRAY_FILTER_USE_BOTH);
            if (!$query['sequence'] && !$start) {
                $error = '景観の流れを読み取れませんでした。「繁華街」「路地」「川」「海」「港」などの言葉を入れるか、下の「流れを直接指定」を使ってください。';
            } else {
                $results = sb_rpc('build_routes', [
                    'p_seq' => $query['sequence'], 'p_tags' => (object)$query['tags'],
                    'p_mode' => $query['travel_mode'] ?? 'any', 'p_area' => $area ?: null,
                    'p_start' => $start, 'p_limit' => 8,
                ]) ?? [];
                // 記録（上位3件は「おすすめの流れ」に自動で蓄積）
                try {
                    sb_rpc('record_route_search', [
                        'p_scene' => $scene ?: null, 'p_seq' => $query['sequence'], 'p_tags' => (object)$query['tags'],
                        'p_mode' => $query['travel_mode'] ?? 'any', 'p_area' => $area ?: null, 'p_start' => $start,
                        'p_routes' => $results, 'p_user' => current_user()]);
                } catch (Throwable $e) { error_log('record_route_search: ' . $e->getMessage()); }
                $_SESSION['last_routes'] = [];
                foreach ($results as $r) $_SESSION['last_routes'][$r['route_key']] = $r;
                $_SESSION['last_query'] = $query; $_SESSION['last_scene'] = $scene;
            }
        } catch (Throwable $e) { $error = $e->getMessage(); }
    }
}

try { $popular = sb_rpc('popular_sequences', ['p_limit' => 8]) ?? []; } catch (Throwable $e) { $popular = []; }
$formSeq = $seqIn ?: [];

page_header('流れで探す', 'flow.php');
?>
<form method="post" class="hero">
  <h1>どんなシーンを撮りますか</h1>
  <?= csrf_field() ?>
  <input type="hidden" name="action" value="search">
  <?php if ($start): ?>
    <input type="hidden" name="start" value="<?= (int)$start ?>"><input type="hidden" name="start_name" value="<?= h($startName) ?>">
    <p class="startbox">起点：<strong><?= h($startName ?: ('地点 ' . $start)) ?></strong>
      <a class="small" href="flow.php">起点を外す</a></p>
  <?php endif; ?>
  <label class="scene"><span class="sr">シーン要件</span>
    <textarea name="scene" rows="2" placeholder="例：30代の男性が深夜、一人で歩く。繁華街から路地を抜けると、川沿いに出る"><?= h($scene) ?></textarea>
  </label>
  <details class="seqbox" <?= $formSeq ? 'open' : '' ?>>
    <summary>景観の流れを直接指定する</summary>
    <div class="row">
      <?php for ($k = 0; $k < 4; $k++): ?>
        <label><?= $k + 1 ?>番目<?= $k ? '' : ($start ? '（起点の次）' : '') ?>
          <select name="seq[]"><option value="">—</option>
            <?php foreach ($groupNames as $code => $name): ?>
              <option value="<?= h($code) ?>" <?= ($formSeq[$k] ?? '') === $code ? 'selected' : '' ?>><?= h($name) ?></option>
            <?php endforeach; ?>
          </select></label>
      <?php endfor; ?>
    </div>
  </details>
  <div class="row">
    <label>エリア（起点）
      <select name="area"><option value="">すべて</option>
        <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?>
      </select>
    </label>
    <label>移動
      <select name="mode"><option value="">シーンから判断</option>
        <option value="walk" <?= $mode === 'walk' ? 'selected' : '' ?>>徒歩（15分以内）</option>
        <option value="car" <?= $mode === 'car' ? 'selected' : '' ?>>車（15分以内）</option>
      </select>
    </label>
  </div>
  <button>ルートを組み立てる</button>
  <?php if ($popular): ?>
    <p class="small muted popular">よく探される流れ：
      <?php foreach ($popular as $p): $seq = $p['seq'] ?? []; ?>
        <a class="chip" href="flow.php?seq=<?= h(implode(',', $seq)) ?>"><?= h(implode(' → ', array_map(fn($g) => $groupNames[$g] ?? $g, $seq))) ?>（<?= (int)$p['times'] ?>）</a>
      <?php endforeach; ?></p>
  <?php endif; ?>
</form>

<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($query && ($results || !$error)): ?>
  <section class="interp">
    <h2>検索条件</h2>
    <p><span class="label">景観の流れ</span><?= $start ? '起点 → ' : '' ?><?= implode(' <span class="muted">→</span> ', array_map(fn($g) => dot($g) . h($groupNames[$g] ?? $g), $query['sequence'] ?? [])) ?></p>
    <?php if (!empty($query['tags'])): ?>
      <p><span class="label">雰囲気</span>
        <?php foreach ($query['tags'] as $t => $w): ?><span class="chip"><?= h($tagNames[$t] ?? $t) ?></span><?php endforeach; ?></p>
    <?php endif; ?>
    <?php if (!empty($query['notes'])): ?><p class="notes"><?= h($query['notes']) ?></p><?php endif; ?>
  </section>

  <?php if (!$results): ?>
    <p class="card">条件に合うルートを組み立てられませんでした。景観の数を減らす、移動を「車」にする、エリアを「すべて」にする、などを試してください。</p>
  <?php endif; ?>

  <?php foreach ($results as $i => $r):
      $key = $r['route_key'] ?? '';
      render_route_card($r, $i, ['save_key' => current_user() ? $key : null, 'saved' => !empty($_SESSION['saved_keys'][$key])]);
  endforeach; ?>
<?php endif; ?>

<?php leaflet_script(); page_footer();
