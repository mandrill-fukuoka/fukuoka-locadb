<?php
// 場所を探す（トップページ）：名前・種別・景観・雰囲気でピンポイント検索。周辺の景観表示、ここから流れを探す
require __DIR__ . '/bootstrap.php';
// 閲覧専用：ログインなしで利用できる

$q     = trim($_GET['q'] ?? '');
$area  = $_GET['area'] ?? '';
$cat   = $_GET['cat'] ?? '';
$grp   = $_GET['grp'] ?? '';
$tag   = $_GET['tag'] ?? '';
$terr  = ($_GET['terrain'] ?? '') === 'high' ? 'high' : '';      // 高台：周りより+10m以上（タスクF）
// 周辺の特徴（推定）：空間特徴（タスクI）。段階の境界は sql/api/10_spots.sql
$spOpen  = in_array($_GET['sp_open'] ?? '', ['open', 'closed'], true) ? $_GET['sp_open'] : '';
$spWater = in_array($_GET['sp_water'] ?? '', ['near', 'within150'], true) ? $_GET['sp_water'] : '';
$spBuilt = in_array($_GET['sp_built'] ?? '', ['few', 'many'], true) ? $_GET['sp_built'] : '';
$spRoad  = in_array($_GET['sp_road'] ?? '', ['narrow', 'wide'], true) ? $_GET['sp_road'] : '';
$photoSt = photo_state_filter();
$near  = (int)($_GET['near'] ?? 0);
$page  = max(0, (int)($_GET['p'] ?? 0));
$per   = 40;
$error = ''; $rows = []; $nearby = []; $nearSpot = null;

try {
    $cats   = sb_get('spot_categories?select=code,name,landscape_group&order=landscape_group,code') ?? [];
    $groups = sb_get('landscape_groups?select=code,name') ?? [];
    $tags   = sb_get('tags?select=code,name,facet&order=facet,code') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $cats = $groups = $tags = []; }
$groupNames = array_column($groups, 'name', 'code');

$searched = $q !== '' || $area !== '' || $cat !== '' || $grp !== '' || $tag !== '' || $terr !== '' || $spOpen !== '' || $spWater !== '' || $spRoad !== '' || $spBuilt !== '' || $photoSt !== '';
try {
    if ($near) {
        $nearby = sb_rpc('spots_nearby', ['p_id' => $near, 'p_radius' => 800]) ?? [];
        $ns = sb_get('spots?select=id,name,code&id=eq.' . $near) ?? [];
        $nearSpot = $ns[0] ?? null;
    }
    if ($searched) {
        $rows = sb_rpc('search_spots', [
            'p_q' => $q ?: null, 'p_area' => $area ?: null, 'p_category' => $cat ?: null,
            'p_group' => $grp ?: null, 'p_tags' => $tag ? [$tag] : null,
            'p_offset' => $page * $per, 'p_limit' => $per, 'p_relief_min' => $terr === 'high' ? HIGH_GROUND_M : null,
            'p_open' => $spOpen ?: null, 'p_water' => $spWater ?: null, 'p_road' => $spRoad ?: null, 'p_built' => $spBuilt ?: null, 'p_photo' => $photoSt ?: null]) ?? [];
    }
} catch (Throwable $e) { $error = $e->getMessage(); }

$embedKey = cfg('google_maps_embed_key');
$qs = fn(array $o) => '?' . http_build_query(array_merge(['q' => $q, 'area' => $area, 'cat' => $cat, 'grp' => $grp, 'tag' => $tag, 'terrain' => $terr,
    'sp_open' => $spOpen, 'sp_water' => $spWater, 'sp_road' => $spRoad, 'sp_built' => $spBuilt, 'photo' => $photoSt], $o));


