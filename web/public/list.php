<?php
// ロケハンリスト：選んだ地点を一覧・地図にし、CSV・Googleマップのルート・共有URLで書き出す
require __DIR__ . '/bootstrap.php';

$ids = array_values(array_unique(array_filter(array_map('intval', explode(',', (string)($_GET['ids'] ?? ''))))));
$ids = array_slice($ids, 0, 60);
$rows = []; $error = '';
if ($ids) {
    try {
        $rows = sb_rpc('spots_by_ids', ['p_ids' => $ids]) ?? [];
    } catch (Throwable $e) { $error = $e->getMessage(); }
}
$base = (isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off' ? 'https' : 'http') . '://' . ($_SERVER['HTTP_HOST'] ?? '')
      . rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? '/'), '/') . '/';

if (($_GET['format'] ?? '') === 'csv' && $rows) {
    header('Content-Type: text/csv; charset=UTF-8');
    header('Content-Disposition: attachment; filename="locations-' . date('Ymd') . '.csv"');
    $out = fopen('php://output', 'w');
    fwrite($out, "\xEF\xBB\xBF");   // Excel で文字化けしないように
    $withPhoto = (bool)current_user();      // 写真の状態はログイン時だけ（候補は未公開のため）
    fputcsv($out, array_merge(['No', '名前', '種別', '景観', '区', '住所', '緯度', '経度', '確認状況'], $withPhoto ? ['写真の状態'] : [],
        ['Googleマップ', 'ストリートビュー', '詳細ページ']));
    foreach ($rows as $i => $s) {
        fputcsv($out, array_merge([$i + 1, nm($s['name']), $s['category'], $s['grp'], AREA_LABEL[$s['area_code']] ?? '', $s['address'],
            $s['lat'], $s['lon'], conf_stage($s)], $withPhoto ? [PHOTO_STATE_LABEL[$s['photo_state'] ?? ''] ?? ''] : [], [
            place_link_url($s),
            'https://www.google.com/maps/@?' . http_build_query(['api' => 1, 'map_action' => 'pano', 'viewpoint' => $s['lat'] . ',' . $s['lon']]),
            $base . 'spot.php?id=' . $s['id']]));
    }
    exit;
}

// Googleマップのルート（目的地＋経由地は最大10か所）
$route = '';
if (count($rows) >= 2) {
    $pts = array_map(fn($s) => $s['lat'] . ',' . $s['lon'], array_slice($rows, 0, 10));
    $dir = ['origin' => $pts[0], 'destination' => end($pts)];
    if (count($pts) > 2) $dir['waypoints'] = implode('|', array_slice($pts, 1, -1));
    $route = 'https://www.google.com/maps/dir/?' . http_build_query(['api' => 1] + $dir + ['travelmode' => 'driving']);
}
$shareUrl = $base . 'list.php?ids=' . implode(',', array_column($rows, 'id'));

