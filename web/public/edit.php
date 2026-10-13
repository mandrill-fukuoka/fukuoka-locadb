<?php
// 地点の追加・修正（ログイン必須）
//  - 新規：OSMにない場所を、地図で位置を指定して「手入力の地点」（origin=manual）として追加する。自動収集では動かない・消えない
//  - 修正（?id=）：位置・名前・種別を直す。OSMの地点を直すと「位置固定」になり、以後はOSMが変わったときだけ要確認になる
//  - ?check=1&lat=&lon=&name= は近くの地点を返す（地図でピンを動かしたときに使う。読み取りのみ）
require __DIR__ . '/bootstrap.php';
require_login();

const DEFAULT_CENTER = [33.5903, 130.4017];      // 天神
const PICK_CONF = ['unverified' => '未確認', 'desk_checked' => '資料確認済み', 'field_checked' => '現地確認済み'];

// 近くの地点（JSON）
if (($_GET['check'] ?? '') === '1') {
    header('Content-Type: application/json; charset=utf-8');
    $lat = (float)($_GET['lat'] ?? 0); $lon = (float)($_GET['lon'] ?? 0);
    try {
        echo json_encode(sb_rpc('manual_check', ['p_lat' => $lat, 'p_lon' => $lon,
            'p_name' => trim((string)($_GET['name'] ?? '')) ?: null, 'p_exclude' => (int)($_GET['id'] ?? 0) ?: null]),
            JSON_UNESCAPED_UNICODE);
    } catch (Throwable $e) {
        http_response_code(502); echo json_encode(['error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
    }
    exit;
}

$id = (int)($_GET['id'] ?? $_POST['id'] ?? 0);
$s = null; $error = ''; $similar = [];
$cats = [];
try {
    $cats = sb_get('spot_categories?select=code,name,landscape_group&order=priority,code') ?? [];
    if ($id) $s = sb_rpc('spot_detail', ['p_id' => $id]);
} catch (Throwable $e) { $error = $e->getMessage(); }
if ($id && !$s && !$error) { http_response_code(404); $error = '地点が見つかりません'; }
$catCodes = array_column($cats, 'code');

// 入力値（POSTの値 → 既存の地点 → URLの指定 の順）
$in = fn(string $k, $def = '') => isset($_POST[$k]) ? trim((string)$_POST[$k]) : $def;
$f = [
    'name'        => $in('name', $s ? (strpos((string)$s['name'], '（無名）') === 0 ? '' : (string)$s['name']) : ''),
    'lat'         => $in('lat', $s ? (string)$s['lat'] : (string)($_GET['lat'] ?? '')),
    'lon'         => $in('lon', $s ? (string)$s['lon'] : (string)($_GET['lon'] ?? '')),
    'category'    => $in('category', $s ? (string)$s['category'] : ''),
    'summary'     => $in('summary'),
    'confidence'  => $in('confidence', 'unverified'),
    'source_url'  => $in('source_url'),
    'source_note' => $in('source_note'),
    'note'        => $in('note'),
];

if ($_SERVER['REQUEST_METHOD'] === 'POST' && !$error) {
    check_csrf();
    deny_unless_logged_in();
    $lat = is_numeric($f['lat']) ? (float)$f['lat'] : null;
    $lon = is_numeric($f['lon']) ? (float)$f['lon'] : null;
    try {
        if ($lat === null || $lon === null || $lat < 33.0 || $lat > 34.2 || $lon < 129.8 || $lon > 131.2) {
            throw new RuntimeException('地図をクリックして位置を指定してください');
        }
        if (!$id && $f['name'] === '') throw new RuntimeException('名前を入力してください');
        if (!in_array($f['category'], $catCodes, true)) throw new RuntimeException('種別を選んでください');
        if ($f['source_url'] !== '' && !preg_match('#^https?://#', $f['source_url'])) {
            throw new RuntimeException('出典のURLは http:// か https:// で始まるものを入力してください');
        }

        if (!$id) {
            // 新規：近くに似た名前の地点があれば、まず知らせる（「それでも追加」で登録）
            $chk = sb_rpc('manual_check', ['p_lat' => $lat, 'p_lon' => $lon, 'p_name' => $f['name']]);
            if (empty($chk['area_code'])) throw new RuntimeException('この位置は対象エリア（福岡市7区）の外です');
            $similar = array_values(array_filter($chk['nearby'] ?? [], fn($n) => !empty($n['similar'])));
            if (!$similar || !empty($_POST['confirm_dup'])) {
                $r = sb_rpc('create_manual_spot', [
                    'p_name' => $f['name'], 'p_lat' => $lat, 'p_lon' => $lon, 'p_category' => $f['category'],
                    'p_summary' => $f['summary'] ?: null,
                    'p_confidence' => array_key_exists($f['confidence'], PICK_CONF) ? $f['confidence'] : 'unverified',
                    'p_source_url' => $f['source_url'] ?: null, 'p_source_note' => $f['source_note'] ?: null,
                    'p_address' => gsi_address($lat, $lon), 'p_user' => current_user(),
                ]);
                header('Location: spot.php?' . http_build_query(['id' => (int)$r['id'], 'done' => 'created'])); exit;
            }
        } else {
            $moved = abs($lat - (float)$s['lat']) > 0.000005 || abs($lon - (float)$s['lon']) > 0.000005;
            sb_rpc('edit_spot', [
                'p_id' => $id, 'p_name' => $f['name'] ?: null,
                'p_lat' => $moved ? $lat : null, 'p_lon' => $moved ? $lon : null,
                'p_category' => $f['category'], 'p_extra' => null,
                'p_address' => $moved ? gsi_address($lat, $lon) : null,
                'p_user' => current_user(), 'p_note' => $f['note'] ?: null,
            ]);
            header('Location: spot.php?' . http_build_query(['id' => $id, 'done' => 'edited'])); exit;
        }
    } catch (Throwable $e) {
        // データベースが返す理由（「対象エリアの外です」など）はそのまま見せる
        $error = preg_replace('/^.*?ERROR:\s*/su', '', $e->getMessage());
    }
}

$center = ($f['lat'] !== '' && $f['lon'] !== '') ? [(float)$f['lat'], (float)$f['lon']] : DEFAULT_CENTER;
$hasPoint = $f['lat'] !== '' && $f['lon'] !== '';
$embedKey = (string)cfg('google_maps_embed_key', '');
$isOsm = $s && ($s['origin'] ?? '') === 'osm';
page_header($s ? '地点を直す' : '地点を追加', $s ? '' : 'edit.php');
?>
<section class="card editpage">
  <?php if ($s): ?>
    <p class="crumb small muted"><a href="spot.php?id=<?= (int)$s['id'] ?>">← <?= h(nm($s['name'])) ?></a> <span class="code"><?= h($s['code']) ?></span></p>
    <h1>位置・名前・種別を直す</h1>
    <p class="small muted"><?= $isOsm
        ? 'OpenStreetMapから取り込んだ地点です。直すと「位置固定」になり、自動収集では変わりません。以後、OSMの側が変わったときだけ「要確認」になります。'
        : '手入力の地点です。自動収集では動かず、消えません。' ?></p>
  <?php else: ?>
    <h1>地点を追加</h1>
    <p class="small muted">OpenStreetMapにない場所を、地図で位置を指定して追加します。追加した地点は「手入力」として扱われ、自動収集では動かず、消えません。
      位置は撮影の目安にする場所（門の前、道からの撮影位置など）に置いてください。</p>
  <?php endif; ?>
</section>

<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($similar): ?>
<section class="warnbox">
  <h2>近くに似た名前の地点があります</h2>
  <ul class="compact">
    <?php foreach ($similar as $n): ?>
      <li><?= dot($n['grp_code']) ?><a href="spot.php?id=<?= (int)$n['id'] ?>" target="_blank" rel="noopener"><?= h(nm($n['name'])) ?></a>
        <span class="muted small"><?= h($n['category']) ?>・<?= (int)$n['meters'] ?>m・<?= h($n['code']) ?><?= $n['origin'] === 'manual' ? '・手入力' : '' ?></span></li>
    <?php endforeach; ?>
  </ul>
  <p class="small muted">同じ場所なら、既存の地点を開いて位置・名前を直してください。別の場所なら、下の「それでも追加する」にチェックして登録します。</p>
</section>
<?php endif; ?>

<form method="post" class="editgrid" id="editform">
  <?= csrf_field() ?>
  <?php if ($s): ?><input type="hidden" name="id" value="<?= (int)$s['id'] ?>"><?php endif; ?>
  <div class="pickcol">
    <div id="pickmap" class="map pickmap" data-lat="<?= h($center[0]) ?>" data-lon="<?= h($center[1]) ?>"
         data-has="<?= $hasPoint ? '1' : '0' ?>" data-zoom="<?= $hasPoint ? 18 : 13 ?>"></div>
    <p class="small muted">地図をクリックするとピンが立ちます。ピンはドラッグで動かせます。地図はOpenStreetMap。</p>
    <label>位置を貼り付け
      <input id="paste" placeholder="33.5936, 130.4135　またはGoogleマップのURL" autocomplete="off"></label>
    <div class="row">
      <label>緯度<input name="lat" id="lat" value="<?= h($f['lat']) ?>" inputmode="decimal" required></label>
      <label>経度<input name="lon" id="lon" value="<?= h($f['lon']) ?>" inputmode="decimal" required></label>
    </div>
    <?php if ($embedKey !== ''): ?>
      <iframe id="satview" class="map satview" loading="lazy" referrerpolicy="no-referrer-when-downgrade" title="航空写真で確認"
              data-key="<?= h($embedKey) ?>" <?= $hasPoint ? '' : 'hidden' ?>></iframe>
    <?php endif; ?>
    <p class="card-foot"><a class="gmaps-btn" id="gmlink" target="_blank" rel="noopener" href="#">Googleマップで確認</a>
      <a class="gmaps-btn" id="svlink" target="_blank" rel="noopener" href="#">ストリートビュー</a></p>
  </div>

  <div class="formcol">
    <label>名前<input name="name" id="name" value="<?= h($f['name']) ?>" maxlength="100" <?= $s ? '' : 'required' ?>
      placeholder="例：承天寺"></label>
    <label>種別<select name="category" required>
      <option value="">選んでください</option>
      <?php foreach ($cats as $c): ?><option value="<?= h($c['code']) ?>" <?= $f['category'] === $c['code'] ? 'selected' : '' ?>><?= h($c['name']) ?></option><?php endforeach; ?>
    </select></label>
    <?php if (!$s): ?>
      <label>説明（任意）<textarea name="summary" rows="3" placeholder="どんな場所か、撮影に向く点など"><?= h($f['summary']) ?></textarea></label>
      <label>確度<select name="confidence">
        <?php foreach (PICK_CONF as $k => $v): ?><option value="<?= $k ?>" <?= $f['confidence'] === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?>
      </select></label>
      <label>出典のURL（任意）<input name="source_url" value="<?= h($f['source_url']) ?>" placeholder="https://"></label>
      <label>出典のメモ（任意）<input name="source_note" value="<?= h($f['source_note']) ?>" placeholder="例：寺の公式サイト、2026年10月に現地で確認"></label>
    <?php else: ?>
      <label>メモ（任意・変更履歴に残ります）<input name="note" value="<?= h($f['note']) ?>" placeholder="例：撮影位置を山門の前に"></label>
    <?php endif; ?>

    <div class="nearbox">
      <h2>近くの地点（150m）</h2>
      <ul class="compact small" id="nearlist"><li class="muted">位置を指定すると表示します。</li></ul>
    </div>

    <?php if ($similar): ?>
      <label class="tag"><input type="checkbox" name="confirm_dup" value="1"> それでも追加する（別の場所）</label>
    <?php endif; ?>
    <p class="actions"><button><?= $s ? '保存' : '追加' ?></button>
      <a class="small" href="<?= $s ? 'spot.php?id=' . (int)$s['id'] : 'index.php' ?>">やめる</a></p>
  </div>
</form>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
(function () {
  const el = document.getElementById('pickmap');
  const latIn = document.getElementById('lat'), lonIn = document.getElementById('lon');
  const nameIn = document.getElementById('name'), list = document.getElementById('nearlist');
  const sat = document.getElementById('satview');
  const spotId = <?= json_encode($s ? (int)$s['id'] : null) ?>;
  const COLORS = <?= json_encode(GROUP_COLOR) ?>;
  const map = L.map(el, { scrollWheelZoom: true }).setView([+el.dataset.lat, +el.dataset.lon], +el.dataset.zoom);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    { attribution: '© OpenStreetMap contributors', maxZoom: 19 }).addTo(map);
  let marker = null, others = L.layerGroup().addTo(map), timer = null;

  function setPoint(lat, lon, pan) {
    lat = Math.round(lat * 1e7) / 1e7; lon = Math.round(lon * 1e7) / 1e7;
    if (!marker) {
      marker = L.marker([lat, lon], { draggable: true, autoPan: true }).addTo(map);
      marker.on('dragend', () => { const p = marker.getLatLng(); setPoint(p.lat, p.lng, false); });
    } else marker.setLatLng([lat, lon]);
    if (pan) map.setView([lat, lon], Math.max(map.getZoom(), 17));
    latIn.value = lat; lonIn.value = lon;
    const q = lat + ',' + lon;
    document.getElementById('gmlink').href = 'https://www.google.com/maps/search/?' + new URLSearchParams({ api: 1, query: q });
    document.getElementById('svlink').href = 'https://www.google.com/maps/@?' + new URLSearchParams({ api: 1, map_action: 'pano', viewpoint: q });
    clearTimeout(timer); timer = setTimeout(() => refresh(lat, lon), 400);
  }
  function refresh(lat, lon) {
    if (sat) {
      sat.hidden = false;
      sat.src = 'https://www.google.com/maps/embed/v1/view?' + new URLSearchParams({ key: sat.dataset.key,
        center: lat + ',' + lon, zoom: 19, maptype: 'satellite', language: 'ja', region: 'JP' });
    }
    const p = new URLSearchParams({ check: 1, lat, lon, name: nameIn.value });
    if (spotId) p.set('id', spotId);
    fetch('edit.php?' + p, { credentials: 'same-origin' }).then(r => r.json()).then(d => {
      list.textContent = ''; others.clearLayers();
      if (d.error) { list.innerHTML = '<li class="error"></li>'; list.firstChild.textContent = d.error; return; }
      if (!d.area_code) { const li = document.createElement('li'); li.className = 'error'; li.textContent = 'この位置は対象エリア（福岡市7区）の外です'; list.appendChild(li); }
      if (!d.nearby.length && d.area_code) { const li = document.createElement('li'); li.className = 'muted'; li.textContent = '近くに登録済みの地点はありません'; list.appendChild(li); }
      d.nearby.forEach(n => {
        const li = document.createElement('li'); if (n.similar) li.className = 'similar';
        const a = document.createElement('a'); a.href = 'spot.php?id=' + n.id; a.target = '_blank'; a.rel = 'noopener'; a.textContent = n.name;
        const m = document.createElement('span'); m.className = 'muted'; m.textContent = ' ' + n.category + '・' + n.meters + 'm' + (n.similar ? '・似た名前' : '');
        li.append(a, m); list.appendChild(li);
        // 地図にも小さな点で（景観の色）
        const c = L.circleMarker([n.lat, n.lon], { radius: 5, color: '#fff', weight: 1.5, fillColor: COLORS[n.grp_code] || '#9C978E', fillOpacity: 1 });
        const tip = document.createElement('span'); tip.textContent = n.name + '（' + n.meters + 'm）';
        c.bindTooltip(tip).addTo(others);
      });
    }).catch(() => {});
  }
  // 「33.59, 130.41」や Google マップの URL（@緯度,経度 / !3d緯度!4d経度 / q=緯度,経度）から位置を読む
  function parse(t) {
    t = decodeURIComponent(t || '');
    let m = t.match(/!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)/) || t.match(/@(-?\d+\.\d+),\s*(-?\d+\.\d+)/)
         || t.match(/[?&](?:q|query|ll|center|viewpoint)=(-?\d+\.\d+),\s*(-?\d+\.\d+)/) || t.match(/^\s*(-?\d+\.\d+)\s*[, ]\s*(-?\d+\.\d+)\s*$/);
    return m ? [+m[1], +m[2]] : null;
  }
  document.getElementById('paste').addEventListener('input', e => {
    const p = parse(e.target.value);
    e.target.classList.toggle('bad', !p && e.target.value.trim() !== '');
    if (p) setPoint(p[0], p[1], true);
  });
  [latIn, lonIn].forEach(i => i.addEventListener('change', () => {
    if (isFinite(+latIn.value) && isFinite(+lonIn.value) && latIn.value && lonIn.value) setPoint(+latIn.value, +lonIn.value, true);
  }));
  nameIn.addEventListener('change', () => { if (marker) { const p = marker.getLatLng(); refresh(p.lat, p.lng); } });
  map.on('click', e => setPoint(e.latlng.lat, e.latlng.lng, false));
  if (el.dataset.has === '1') setPoint(+el.dataset.lat, +el.dataset.lon, false);
})();
</script>
<?php page_footer();