page_header('場所を探す', 'index.php');
?>
<form method="get" class="card">
  <h1>場所を探す</h1>
  <label>名前・住所<input name="q" value="<?= h($q) ?>" placeholder="例：中洲、長浜、春吉三丁目、商店街、〇〇小学校"></label>
  <div class="row">
    <label>エリア<select name="area"><option value="">すべて</option>
      <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <label>景観<select name="grp"><option value="">すべて</option>
      <?php foreach ($groupNames as $k => $v): ?><option value="<?= h($k) ?>" <?= $grp === $k ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach; ?></select></label>
    <label>種別<select name="cat"><option value="">すべて</option>
      <?php foreach ($cats as $c): ?><option value="<?= h($c['code']) ?>" <?= $cat === $c['code'] ? 'selected' : '' ?>><?= h($c['name']) ?></option><?php endforeach; ?></select></label>
    <label>雰囲気<select name="tag"><option value="">指定なし</option>
      <?php foreach ($tags as $t): ?><option value="<?= h($t['code']) ?>" <?= $tag === $t['code'] ? 'selected' : '' ?>><?= h($t['name']) ?></option><?php endforeach; ?></select></label>
    <label>地形<select name="terrain"><option value="">指定なし</option>
      <option value="high" <?= $terr === 'high' ? 'selected' : '' ?>>高台（周りより<?= HIGH_GROUND_M ?>m以上高い）</option></select></label>
  </div>
  <div class="row">
    <label>開放感（推定）<select name="sp_open"><option value="">指定なし</option>
      <option value="open" <?= $spOpen === 'open' ? 'selected' : '' ?>>開けている</option>
      <option value="closed" <?= $spOpen === 'closed' ? 'selected' : '' ?>>囲まれている</option></select></label>
    <label>建物の多さ（推定）<select name="sp_built"><option value="">指定なし</option>
      <option value="few" <?= $spBuilt === 'few' ? 'selected' : '' ?>>少ない（まわり100mの面積率15%未満）</option>
      <option value="many" <?= $spBuilt === 'many' ? 'selected' : '' ?>>多い（30%以上）</option></select></label>
    <label>水辺（推定）<select name="sp_water"><option value="">指定なし</option>
      <option value="near" <?= $spWater === 'near' ? 'selected' : '' ?>>すぐそば（50m未満）</option>
      <option value="within150" <?= $spWater === 'within150' ? 'selected' : '' ?>>近い（150m未満）</option></select></label>
    <label>道（推定）<select name="sp_road"><option value="">指定なし</option>
      <option value="narrow" <?= $spRoad === 'narrow' ? 'selected' : '' ?>>狭い道</option>
      <option value="wide" <?= $spRoad === 'wide' ? 'selected' : '' ?>>広い道</option></select></label>
  </div>
  <?php if (current_user()): ?><div class="row">
    <label>写真の状態（ログイン時のみ）<select name="photo"><option value="">指定なし</option>
      <?php foreach (PHOTO_STATE_LABEL as $k => $v): ?><option value="<?= h($k) ?>" <?= $photoSt === $k ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach; ?></select></label>
  </div><?php endif; ?>
  <button>検索</button>
  <p class="small muted">雰囲気での絞り込みは、雰囲気のタグが付いた地点だけが対象です（「推定」のタグは、種別や位置から機械的に付けたものです）。「推定」の条件は、OpenStreetMapの建物・道・水辺から計算した目安で、周りに建物のデータが少ない地点（未判定）は対象外です。</p>
</form>

<?php if (!$searched && !$near): ?>
  <section class="card browse">
    <h2>景観から探す</h2>
    <p><?php foreach ($groupNames as $k => $v): if ($k === 'water') continue; ?><a class="chip" href="?grp=<?= h($k) ?>"><?= dot($k) ?><?= h($v) ?></a><?php endforeach; ?>
      <a class="chip" href="?terrain=high">高台（周りより<?= HIGH_GROUND_M ?>m以上高い）</a></p>
    <h2>エリアから探す</h2>
    <p><?php foreach (AREA_LABEL as $k => $v): ?><a class="chip" href="?area=<?= h($k) ?>"><?= h($v) ?></a><?php endforeach; ?></p>
    <p class="small muted">シーンの流れ（例：繁華街から路地を抜けて川へ）から撮影ルートを組み立てるときは、<a href="flow.php">流れで探す</a>を使ってください。</p>
  </section>
<?php endif; ?>

<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($near && $nearSpot): ?>
  <section class="card">
    <h2>「<?= h(nm($nearSpot['name'])) ?>」の周辺（約800m以内）</h2>
    <p><a class="gmaps-btn" href="flow.php?<?= h(http_build_query(['start' => $near, 'start_name' => nm($nearSpot['name'])])) ?>">ここを起点に流れを探す</a></p>
    <?php if (!$nearby): ?><p class="muted">周辺に登録地点がありません。</p><?php else: ?>
      <?php overview_map(array_map(fn($n) => ['lat' => $n['lat'], 'lon' => $n['lon'], 'name' => nm($n['name']), 'grp' => $n['grp_code'] ?? '',
            'sub' => $n['grp'] . '・' . (int)$n['meters'] . 'm'], $nearby), 'nearmap', 'map'); ?>
      <ul class="nearby">
        <?php foreach ($nearby as $k => $n): ?>
          <li id="nearmap-item-<?= $k + 1 ?>"><span class="num"><?= $k + 1 ?></span><strong><a class="plain" href="spot.php?id=<?= (int)$n['id'] ?>"><?= h(nm($n['name'])) ?></a></strong> <span class="muted"><?= dot($n['grp_code'] ?? $n['grp']) ?><?= h($n['grp']) ?>・<?= (int)$n['meters'] ?>m</span>
            <a class="small" href="?near=<?= (int)$n['id'] ?>">周辺</a>
            <a class="small" target="_blank" rel="noopener" href="https://www.google.com/maps?q=<?= h($n['lat']) ?>,<?= h($n['lon']) ?>">地図</a></li>
        <?php endforeach; ?>
      </ul>
    <?php endif; ?>
  </section>
<?php endif; ?>

