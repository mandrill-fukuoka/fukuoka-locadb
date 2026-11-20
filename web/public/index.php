<?php
// 場所を探す（トップページ）：名前・種別・景観・雰囲気でピンポイント検索。周辺の景観表示、ここから流れを探す
require __DIR__ . '/bootstrap.php';
// 閲覧専用：ログインなしで利用できる

$q     = trim($_GET['q'] ?? '');
$area  = $_GET['area'] ?? '';
$cat   = $_GET['cat'] ?? '';
$grp   = $_GET['grp'] ?? '';
$tagList = array_slice(array_values(array_unique(array_filter(array_map('strval', (array)($_GET['tag'] ?? [])), fn($v) => $v !== ''))), 0, 6);   // 雰囲気：複数指定（?tag=x も ?tag[]=x も可）
$terr  = ($_GET['terrain'] ?? '') === 'high' ? 'high' : '';      // 高台：周りより+10m以上（タスクF）
// 周辺の特徴（推定）：空間特徴（タスクI）。段階の境界は sql/api/10_spots.sql
$spOpen  = in_array($_GET['sp_open'] ?? '', ['open', 'closed'], true) ? $_GET['sp_open'] : '';
$spWater = in_array($_GET['sp_water'] ?? '', ['near', 'within150'], true) ? $_GET['sp_water'] : '';
$spBuilt = in_array($_GET['sp_built'] ?? '', ['few', 'many'], true) ? $_GET['sp_built'] : '';
$spRoad  = in_array($_GET['sp_road'] ?? '', ['narrow', 'wide'], true) ? $_GET['sp_road'] : '';
// 計算済みの距離（順3）：騒音・トイレ・駐車場・ドローンの目安。境界は sql/api/10_spots.sql の search_spots
$spQuiet   = ($_GET['sp_quiet'] ?? '') === 'quiet' ? 'quiet' : '';
$spToilet  = in_array((int)($_GET['sp_toilet'] ?? 0), [100, 300, 500], true) ? (int)$_GET['sp_toilet'] : 0;
$spParking = in_array((int)($_GET['sp_parking'] ?? 0), [100, 300, 500], true) ? (int)$_GET['sp_parking'] : 0;
$spDrone   = ($_GET['sp_drone'] ?? '') === 'easy' ? 'easy' : '';
$photoSt = photo_state_filter();
// 並び順（search_spots の p_sort）。指定なし＝おすすめ順
$sortLabels = ['relevance' => 'おすすめ順', 'name' => '名前順', 'address' => '住所順', 'created' => '登録が新しい順',
               'updated' => '更新が新しい順', 'relief' => '周りより高い順', 'facts' => '事実の多い順',
               'importance' => '重要度順', 'hidden' => '穴場順'];
$sort  = $_GET['sort'] ?? '';
if (!isset($sortLabels[$sort]) || $sort === 'relevance') $sort = '';
$near  = (int)($_GET['near'] ?? 0);
$page  = max(0, (int)($_GET['p'] ?? 0));
$per   = 40;
$error = ''; $rows = []; $nearby = []; $nearSpot = null; $interp = null; $flowHint = false;

try {
    $cats   = sb_get('spot_categories?select=code,name,landscape_group&order=landscape_group,code') ?? [];
    $groups = sb_get('landscape_groups?select=code,name') ?? [];
    $tags   = sb_get('tags?select=code,name,facet&order=facet,code') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $cats = $groups = $tags = []; }
$groupNames = array_column($groups, 'name', 'code');

