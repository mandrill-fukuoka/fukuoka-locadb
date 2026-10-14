<?php
// 地点の詳細：位置・住所・周辺・撮影の目安（日の出・日の入り、相談先の目安）・出典。URLで共有できる
require __DIR__ . '/bootstrap.php';

$id = (int)($_GET['id'] ?? 0);
$msg = ''; $error = '';

// ログインした人の操作（要確認の解消・対象外・戻す・位置の固定）
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    deny_unless_logged_in();
    $action = (string)($_POST['action'] ?? '');
    if (in_array($action, ['resolve', 'archive', 'restore', 'lock', 'unlock'], true)) {
        try {
            sb_rpc('set_spot_state', ['p_id' => $id, 'p_action' => $action, 'p_user' => current_user(),
                                      'p_note' => trim((string)($_POST['note'] ?? '')) ?: null]);
            $msg = ['resolve' => '要確認を解消しました', 'archive' => '対象外にしました', 'restore' => '掲載に戻しました',
                    'lock' => '位置・名前を固定しました（自動では変わりません）', 'unlock' => '固定を外しました'][$action];
        } catch (Throwable $e) { $error = $e->getMessage(); }
    } elseif ($action === 'practicals') {
        // 撮影実務（撮影実績は1行に1件）
        $req = (string)($_POST['permit_required'] ?? '');
        $past = array_values(array_filter(array_map('trim', preg_split('/\R/u', (string)($_POST['past_productions'] ?? '')))));
        try {
            sb_rpc('save_practicals', [
                'p_id' => $id, 'p_permit_required' => array_key_exists($req, PERMIT_REQUIRED_LABEL) ? $req : null,
                'p_permit_contact' => trim((string)($_POST['permit_contact'] ?? '')) ?: null,
                'p_restrictions' => trim((string)($_POST['restrictions'] ?? '')) ?: null,
                'p_past_productions' => $past, 'p_notes' => trim((string)($_POST['notes'] ?? '')) ?: null,
                'p_user' => current_user(),
            ]);
            $msg = '撮影実務を保存しました';
        } catch (Throwable $e) { $error = $e->getMessage(); }
    }
}
$msg = $msg ?: (['created' => '地点を追加しました（手入力の地点として、自動収集では変わりません）',
                 'edited' => '保存しました'][$_GET['done'] ?? ''] ?? '');

$s = null; $nearby = [];
try {
    $s = $id ? sb_rpc('spot_detail', ['p_id' => $id, 'p_with_history' => (bool)current_user()]) : null;
    if ($s) $nearby = sb_rpc('spots_nearby', ['p_id' => $id, 'p_radius' => 600]) ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); }

if (!$s || (($s['data_status'] ?? '') === 'archived' && !current_user())) {
    http_response_code(404);
    page_header('地点が見つかりません');
    echo '<section class="card"><h1>地点が見つかりません</h1><p class="muted">対象外になったか、URLが正しくありません。<a href="index.php">場所を探す</a></p></section>';
    page_footer(); exit;
}

// 撮影の相談先の目安（種別ごと）。正式な窓口・手続きは必ず各管理者に確認する
const PERMIT_HINTS = [
    'road'     => ['道路上での撮影', '道路使用許可は所轄の警察署。歩道・車道に機材を置く、人を止める場合に必要になることがあります。'],
    'shop'     => ['商店街・店舗の前', '商店街の振興組合や、店舗・建物の管理者への事前相談が必要です。'],
    'park'     => ['公園', '公園の管理者（福岡市の公園管理の窓口、または指定管理者）への申請が必要な場合があります。'],
    'river'    => ['川・河川敷', '河川の管理者（国・福岡県・福岡市のいずれか。川によって異なります）への確認が必要です。護岸・河川敷への立入にも注意してください。'],
    'sea'      => ['港・海岸・砂浜', '港湾・海岸・漁港の管理者（博多港は福岡市港湾空港局など）や漁協への確認が必要です。岸壁・ふ頭は立入制限があります。'],
    'facility' => ['学校・病院・公共施設', '敷地外からの外観撮影が前提です。敷地内・建物内は施設管理者の許可が必要です。病院・学校周辺では利用者への配慮が必要です。'],
    'temple'   => ['寺社・歴史的建造物', '寺社・所有者への事前相談が必要です。文化財は撮影条件が決まっている場合があります。'],
    'rail'     => ['駅・線路', '駅構内・線路敷地は鉄道事業者の許可が必要です（線路内への立入は禁止）。周辺の道路からの撮影は道路の扱いになります。'],
    'private'  => ['建物・施設・土地', '所有者・管理会社への事前相談が必要です。私有地への立入は許可を得てからにしてください。'],
];
const PERMIT_BY_CATEGORY = [
    'alley' => ['road'], 'shotengai' => ['shop', 'road'], 'public_space' => ['road', 'private'], 'bridge_overpass' => ['road'],
    'park' => ['park'], 'hill' => ['park'], 'pond' => ['park'], 'river' => ['river', 'road'],
    'coast' => ['sea'], 'beach' => ['sea'], 'port' => ['sea'], 'fishing_port' => ['sea'], 'island' => ['sea'],
    'school' => ['facility'], 'hospital' => ['facility'], 'civic' => ['facility'],
    'shrine_temple' => ['temple'], 'heritage' => ['temple'], 'station' => ['rail', 'road'], 'railway' => ['rail', 'road'],
];
$permits = PERMIT_BY_CATEGORY[$s['category']] ?? ['private', 'road'];