<?php if ($searched): ?>
  <?php if (!$rows && !$error): ?><p class="card">該当する場所がありません。条件をゆるめて試してください。</p><?php endif; ?>
  <?php if ($rows): ?>
    <section class="card places-wrap">
      <h2>検索結果 <?= count($rows) ?>件<?= count($rows) === $per ? '（このページ）' : '' ?></h2>
      <?php overview_map(array_map(fn($s) => ['lat' => $s['lat'], 'lon' => $s['lon'], 'name' => nm($s['name']), 'grp' => $s['grp_code'] ?? '',
            'sub' => $s['category'] . '・' . (AREA_LABEL[$s['area_code']] ?? '')], $rows), 'ovmap'); ?>
      <p class="small muted">ピン、または一覧の「全体地図で表示」を押すと、その地点に寄ります。各候補の右の地図は周辺地図です（押すとGoogleマップで開きます）。川・線路など長いものは、同じ区の地点を「ほか○か所」として1件にまとめています（詳細ページで流域の地点を確認できます）。</p>
      <ol class="places">
        <?php foreach ($rows as $i => $s): ?>
          <li id="ovmap-item-<?= $i + 1 ?>" class="place-item">
            <div class="place-body">
            <div><span class="num" style="background:<?= h(group_color((string)($s['grp_code'] ?? ''))) ?>"><?= $i + 1 ?></span><strong><a class="plain" href="spot.php?id=<?= (int)$s['id'] ?>"><?= h(nm($s['name'])) ?></a></strong>
              <span class="muted"><?= dot($s['grp_code'] ?? '') ?><?= h($s['category']) ?>・<?= h($s['address'] ?: (AREA_LABEL[$s['area_code']] ?? '')) ?></span>
              <?php if (($s['data_status'] ?? '') === 'needs_review'): ?><span class="chip warn">データ要確認</span><?php endif; ?>
              <?php if (!empty($s['risk'])): ?><span class="chip warn">変化リスク</span><?php endif; ?>
              <?php if ($s['confidence'] !== 'field_checked'): ?><span class="chip dim"><?= h(conf_stage($s)) ?></span><?php endif; ?><?= photo_state_chip($s) ?>
              <?php if (!empty($s['ffc_registered'])): ?><span class="chip">FFC登録</span><?php endif; ?>
              <?php if (!empty($s['others'])): ?><span class="chip">ほか<?= (int)$s['others'] ?>か所</span><?php endif; ?>
            </div>
            <?php if (!empty($s['tags'])): ?><div class="small"><?php foreach ($s['tags'] as $t): ?><span class="chip"><?= h($t) ?></span><?php endforeach; ?></div><?php endif; ?>
            <?php if (!empty($s['rule_tags'])): ?><div class="small"><?php foreach ($s['rule_tags'] as $t): ?><span class="chip dim"><?= h($t) ?>（推定）</span><?php endforeach; ?></div><?php endif; ?>
            <?php if (!empty($s['spatial']) && ($s['spatial']['status'] ?? '') === 'ok'): ?><div class="small muted">周辺の特徴（推定）：<?= h(spatial_line($s['spatial'])) ?></div><?php endif; ?>
            <?php if (!empty($s['photo']['url'])): ?>
              <figure class="cardphoto"><a href="spot.php?id=<?= (int)$s['id'] ?>"><img src="<?= h($s['photo']['url']) ?>" alt="<?= h(nm($s['name'])) ?>" loading="lazy" referrerpolicy="no-referrer"></a>
                <figcaption class="small muted"><?= photo_credit_html($s['photo']) ?></figcaption></figure>
            <?php endif; ?>
            <?= summary_html($s, 'div', 'small muted') ?>
            <div class="small actions">
              <a href="#ovmap" class="showmap" data-n="<?= $i + 1 ?>">全体地図で表示</a>
              <a href="spot.php?id=<?= (int)$s['id'] ?>">詳細</a>
              <a href="?near=<?= (int)$s['id'] ?>">周辺の景観</a>
              <a href="flow.php?<?= h(http_build_query(['start' => $s['id'], 'start_name' => nm($s['name'])])) ?>">ここから流れを探す</a>
              <a href="#" class="addlist" data-id="<?= (int)$s['id'] ?>">＋ロケハンリスト</a>
            </div>
            </div>
            <?= minimap_html($s) ?>
          </li>
        <?php endforeach; ?>
      </ol>
      <nav class="pager">
        <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
        <?php if (count($rows) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
      </nav>
    </section>
  <?php endif; ?>
<?php endif; ?>

<script>
document.querySelectorAll('a.showmap').forEach(a => a.addEventListener('click', e => {
  e.preventDefault();
  window.ovFocus && window.ovFocus('ovmap', Number(a.dataset.n));
  document.getElementById('ovmap').scrollIntoView({ behavior: 'smooth', block: 'center' });
}));
</script>
<?php overview_map_script(); page_footer();