$searched = $q !== '' || $area !== '' || $cat !== '' || $grp !== '' || $tagList || $terr !== '' || $spOpen !== '' || $spWater !== '' || $spRoad !== '' || $spBuilt !== '' || $spQuiet !== '' || $spToilet || $spParking || $spDrone !== '' || $photoSt !== '';
try {
    if ($near) {
        $nearby = sb_rpc('spots_nearby', ['p_id' => $near, 'p_radius' => 800]) ?? [];
        $ns = sb_get('spots?select=id,name,code&id=eq.' . $near) ?? [];
        $nearSpot = $ns[0] ?? null;
    }
    if ($searched) {
        $base = [
            'p_q' => $q ?: null, 'p_area' => $area ?: null, 'p_category' => $cat ?: null,
            'p_group' => $grp ?: null, 'p_tags' => $tagList ?: null, 'p_tags_all' => count($tagList) > 1,
            'p_offset' => $page * $per, 'p_limit' => $per, 'p_relief_min' => $terr === 'high' ? HIGH_GROUND_M : null,
            'p_open' => $spOpen ?: null, 'p_water' => $spWater ?: null, 'p_road' => $spRoad ?: null, 'p_built' => $spBuilt ?: null, 'p_photo' => $photoSt ?: null,
            'p_quiet' => $spQuiet ?: null, 'p_toilet_m' => $spToilet ?: null, 'p_parking_m' => $spParking ?: null, 'p_drone' => $spDrone ?: null,
            'p_sort' => $sort ?: null];
        $rows = sb_rpc('search_spots', $base) ?? [];
        // 入力欄の文を辞書で条件に読み替える（順3）。名前・住所で1件も見つからず、ほかに景観・種類・雰囲気・エリアの指定がないときだけ
        if ($q !== '' && $area === '' && $cat === '' && $grp === '' && !$tagList) {
            $cs = scene_to_conditions($q);
            $flowHint = $cs['flow'];
            if (!$rows && $cs['any']) {
                $okTags = array_column($tags, 'code');
                $iGroup = ($cs['group'] && isset($groupNames[$cs['group']])) ? $cs['group'] : null;
                $iTags  = array_values(array_intersect($cs['tags'], $okTags));
                $iCodes = $iGroup ? ($cs['kinds'][$iGroup] ?? []) : [];
                $iCat   = count($iCodes) === 1 ? $iCodes[0] : null;                       // 種類が1つに決まるときだけ種類で絞る
                $try = fn(?array $t, ?string $c) => sb_rpc('search_spots', array_merge($base, ['p_q' => null, 'p_area' => $cs['area'],
                    'p_group' => $iGroup, 'p_category' => $c, 'p_tags' => $t ?: null, 'p_tags_all' => false])) ?? [];
                $rows = $try($iTags, $iCat);
                if (!$rows && $iTags) { $rows = $try(null, $iCat); $iTags = []; }     // 雰囲気が付いた地点が少ないので、なければ雰囲気を外す
                if (!$rows && $iCat) { $rows = $try(null, null); $iCat = null; $iTags = []; }
                $interp = ['group' => $iGroup, 'tags' => $iTags, 'area' => $cs['area'], 'cat' => $iCat, 'unparsed' => $cs['unparsed'], 'flow' => $cs['flow']];
            }
        }
        if ($page === 0) {   // 利用の記録：何で探して何件だったか（0件の検索が辞書を育てる材料）
            $cond = array_filter(['cat' => $cat, 'grp' => $grp, 'tag' => implode(',', $tagList), 'terrain' => $terr, 'sp_open' => $spOpen,
                'sp_water' => $spWater, 'sp_road' => $spRoad, 'sp_built' => $spBuilt, 'sp_quiet' => $spQuiet, 'sp_toilet' => $spToilet ?: '',
                'sp_parking' => $spParking ?: '', 'sp_drone' => $spDrone, 'photo' => $photoSt, 'sort' => $sort], fn($v) => $v !== '');
            if ($interp) {   // 語を条件に読み替えた検索：読めた条件と、読めなかった語（辞書を育てる材料）
                $cond['interp'] = array_filter(['grp' => $interp['group'], 'tags' => $interp['tags'], 'area' => $interp['area'], 'cat' => $interp['cat'],
                    'unparsed' => $interp['unparsed']], fn($v) => $v !== null && $v !== []);
            } elseif ($q !== '' && !empty($cs) && !$cs['any'] && !$rows) {
                $cond['interp'] = ['none' => true];   // 名前にも辞書にも当たらなかった語
            }
            if ($flowHint) $cond['flow_hint'] = true;
            usage_log('spots', ['scene' => $q !== '' ? mb_substr($q, 0, 100) : null, 'tags' => (object)$cond, 'area_code' => $area ?: null,
                'result_count' => $rows ? (int)($rows[0]['total'] ?? count($rows)) : 0]);
        }
    }
} catch (Throwable $e) { $error = $e->getMessage(); }

