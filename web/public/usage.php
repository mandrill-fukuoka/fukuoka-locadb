<?php
// 利用の記録（ログイン必須）：直近30日の検索語・0件の検索・よく見られた地点
require __DIR__ . '/bootstrap.php';
require_login();
$error = ''; $rows = []; $names = [];
try {
    $since = gmdate('Y-m-d\TH:i:s\Z', time() - 30 * 86400);
    $rows = sb_get('search_logs?select=kind,scene,tags,area_code,result_count,start_spot,created_at'
        . '&kind=in.(spots,spot_view,route)&created_at=gte.' . $since . '&order=created_at.desc&limit=5000') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); }

$days = []; $qs = []; $zero = []; $views = []; $routes = [];
foreach ($rows as $r) {
    $d = substr((string)$r['created_at'], 0, 10);
    $days[$d][$r['kind']] = ($days[$d][$r['kind']] ?? 0) + 1;
    if ($r['kind'] === 'spots') {
        $label = trim((string)($r['scene'] ?? '')) ?: '（語なし・条件のみ）';
        $cond = (array)($r['tags'] ?? []);
        if ($r['area_code']) $cond['area'] = $r['area_code'];
        $key = $label . ($cond ? '　［' . implode('・', array_map(fn($k, $v) => "$k=$v", array_keys($cond), $cond)) . '］' : '');
        $qs[$key] = ($qs[$key] ?? 0) + 1;
        if ((int)$r['result_count'] === 0) $zero[$key] = ($zero[$key] ?? 0) + 1;
    } elseif ($r['kind'] === 'spot_view' && $r['start_spot']) {
        $views[(int)$r['start_spot']] = ($views[(int)$r['start_spot']] ?? 0) + 1;
    } elseif ($r['kind'] === 'route' && trim((string)$r['scene']) !== '') {
        $k = trim((string)$r['scene']);
        $routes[$k] = ($routes[$k] ?? 0) + 1;
        if ((int)$r['result_count'] === 0) $zero['［流れ］' . $k] = ($zero['［流れ］' . $k] ?? 0) + 1;
    }
}
arsort($qs); arsort($zero); arsort($views); arsort($routes); krsort($days);
$topViews = array_slice($views, 0, 20, true);
if ($topViews) {
    try {
        foreach (sb_get('spots?select=id,name,address&id=in.(' . implode(',', array_keys($topViews)) . ')') ?? [] as $sp) $names[(int)$sp['id']] = $sp;
    } catch (Throwable $e) {}
}
page_header('利用の記録', 'usage.php');
$top = fn(array $a, int $n = 20) => array_slice($a, 0, $n, true);
?>
<section class="card">
  <h1>利用の記録</h1>
  <p class="muted">直近30日・ログインしていない人の操作のみ（ボットと自分の操作は除く）。最大5,000件まで集計。訪問数・流入元は Cloudflare Web Analytics で見ます。</p>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
  <h2>日ごとの件数</h2>
  <table class="tbl"><tr><th>日</th><th>場所を探す</th><th>地点ページ</th><th>流れで探す</th></tr>
  <?php foreach (array_slice($days, 0, 14, true) as $d => $c): ?>
    <tr><td><?= h($d) ?></td><td><?= (int)($c['spots'] ?? 0) ?></td><td><?= (int)($c['spot_view'] ?? 0) ?></td><td><?= (int)($c['route'] ?? 0) ?></td></tr>
  <?php endforeach; ?></table>
  <h2>0件だった検索（辞書を育てる材料）</h2>
  <?php if (!$zero): ?><p class="muted">なし</p><?php else: ?>
  <table class="tbl"><?php foreach ($top($zero) as $k => $n): ?><tr><td><?= h($k) ?></td><td><?= (int)$n ?>回</td></tr><?php endforeach; ?></table><?php endif; ?>
  <h2>よく探された語・条件</h2>
  <?php if (!$qs): ?><p class="muted">まだありません</p><?php else: ?>
  <table class="tbl"><?php foreach ($top($qs) as $k => $n): ?><tr><td><?= h($k) ?></td><td><?= (int)$n ?>回</td></tr><?php endforeach; ?></table><?php endif; ?>
  <h2>よく見られた地点</h2>
  <?php if (!$topViews): ?><p class="muted">まだありません</p><?php else: ?>
  <table class="tbl"><?php foreach ($topViews as $id => $n): $sp = $names[$id] ?? null; ?>
    <tr><td><a href="spot.php?id=<?= (int)$id ?>"><?= h($sp['name'] ?? ('地点' . $id)) ?></a> <span class="muted"><?= h($sp['address'] ?? '') ?></span></td><td><?= (int)$n ?>回</td></tr>
  <?php endforeach; ?></table><?php endif; ?>
  <h2>流れ検索のシーン文</h2>
  <?php if (!$routes): ?><p class="muted">まだありません</p><?php else: ?>
  <table class="tbl"><?php foreach ($top($routes) as $k => $n): ?><tr><td><?= h($k) ?></td><td><?= (int)$n ?>回</td></tr><?php endforeach; ?></table><?php endif; ?>
</section>
<?php page_footer();