page_header('ロケハンリスト', 'list.php');
?>
<section class="card">
  <h1>ロケハンリスト</h1>
  <p class="small muted">「＋ロケハンリスト」を押した地点がここに集まります（このブラウザに保存）。URLを送れば、同じリストを相手も開けます。</p>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
  <?php if (!$rows): ?>
    <p class="muted" id="emptymsg">まだ地点がありません。<a href="index.php">場所を探す</a>・<a href="flow.php">流れで探す</a>の候補から追加してください。</p>
  <?php else: ?>
    <p class="card-foot listtools">
      <a class="gmaps-btn" href="<?= h('list.php?' . http_build_query(['ids' => implode(',', array_column($rows, 'id')), 'format' => 'csv'])) ?>">CSVで書き出す</a>
      <?php if ($route): ?><a class="gmaps-btn" target="_blank" rel="noopener" href="<?= h($route) ?>">Googleマップでルート（先頭10か所）</a><?php endif; ?>
      <button type="button" class="secondary small-btn" id="copyurl" data-url="<?= h($shareUrl) ?>">共有用URLをコピー</button>
      <button type="button" class="secondary small-btn" onclick="window.print()">印刷</button>
      <button type="button" class="secondary small-btn" id="savelist" data-ids="<?= h(implode(',', array_column($rows, 'id'))) ?>">このリストを自分のリストにする</button>
      <button type="button" class="secondary small-btn" id="clearlist">リストを空にする</button>
    </p>
    <?php overview_map(array_map(fn($s) => ['lat' => $s['lat'], 'lon' => $s['lon'], 'name' => nm($s['name']), 'grp' => $s['grp_code'],
          'sub' => $s['category'] . '・' . ($s['address'] ?: (AREA_LABEL[$s['area_code']] ?? ''))], $rows), 'ovmap'); ?>
    <ol class="places listitems">
      <?php foreach ($rows as $i => $s): ?>
        <li id="ovmap-item-<?= $i + 1 ?>" class="place-item" data-id="<?= (int)$s['id'] ?>">
          <div class="place-body">
            <div><span class="num" style="background:<?= h(group_color($s['grp_code'])) ?>"><?= $i + 1 ?></span><strong><a class="plain" href="spot.php?id=<?= (int)$s['id'] ?>"><?= h(nm($s['name'])) ?></a></strong>
              <span class="muted"><?= dot($s['grp_code']) ?><?= h($s['category']) ?>・<?= h($s['address'] ?: (AREA_LABEL[$s['area_code']] ?? '')) ?></span>
              <?php if ($s['confidence'] !== 'field_checked'): ?><span class="chip dim"><?= h(conf_stage($s)) ?></span><?php endif; ?><?= photo_state_chip($s) ?>
              <?php if (!empty($s['risk'])): ?><span class="chip warn">変化リスク</span><?php endif; ?>
              <?php if ($s['data_status'] === 'archived'): ?><span class="chip warn">対象外</span><?php endif; ?></div>
            <div class="small actions noprint">
              <a href="#" class="mv" data-d="-1">↑ 上へ</a><a href="#" class="mv" data-d="1">↓ 下へ</a>
              <a href="#" class="rm">リストから外す</a>
              <a target="_blank" rel="noopener" href="<?= h('https://www.google.com/maps/@?' . http_build_query(['api' => 1, 'map_action' => 'pano', 'viewpoint' => $s['lat'] . ',' . $s['lon']])) ?>">ストリートビュー</a>
            </div>
            <div class="small muted printonly"><?= h(number_format((float)$s['lat'], 5)) ?>, <?= h(number_format((float)$s['lon'], 5)) ?></div>
          </div>
          <?= minimap_html($s) ?>
        </li>
      <?php endforeach; ?>
    </ol>
  <?php endif; ?>
</section>
<script>
document.addEventListener('DOMContentLoaded', function () {
  const L = window.locadbList;
  const urlIds = <?= json_encode(array_map('intval', array_column($rows, 'id'))) ?>;
  const mine = L.read();
  // 自分のリストを開いたとき（URLに ids がない）
  if (!urlIds.length && mine.length) { location.replace('list.php?ids=' + mine.join(',')); return; }
  const isMine = urlIds.length && urlIds.join(',') === mine.join(',');
  const save = document.getElementById('savelist');
  if (save && isMine) save.hidden = true;
  save && save.addEventListener('click', () => { L.write(urlIds); save.hidden = true; });
  const go = ids => location.replace(ids.length ? 'list.php?ids=' + ids.join(',') : 'list.php');
  document.querySelectorAll('.listitems li').forEach(li => {
    const id = Number(li.dataset.id);
    li.querySelector('.rm').addEventListener('click', e => { e.preventDefault(); const ids = urlIds.filter(x => x !== id); if (isMine) L.write(ids); go(ids); });
    li.querySelectorAll('.mv').forEach(a => a.addEventListener('click', e => {
      e.preventDefault();
      const ids = urlIds.slice(), i = ids.indexOf(id), j = i + Number(a.dataset.d);
      if (j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      if (isMine) L.write(ids);
      go(ids);
    }));
  });
  const clr = document.getElementById('clearlist');
  clr && clr.addEventListener('click', () => { if (isMine) L.write([]); go([]); });
  const cp = document.getElementById('copyurl');
  cp && cp.addEventListener('click', () => {
    navigator.clipboard && navigator.clipboard.writeText(cp.dataset.url).then(() => { cp.textContent = 'コピーしました'; });
  });
});
</script>
<?php overview_map_script(); page_footer();