$embedKey = cfg('google_maps_embed_key');
$qs = fn(array $o) => '?' . http_build_query(array_merge(['q' => $q, 'area' => $area, 'cat' => $cat, 'grp' => $grp, 'tag' => $tagList, 'terrain' => $terr,
    'sp_open' => $spOpen, 'sp_water' => $spWater, 'sp_road' => $spRoad, 'sp_built' => $spBuilt, 'sp_quiet' => $spQuiet, 'sp_toilet' => $spToilet ?: '',
    'sp_parking' => $spParking ?: '', 'sp_drone' => $spDrone, 'photo' => $photoSt, 'sort' => $sort], $o));
// 全件数（まとめたあとの件数）。ページ送りの終わりもこれで決める
$total = $rows ? (int)($rows[0]['total'] ?? count($rows)) : 0;
$pages = max(1, (int)ceil($total / $per));


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

    <label>地形<select name="terrain"><option value="">指定なし</option>
      <option value="high" <?= $terr === 'high' ? 'selected' : '' ?>>高台（周りより<?= HIGH_GROUND_M ?>m以上高い）</option></select></label>
  </div>
  <details class="tagpick"<?= $tagList ? ' open' : '' ?>>
    <summary>雰囲気<?= $tagList ? '（' . count($tagList) . '件を指定）' : '（複数選べます）' ?></summary>
    <p class="small muted">複数選ぶと、すべてに当てはまる地点だけに絞ります。</p>
    <p><?php foreach ($tags as $t): ?><label class="chip"><input type="checkbox" name="tag[]" value="<?= h($t['code']) ?>" <?= in_array($t['code'], $tagList, true) ? 'checked' : '' ?>> <?= h($t['name']) ?></label> <?php endforeach; ?></p>
  </details>
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
  <div class="row">
    <label>騒音（推定）<select name="sp_quiet"><option value="">指定なし</option>
      <option value="quiet" <?= $spQuiet === 'quiet' ? 'selected' : '' ?>>静かめ（線路・幹線道路が150m以上、滑走路が2.5km以上）</option></select></label>
    <label>公衆トイレ<select name="sp_toilet"><option value="">指定なし</option>
      <?php foreach ([100, 300, 500] as $m): ?><option value="<?= $m ?>" <?= $spToilet === $m ? 'selected' : '' ?>><?= $m ?>m以内</option><?php endforeach; ?></select></label>
    <label>駐車場<select name="sp_parking"><option value="">指定なし</option>
      <?php foreach ([100, 300, 500] as $m): ?><option value="<?= $m ?>" <?= $spParking === $m ? 'selected' : '' ?>><?= $m ?>m以内</option><?php endforeach; ?></select></label>
    <label>ドローン（目安）<select name="sp_drone"><option value="">指定なし</option>
      <option value="easy" <?= $spDrone === 'easy' ? 'selected' : '' ?>>人口集中地区の外で、空港から6km以上</option></select></label>
  </div>
  <?php if (current_user()): ?><div class="row">
    <label>写真の状態（ログイン時のみ）<select name="photo"><option value="">指定なし</option>
      <?php foreach (PHOTO_STATE_LABEL as $k => $v): ?><option value="<?= h($k) ?>" <?= $photoSt === $k ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach; ?></select></label>
  </div><?php endif; ?>
  <input type="hidden" name="sort" value="<?= h($sort) ?>">
  <button>検索</button>
  <p class="small muted">雰囲気での絞り込みは、雰囲気のタグが付いた地点だけが対象です（「推定」のタグは、種別や位置から機械的に付けたものです）。「推定」の条件は、OpenStreetMapの建物・道・水辺から計算した目安で、周りに建物のデータが少ない地点（未判定）は対象外です。騒音・トイレ・駐車場・ドローンは、OpenStreetMapなどから計算した距離の目安で、現地の状況や飛行の可否を保証するものではありません（ドローンは必ず国土交通省の最新の区域・許可を確認してください）。計算がまだの地点は対象外です。</p>
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