$lat = (float)$s['lat']; $lon = (float)$s['lon'];
$embedKey = cfg('google_maps_embed_key');
$streetView = 'https://www.google.com/maps/@?' . http_build_query(['api' => 1, 'map_action' => 'pano', 'viewpoint' => "$lat,$lon"]);
$gmaps = place_link_url($s);
$osmUrl = fn($f) => $f['kind'] === 'element'
    ? 'https://www.openstreetmap.org/' . ['n' => 'node', 'w' => 'way', 'r' => 'relation'][$f['osm_type']] . '/' . $f['osm_id']
    : (!empty($f['way_ids'][0]) ? 'https://www.openstreetmap.org/way/' . $f['way_ids'][0] : '');
$lineName = ($s['shape'] ?? '') === 'line' ? preg_replace('/^（無名）.*/u', '', (string)($s['osm'][0]['name'] ?? '')) : '';

page_header(nm($s['name']), '');
?>
<article class="spotpage">
  <p class="crumb small muted"><a href="index.php?grp=<?= h($s['grp_code']) ?>"><?= dot($s['grp_code']) ?><?= h($s['grp']) ?></a>
    ／ <a href="index.php?cat=<?= h($s['category']) ?>"><?= h($s['category_name']) ?></a>
    ／ <a href="index.php?area=<?= h($s['area_code']) ?>"><?= h(AREA_LABEL[$s['area_code']] ?? '') ?></a>
    <span class="code"><?= h($s['code']) ?></span></p>
  <h1><?= h(nm($s['name'])) ?></h1>
  <p class="lead">
    <?php if ($s['address']): ?><span><?= h($s['address']) ?></span><?php endif; ?>
    <?php foreach ($s['extra_categories'] ?? [] as $x): ?><span class="chip"><?= h($x) ?></span><?php endforeach; ?>
    <?php if ($s['confidence'] !== 'field_checked'): ?><span class="chip dim"><?= h(CONF_LABEL[$s['confidence']] ?? '') ?></span><?php else: ?><span class="chip ok">現地確認済み</span><?php endif; ?>
    <?php if ($s['data_status'] === 'needs_review'): ?><span class="chip warn" title="<?= h($s['review_reason']) ?>">データ要確認</span><?php endif; ?>
    <?php if ($s['data_status'] === 'archived'): ?><span class="chip warn">対象外：<?= h(preg_replace('/^(auto|manual):/', '', (string)$s['archived_reason'])) ?></span><?php endif; ?>
    <?php if (!empty($s['risks'])): ?><span class="chip warn">変化リスク</span><?php endif; ?>
    <?php if ($s['ffc_registered']): ?><span class="chip">FFC登録</span><?php endif; ?>
  </p>
  <?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

  <div class="spot-grid">
    <div class="spot-main">
      <?php if ($embedKey): ?>
        <iframe class="map spotmap" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen
          src="<?= h('https://www.google.com/maps/embed/v1/view?' . http_build_query(['key' => $embedKey, 'center' => "$lat,$lon", 'zoom' => 17, 'maptype' => 'roadmap', 'language' => 'ja', 'region' => 'JP'])) ?>"></iframe>
      <?php else: ?>
        <div class="map spotmap" data-spots='<?= h(json_encode([['lat' => $lat, 'lon' => $lon, 'name' => $s['name']]], JSON_UNESCAPED_UNICODE)) ?>' data-line="0"></div>
      <?php endif; ?>
      <p class="card-foot">
        <a class="gmaps-btn" target="_blank" rel="noopener" href="<?= h($gmaps) ?>">Googleマップで開く</a>
        <a class="gmaps-btn" target="_blank" rel="noopener" href="<?= h($streetView) ?>">ストリートビュー</a>
        <a class="gmaps-btn addlist" href="#" data-id="<?= (int)$s['id'] ?>">＋ロケハンリスト</a>
        <a class="gmaps-btn" href="flow.php?<?= h(http_build_query(['start' => $s['id'], 'start_name' => nm($s['name'])])) ?>">ここから流れを探す</a>
      </p>
      <p class="small muted">位置：<?= h(POSITION_LABEL[$s['position_kind']] ?? '') ?>。<?= h(number_format($lat, 5)) ?>, <?= h(number_format($lon, 5)) ?>
        <?php if ($s['position_kind'] === 'standpoint'): ?>（線上の位置から近くの道に寄せています）<?php endif; ?></p>
      <?php if (($s['terrain_status'] ?? null) === 'ok'): ?>
        <p class="small terrain">地形：標高 <?= h(number_format((float)$s['elevation_m'], 1)) ?>m
          <?php if ($s['relief_m'] !== null): $r = (float)$s['relief_m']; ?>・周り（半径150m）より<?= h(($r >= 0 ? '＋' : '−') . number_format(abs($r), 1)) ?>m<?php endif; ?>
          <?php if ($s['slope_pct'] !== null): ?>・最大の傾き <?= h(number_format((float)$s['slope_pct'], 0)) ?>%<?php endif; ?>
          <span class="muted">（建物を除いた地面の高さ。<?= h(TERRAIN_SRC_LABEL[$s['terrain_src']] ?? '') ?>）</span></p>
      <?php elseif (($s['terrain_status'] ?? null) === 'no_data'): ?>
        <p class="small muted terrain">地形：標高データなし（海上など）</p>
      <?php endif; ?>

      <?= summary_html($s, 'p', 'summary') ?>
      <?php if (!empty($s['tags'])): ?><p><?php foreach ($s['tags'] as $t): ?><span class="chip"><?= h($t['name']) ?></span><?php endforeach; ?>
        <span class="small muted"><?= ($s['tags'][0]['origin'] ?? '') === 'human' ? '（人が確認）' : '（AIの推定）' ?></span></p><?php endif; ?>

      <section class="block">
        <h2>光の目安</h2>
        <div class="sunbox" data-lat="<?= h($lat) ?>" data-lon="<?= h($lon) ?>">
          <label class="inline-label">日付 <input type="date" class="sun-date"></label>
          <dl class="sun"></dl>
          <p class="small muted">太陽の方角は真北から時計回り。建物や地形による日陰は考慮していません。</p>
        </div>
      </section>

      <section class="block">
        <h2>撮影の相談先の目安</h2>
        <ul class="permits">
          <?php foreach ($permits as $k): [$t, $d] = PERMIT_HINTS[$k]; ?>
            <li><strong><?= h($t) ?></strong><span><?= h($d) ?></span></li>
          <?php endforeach; ?>
          <li><strong>まとめて相談</strong><span>福岡市内のロケは、福岡フィルムコミッションが撮影の相談・調整の窓口になれる場合があります。<?php if (cfg('ffc_url')): ?><a href="<?= h(cfg('ffc_url')) ?>" target="_blank" rel="noopener">サイト</a><?php endif; ?></span></li>
        </ul>
        <?php $p = $s['practical'] ?? null; $past = is_array($p['past_productions'] ?? null) ? $p['past_productions'] : []; ?>
        <?php if ($p && ($p['permit_required'] || $p['permit_contact'] || $p['restrictions'] || $p['notes'] || $past)): ?>
          <h3 class="subhead">この地点の撮影実務</h3>
          <dl class="practical">
            <?php if (!empty($p['permit_required'])): ?><dt>許可</dt><dd><?= h(PERMIT_REQUIRED_LABEL[$p['permit_required']] ?? $p['permit_required']) ?></dd><?php endif; ?>
            <?php foreach (['permit_contact' => '窓口', 'restrictions' => '制約・注意', 'notes' => 'メモ'] as $k => $v): if (!empty($p[$k])): ?>
              <dt><?= h($v) ?></dt><dd><?= nl2br(h($p[$k])) ?></dd><?php endif; endforeach; ?>
            <?php if ($past): ?><dt>撮影実績</dt><dd><?= implode('<br>', array_map('h', $past)) ?></dd><?php endif; ?>
          </dl>
          <?php if (!empty($p['updated_at'])): ?><p class="small muted">更新 <?= h(substr((string)$p['updated_at'], 0, 10)) ?><?= current_user() && !empty($p['updated_by']) ? '（' . h($p['updated_by']) . '）' : '' ?></p><?php endif; ?>
        <?php endif; ?>
        <p class="small muted">一般的な目安です。撮影の可否・手続きは各管理者に必ず確認してください。</p>
        <?php if (current_user()): ?>
          <details class="practical-edit"<?= ($_POST['action'] ?? '') === 'practicals' && $error ? ' open' : '' ?>>
            <summary class="small">撮影実務を<?= $p ? '直す' : '入力する' ?></summary>
            <form method="post"><?= csrf_field() ?><input type="hidden" name="action" value="practicals">
              <label>許可<select name="permit_required"><option value="">—</option>
                <?php foreach (PERMIT_REQUIRED_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= ($p['permit_required'] ?? '') === $k ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach; ?></select></label>
              <label>窓口<input name="permit_contact" value="<?= h($p['permit_contact'] ?? '') ?>" placeholder="例：寺務所（電話で事前相談）"></label>
              <label>制約・注意<textarea name="restrictions" rows="2" placeholder="例：法要の日は不可。境内は三脚禁止"><?= h($p['restrictions'] ?? '') ?></textarea></label>
              <label>撮影実績（1行に1件）<textarea name="past_productions" rows="2" placeholder="例：映画「〇〇」（2024）"><?= h(implode("\n", $past)) ?></textarea></label>
              <label>メモ<textarea name="notes" rows="2"><?= h($p['notes'] ?? '') ?></textarea></label>
              <button class="secondary small-btn">撮影実務を保存</button>
            </form>
          </details>
        <?php endif; ?>
      </section>
    </div>

    <aside class="spot-side">
      <section class="block">
        <h2>周辺の景観（600m）</h2>
        <?php if (!$nearby): ?><p class="muted small">周辺に登録地点がありません。</p><?php else: ?>
        <ul class="nearby compact">
          <?php foreach (array_slice($nearby, 0, 14) as $n): ?>
            <li><?= dot($n['grp_code'] ?? $n['grp']) ?><a class="plain" href="spot.php?id=<?= (int)$n['id'] ?>"><?= h(nm($n['name'])) ?></a>
              <span class="muted small"><?= (int)$n['meters'] ?>m</span></li>
          <?php endforeach; ?>
        </ul>
        <p class="small"><a href="index.php?near=<?= (int)$s['id'] ?>">周辺を地図で見る</a></p>
        <?php endif; ?>
      </section>

      <?php if ($lineName && (int)$s['line_count'] > 1): ?>
      <section class="block">
        <h2><?= h($lineName) ?></h2>
        <p class="small">この<?= h($s['category_name']) ?>には <?= (int)$s['line_count'] ?> か所の地点があります。
          <a href="index.php?<?= h(http_build_query(['q' => $lineName, 'cat' => $s['category']])) ?>">一覧</a></p>
      </section>
      <?php endif; ?>

      <?php if (!empty($s['flows'])): ?>
      <section class="block">
        <h2>この地点を含む流れ</h2>
        <ul class="compact"><?php foreach ($s['flows'] as $f): ?><li class="small"><?= h($f['label']) ?>（<?= h(MODE_LABEL[$f['mode']] ?? '') ?>）</li><?php endforeach; ?></ul>
        <p class="small"><a href="flows.php?f=saved">保存した流れ</a></p>
      </section>
      <?php endif; ?>

      <?php if (!empty($s['risks'])): ?>
      <section class="block">
        <h2>変化リスク</h2>
        <?php foreach ($s['risks'] as $r): ?>
          <p class="small"><span class="chip warn"><?= h(RISK_LABEL[$r['type']] ?? $r['type']) ?><?= $r['verified'] ? '' : '（要確認）' ?></span>
            <?php if (preg_match('#^https?://#', (string)$r['url'])): ?><a href="<?= h($r['url']) ?>" target="_blank" rel="noopener">出典</a><?php endif; ?></p>
        <?php endforeach; ?>
      </section>
      <?php endif; ?>

      <section class="block">
        <h2>出典</h2>
        <ul class="compact small">
          <?php foreach ($s['osm'] as $f): $u = $osmUrl($f); ?>
            <li>OpenStreetMap <?php if ($u): ?><a href="<?= h($u) ?>" target="_blank" rel="noopener"><?= h($f['kind'] === 'element' ? $f['osm_type'] . $f['osm_id'] : '線のデータ') ?></a><?php endif; ?>
              <?= $f['role'] === 'secondary' ? '<span class="muted">（同じ場所の別データ）</span>' : '' ?></li>
          <?php endforeach; ?>
          <?php if (($s['origin'] ?? '') === 'manual'): ?>
            <li>手入力（<?= h(substr((string)$s['created_at'], 0, 10)) ?><?= current_user() && !empty($s['created_by']) ? '・' . h($s['created_by']) : '' ?>）</li>
          <?php endif; ?>
          <?php foreach ($s['sources'] ?? [] as $src): if ($src['kind'] === 'review') continue; ?>
            <li><?= h($src['title'] ?: (SOURCE_KIND_LABEL[$src['kind']] ?? $src['kind'])) ?><?php if (!empty($src['note'])): ?><span class="muted">：<?= h($src['note']) ?></span><?php endif; ?><?php if (preg_match('#^https?://#', (string)$src['url'])): ?> <a href="<?= h($src['url']) ?>" target="_blank" rel="noopener">↗</a><?php endif; ?></li>
          <?php endforeach; ?>
          <?php if ($s['address']): ?><li>住所：国土地理院 逆ジオコーダー</li><?php endif; ?>
          <?php if (($s['terrain_status'] ?? null) === 'ok'): ?><li>地形：<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">国土地理院 標高タイル</a></li><?php endif; ?>
        </ul>
        <p class="small muted">更新 <?= h(substr((string)$s['updated_at'], 0, 10)) ?></p>
      </section>

      <?php if (current_user()): ?>
      <section class="block admin">
        <h2>管理</h2>
        <p class="small">状態：<?= h(STATUS_LABEL[$s['status']] ?? $s['status']) ?>／<?= h(DATA_STATUS_LABEL[$s['data_status']] ?? '') ?><?= $s['locked'] ? '／位置固定' : '' ?>
          <?php if ($s['review_reason']): ?><br><span class="error">要確認の理由：<?= h($s['review_reason']) ?></span><?php endif; ?></p>
        <form method="post" class="admin-actions"><?= csrf_field() ?>
          <input name="note" placeholder="メモ（任意）">
          <?php if ($s['data_status'] === 'needs_review'): ?><button name="action" value="resolve" class="secondary small-btn">要確認を解消</button><?php endif; ?>
          <?php if ($s['data_status'] !== 'archived'): ?><button name="action" value="archive" class="secondary small-btn">対象外にする</button>
          <?php else: ?><button name="action" value="restore" class="secondary small-btn">掲載に戻す</button><?php endif; ?>
          <button name="action" value="<?= $s['locked'] ? 'unlock' : 'lock' ?>" class="secondary small-btn"><?= $s['locked'] ? '固定を外す' : '位置・名前を固定' ?></button>
        </form>
        <p class="small"><a href="edit.php?id=<?= (int)$s['id'] ?>">位置・名前・種別を直す</a>
          ・<a href="spots.php?<?= h(http_build_query(['q' => $s['name'], 'queue' => 'all'])) ?>">確認画面で編集</a></p>
        <?php if (!empty($s['history'])): ?>
          <details class="history"><summary class="small">変更履歴（<?= count($s['history']) ?>）</summary>
            <ol class="compact small">
              <?php foreach ($s['history'] as $hst): ?>
                <li><?= h(substr((string)$hst['at'], 0, 16)) ?> <?= h(HISTORY_LABEL[$hst['event']] ?? $hst['event']) ?>
                  <span class="muted"><?= h($hst['actor'] === 'pipeline' ? '自動' : $hst['actor']) ?></span>
                  <?php $d = $hst['detail'] ?? []; if (!empty($d['reason'])): ?><span class="muted">：<?= h($d['reason']) ?></span><?php endif; ?>
                  <?php if (!empty($d['m'])): ?><span class="muted">（<?= (int)$d['m'] ?>m）</span><?php endif; ?>
                  <?php if (!empty($d['to']) && is_string($d['to'])): ?><span class="muted">→ <?= h($d['to']) ?></span><?php endif; ?>
                  <?php if (!empty($d['note']) && is_string($d['note'])): ?><span class="muted">「<?= h($d['note']) ?>」</span><?php endif; ?></li>
              <?php endforeach; ?>
            </ol></details>
        <?php endif; ?>
      </section>
      <?php endif; ?>
    </aside>
  </div>
</article>
<script src="assets/sun.js"></script>
<?php leaflet_script(false); page_footer();
