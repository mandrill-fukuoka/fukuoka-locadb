<?php
require __DIR__ . '/bootstrap.php';
require_login();

$scene = trim($_POST['scene'] ?? '');
$area  = $_POST['area'] ?? '';
$mode  = $_POST['mode'] ?? '';
$query = null; $results = []; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && $scene !== '') {
    check_csrf();
    try {
        $tags   = sb_get('tags?select=code,name');
        $groups = sb_get('landscape_groups?select=code,name');
        $query  = interpret_scene($scene, $tags, $groups);
        if ($mode !== '') $query['travel_mode'] = $mode;
        $valid = array_column($groups, 'code');
        $seq = array_values(array_filter($query['sequence'] ?? [], fn($g) => in_array($g, $valid, true)));
        $results = sb_rpc('search_clusters', [
            'p_seq' => $seq, 'p_tags' => (object)($query['tags'] ?? []),
            'p_mode' => $query['travel_mode'] ?? 'any', 'p_area' => $area ?: null, 'p_limit' => 8,
        ]) ?? [];
        $tagNames = array_column($tags, 'name', 'code');
        $groupNames = array_column($groups, 'name', 'code');
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

page_header('シーン検索', 'index.php');
?>
<form method="post" class="card">
  <h1>シーンから探す</h1>
  <?= csrf_field() ?>
  <label>シーン要件
    <textarea name="scene" rows="3" required placeholder="例：30代の男性が深夜、一人で歩く。都会だが少し寂しい。歩いているうちに海が見える"><?= h($scene) ?></textarea>
  </label>
  <div class="row">
    <label>エリア
      <select name="area"><option value="">すべて</option>
        <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?>
      </select>
    </label>
    <label>移動
      <select name="mode"><option value="">シーンから判断</option>
        <option value="walk" <?= $mode === 'walk' ? 'selected' : '' ?>>徒歩</option>
        <option value="car" <?= $mode === 'car' ? 'selected' : '' ?>>車</option>
      </select>
    </label>
  </div>
  <button>候補を探す</button>
</form>

<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($query): ?>
  <section class="card interp">
    <h2>検索条件の解釈</h2>
    <p><?= h($query['notes'] ?? '') ?></p>
    <p><span class="label">景観の流れ</span>
      <?= h(implode(' → ', array_map(fn($g) => $groupNames[$g] ?? $g, $query['sequence'] ?? []))) ?: '指定なし' ?></p>
    <p><span class="label">雰囲気</span>
      <?php foreach (($query['tags'] ?? []) as $t => $w): ?><span class="chip"><?= h($tagNames[$t] ?? $t) ?></span><?php endforeach; ?></p>
  </section>

  <?php if (!$results): ?>
    <p class="card">該当するクラスターが見つかりませんでした。エリアや移動手段を変えて試してください。</p>
  <?php endif; ?>

  <?php foreach ($results as $i => $r): ?>
    <article class="card result">
      <div class="result-head">
        <span class="rank"><?= $i + 1 ?></span>
        <div>
          <h3><?= h($r['transition_label']) ?></h3>
          <p class="meta"><?= h(AREA_LABEL[$r['area_code']] ?? $r['area_code']) ?>・<?= h(MODE_LABEL[$r['travel_mode']] ?? '') ?>
            約<?= h(round((float)$r['total_minutes'])) ?>分・適合度 <?= h(round($r['match'] * 100)) ?>%・<?= h($r['code']) ?></p>
        </div>
      </div>
      <ol class="route">
        <?php foreach ($r['spots'] as $s): ?>
          <li>
            <?php if ($s['minutes_from_prev']): ?><span class="leg">↓ <?= h($s['minutes_from_prev']) ?>分</span><?php endif; ?>
            <strong><?= h($s['name']) ?></strong> <span class="muted"><?= h($s['group']) ?></span>
            <?php if ($s['risk']): ?><span class="chip warn">変化リスク</span><?php endif; ?>
            <?php if ($s['confidence'] !== 'field_checked'): ?><span class="chip dim"><?= h(CONF_LABEL[$s['confidence']] ?? '') ?></span><?php endif; ?>
            <?php if ($s['summary']): ?><div class="muted small"><?= h($s['summary']) ?></div><?php endif; ?>
            <a class="small" target="_blank" rel="noopener" href="https://www.google.com/maps?q=<?= h($s['lat']) ?>,<?= h($s['lon']) ?>">地図で開く</a>
          </li>
        <?php endforeach; ?>
      </ol>
      <div class="map" data-spots='<?= h(json_encode($r['spots'], JSON_UNESCAPED_UNICODE)) ?>'></div>
    </article>
  <?php endforeach; ?>
<?php endif; ?>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
document.querySelectorAll('.map').forEach(el => {
  const spots = JSON.parse(el.dataset.spots);
  const map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    { attribution: '© OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
  const pts = spots.map(s => [s.lat, s.lon]);
  spots.forEach((s, i) => {
    const label = document.createElement('span');
    label.textContent = (i + 1) + '. ' + s.name;   // 名前はテキストとして表示
    L.marker([s.lat, s.lon]).addTo(map).bindTooltip(label);
  });
  L.polyline(pts, { weight: 4, opacity: .7 }).addTo(map);
  map.fitBounds(pts, { padding: [24, 24] });
});
</script>
<?php page_footer();