<?php if ($interp): ?>
  <section class="card interp">
    <p>「<strong><?= h($q) ?></strong>」に当てはまる名前・住所がなかったので、語を条件に読み替えて探しました：
      <?php $parts = [];
        if ($interp['group']) $parts[] = '景観＝' . h($groupNames[$interp['group']] ?? $interp['group']);
        if ($interp['cat']) { $cn = array_column($cats, 'name', 'code'); $parts[] = '種別＝' . h($cn[$interp['cat']] ?? $interp['cat']); }
        if ($interp['tags']) { $tn = array_column($tags, 'name', 'code'); $parts[] = '雰囲気＝' . h(implode('・', array_map(fn($c) => $tn[$c] ?? $c, $interp['tags']))) . '（どれか）'; }
        if ($interp['area']) $parts[] = 'エリア＝' . h(AREA_LABEL[$interp['area']] ?? $interp['area']);
        echo implode('／', $parts); ?></p>
    <?php if ($interp['unparsed']): ?><p class="small muted">読み取れなかった語：<?= h(implode('、', $interp['unparsed'])) ?>（条件には使っていません）</p><?php endif; ?>
    <p class="small muted">読み替えは辞書による機械的なものです。違うときは、上の条件欄で指定してください。</p>
  </section>
<?php endif; ?>
<?php if ($flowHint): ?>
  <section class="card interp">
    <form method="post" action="flow.php"><?= csrf_field() ?><input type="hidden" name="action" value="search"><input type="hidden" name="scene" value="<?= h($q) ?>">
      <p>「<?= h(mb_strimwidth($q, 0, 60, '…')) ?>」は、景観が移り変わる流れの指定に見えます。<button>流れで探す</button></p></form>
  </section>
<?php endif; ?>

<?php if ($near && $nearSpot): ?>
  <section class="card">
    <h2>「<?= h(nm($nearSpot['name'])) ?>」の周辺（約800m以内）</h2>
    <p><a class="gmaps-btn" href="flow.php?<?= h(http_build_query(['start' => $near, 'start_name' => nm($nearSpot['name'])])) ?>">ここを起点に流れを探す</a></p>
    <?php if (!$nearby): ?><p class="muted">周辺に登録地点がありません。</p><?php else: ?>
      <?php overview_map(array_map(fn($n) => ['lat' => $n['lat'], 'lon' => $n['lon'], 'name' => nm($n['name']), 'grp' => $n['grp_code'] ?? '',
            'sub' => $n['grp'] . '・' . (int)$n['meters'] . 'm'], $nearby), 'nearmap', 'map'); ?>
      <ul class="nearby">
        <?php foreach ($nearby as $k => $n): ?>
          <li id="nearmap-item-<?= $k + 1 ?>"><span class="num"><?= $k + 1 ?></span><strong><a class="plain" href="spot.php?id=<?= (int)$n['id'] ?>"><?= h(sn($n)) ?></a></strong> <span class="muted"><?= dot($n['grp_code'] ?? $n['grp']) ?><?= h($n['grp']) ?>・<?= (int)$n['meters'] ?>m</span>
            <a class="small" href="?near=<?= (int)$n['id'] ?>">周辺</a>
            <a class="small" target="_blank" rel="noopener" href="https://www.google.com/maps?q=<?= h($n['lat']) ?>,<?= h($n['lon']) ?>">地図</a></li>
        <?php endforeach; ?>
      </ul>
    <?php endif; ?>
  </section>
<?php endif; ?>

<?php if ($searched): ?>
  <?php if (!$rows && !$error): ?><p class="card">該当する場所がありません。条件をゆるめて試してください。<?= $page > 0 ? '（<a href="' . h($qs(['p' => 0])) . '">1ページ目へ</a>）' : '' ?></p><?php endif; ?>
  <?php if ($rows): ?>
    <section class="card places-wrap">
      <h2>検索結果 全<?= number_format($total) ?>件<?= $total > $per ? '（' . number_format($page * $per + 1) . '〜' . number_format($page * $per + count($rows)) . '件目を表示）' : '' ?></h2>
      <p class="small sortbar"><label>並び順
        <select onchange="location.href=this.value">
          <?php foreach ($sortLabels as $k => $v): $cur = ($k === 'relevance' ? '' : $k) === $sort; ?>
            <option value="<?= h($qs(['sort' => $k === 'relevance' ? '' : $k, 'p' => 0])) ?>" <?= $cur ? 'selected' : '' ?>><?= h($v) ?></option>
          <?php endforeach; ?></select></label>
        <span class="muted">件数は、川・線路・境内などを「ほか○か所」として1件にまとめたあとの数です。</span></p>
      <?php overview_map(array_map(fn($s) => ['lat' => $s['lat'], 'lon' => $s['lon'], 'name' => sn($s), 'grp' => $s['grp_code'] ?? '',
            'sub' => $s['category'] . '・' . (AREA_LABEL[$s['area_code']] ?? '')], $rows), 'ovmap'); ?>
      <p class="small muted">ピン、または一覧の「全体地図で表示」を押すと、その地点に寄ります。各候補の右の地図は周辺地図です（押すとGoogleマップで開きます）。川・線路など長いものは、同じ区の地点を「ほか○か所」として1件にまとめています（詳細ページで流域の地点を確認できます）。寺社の境内、大きな公園・大学の敷地内の地点も、同じ景観のものは代表の地点に「ほか○か所」としてまとめています（詳細ページで敷地内の地点を確認できます）。</p>
      <ol class="places">
        <?php foreach ($rows as $i => $s): ?>
          <li id="ovmap-item-<?= $i + 1 ?>" class="place-item">
            <div class="place-body">
            <div><span class="num" style="background:<?= h(group_color((string)($s['grp_code'] ?? ''))) ?>"><?= $i + 1 ?></span><strong><a class="plain" href="spot.php?id=<?= (int)$s['id'] ?>"><?= h(sn($s)) ?></a></strong>
              <span class="muted"><?= dot($s['grp_code'] ?? '') ?><?= h($s['category']) ?>・<?= h($s['address'] ?: (AREA_LABEL[$s['area_code']] ?? '')) ?></span>
              <?php if (($s['data_status'] ?? '') === 'needs_review'): ?><span class="chip warn">データ要確認</span><?php endif; ?>
              <?php if (!empty($s['risk'])): ?><span class="chip warn">変化リスク</span><?php endif; ?>
              <?php if ($s['confidence'] !== 'field_checked'): ?><span class="chip dim"><?= h(conf_stage($s)) ?></span><?php endif; ?><?= photo_state_chip($s) ?>
              <?php if (!empty($s['ffc_registered'])): ?><span class="chip">FFC登録</span><?php endif; ?>
              <?php if (!empty($s['others'])): ?><span class="chip"><?= !empty($s['precinct_group']) ? '境内・敷地内 ' : '' ?>ほか<?= (int)$s['others'] ?>か所</span><?php endif; ?>
              <?php if (!empty($s['parent']['id'])): ?><span class="small muted"><a class="plain" href="spot.php?id=<?= (int)$s['parent']['id'] ?>"><?= h(nm($s['parent']['name'])) ?></a>の<?= h($s['parent']['word']) ?></span><?php endif; ?>
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
        <span><?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?></span>
        <span class="muted small"><?= $page + 1 ?> / <?= $pages ?> ページ</span>
        <span><?php if (($page + 1) * $per < $total): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?></span>
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
