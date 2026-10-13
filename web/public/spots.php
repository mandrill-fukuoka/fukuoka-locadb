<?php
// 地点の確認（ログイン必須）：要確認・未確認・対象外・重複の疑い
require __DIR__ . '/bootstrap.php';
require_login();

$queue  = $_GET['queue'] ?? 'review';
if (!in_array($queue, ['review', 'unreviewed', 'manual', 'archived', 'all', 'dups'], true)) $queue = 'review';
$area   = $_GET['area'] ?? '';
$status = $_GET['status'] ?? '';
$q      = trim($_GET['q'] ?? '');
$page   = max(0, (int)($_GET['p'] ?? 0));
$per    = 30;
$msg = ''; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    try {
        if (($_POST['action'] ?? '') === 'merge') {
            sb_rpc('merge_spots', ['p_keep' => (int)$_POST['keep'], 'p_drop' => (int)$_POST['drop'], 'p_user' => current_user()]);
            $msg = '2つの地点を1つにまとめました';
        } elseif (in_array($_POST['action'] ?? '', ['archive', 'restore'], true)) {
            sb_rpc('set_spot_state', ['p_id' => (int)$_POST['id'], 'p_action' => $_POST['action'], 'p_user' => current_user(),
                                      'p_note' => trim($_POST['note'] ?? '') ?: null]);
            $msg = '「' . ($_POST['name'] ?? '') . '」を' . ($_POST['action'] === 'archive' ? '対象外にしました' : '掲載に戻しました');
        } else {
            sb_rpc('review_spot', [
                'p_id' => (int)$_POST['id'],
                'p_status' => array_key_exists($_POST['status'] ?? '', STATUS_LABEL) ? $_POST['status'] : 'candidate',
                'p_confidence' => array_key_exists($_POST['confidence'] ?? '', CONF_LABEL) ? $_POST['confidence'] : 'unverified',
                'p_summary' => trim($_POST['summary'] ?? '') ?: null,
                'p_tags' => array_values($_POST['tags'] ?? []),
                'p_reviewer' => current_user(),
                'p_resolve' => !empty($_POST['resolve']),
            ]);
            $msg = '「' . ($_POST['name'] ?? '') . '」を保存しました';
        }
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

$rows = []; $dups = []; $tags = [];
try {
    if ($queue === 'dups') {
        $dups = sb_rpc('duplicate_candidates', ['p_limit' => 100, 'p_meters' => 150]) ?? [];
    } else {
        $tags = sb_get('tags?select=code,name,facet&order=facet,code') ?? [];
        $rows = sb_rpc('review_queue', [
            'p_area' => $area ?: null, 'p_status' => $status ?: null, 'p_q' => $q ?: null,
            'p_offset' => $page * $per, 'p_limit' => $per, 'p_queue' => $queue,
        ]) ?? [];
    }
} catch (Throwable $e) { $error = $e->getMessage(); }

$qs = fn(array $o) => '?' . http_build_query(array_merge(['queue' => $queue, 'area' => $area, 'status' => $status, 'q' => $q], $o));
$tabs = ['review' => '要確認', 'unreviewed' => '未確認', 'manual' => '手入力・人が直した', 'dups' => '重複の疑い', 'archived' => '対象外', 'all' => 'すべて'];
page_header('地点の確認', 'spots.php');
?>
<section class="card">
  <h1>地点の確認</h1>
  <p class="small muted">「要確認」は、人が確認した地点でOSMの位置・名前・種別が変わったもの、OSMから見つからなくなったものです。自動では変更せず、ここで判断します。</p>
  <nav class="tabs">
    <?php foreach ($tabs as $k => $v): ?><a href="<?= h($qs(['queue' => $k, 'p' => 0])) ?>" class="<?= $queue === $k ? 'on' : '' ?>"><?= $v ?></a><?php endforeach; ?>
  </nav>
  <?php if ($queue !== 'dups'): ?>
  <form class="row filters" method="get">
    <input type="hidden" name="queue" value="<?= h($queue) ?>">
    <label>エリア<select name="area"><option value="">すべて</option>
      <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <label>確認の状態<select name="status"><option value="">すべて</option>
      <?php foreach (STATUS_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $status === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <label>名称・住所<input name="q" value="<?= h($q) ?>" placeholder="部分一致"></label>
    <button>絞り込み</button>
  </form>
  <?php endif; ?>
</section>

<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($queue === 'dups'): ?>
  <?php if (!$dups && !$error): ?><p class="card">重複の疑いはありません。</p><?php endif; ?>
  <p class="small muted">同じ名前・同じ景観で150m以内にある別々の地点です。同じ場所なら、残す方を選んでまとめてください（もう一方は対象外になり、履歴に残ります）。別の場所なら何もしなくて構いません。</p>
  <?php foreach ($dups as $d): ?>
    <article class="card dup">
      <h3><?= h($d['a_name']) ?> <span class="muted small"><?= h($d['category']) ?>・<?= (int)$d['meters'] ?>m 離れている</span></h3>
      <form method="post" class="row"><?= csrf_field() ?><input type="hidden" name="action" value="merge">
        <span class="small"><a href="spot.php?id=<?= (int)$d['a_id'] ?>"><?= h($d['a_code']) ?></a> と <a href="spot.php?id=<?= (int)$d['b_id'] ?>"><?= h($d['b_code']) ?></a></span>
        <button name="keep" value="<?= (int)$d['a_id'] ?>" class="secondary small-btn" onclick="this.form.drop.value='<?= (int)$d['b_id'] ?>'"><?= h($d['a_code']) ?> に統合</button>
        <button name="keep" value="<?= (int)$d['b_id'] ?>" class="secondary small-btn" onclick="this.form.drop.value='<?= (int)$d['a_id'] ?>'"><?= h($d['b_code']) ?> に統合</button>
        <input type="hidden" name="drop" value="">
        <a class="small" target="_blank" rel="noopener" href="<?= h('https://www.google.com/maps/dir/?' . http_build_query(['api' => 1, 'origin' => $d['a_lat'] . ',' . $d['a_lon'], 'destination' => $d['b_lat'] . ',' . $d['b_lon'], 'travelmode' => 'walking'])) ?>">地図で2点を見る</a>
      </form>
    </article>
  <?php endforeach; ?>
<?php else: ?>
  <?php if (!$rows && !$error): ?><p class="card">該当する地点はありません。</p><?php endif; ?>
  <?php foreach ($rows as $s): $has = array_column($s['tags'] ?? [], 'origin', 'code'); ?>
    <details class="card spot" <?= $queue === 'review' ? 'open' : '' ?>>
      <summary>
        <strong><?= h(nm($s['name'])) ?></strong>
        <span class="muted"><?= h($s['category']) ?>・<?= h($s['address'] ?: (AREA_LABEL[$s['area_code']] ?? '')) ?></span>
        <span class="chip dim"><?= h(STATUS_LABEL[$s['status']] ?? '') ?>／<?= h(CONF_LABEL[$s['confidence']] ?? '') ?></span>
        <?php if (($s['origin'] ?? '') === 'manual'): ?><span class="chip">手入力</span><?php elseif (!empty($s['locked'])): ?><span class="chip">位置固定</span><?php endif; ?>
        <?php if ($s['data_status'] === 'needs_review'): ?><span class="chip warn"><?= h($s['review_reason'] ?: '要確認') ?></span><?php endif; ?>
        <?php if ($s['data_status'] === 'archived'): ?><span class="chip warn">対象外：<?= h(preg_replace('/^(auto|manual):/', '', (string)$s['archived_reason'])) ?></span><?php endif; ?>
        <?php foreach (($s['risks'] ?? []) as $r): if ($r['type'] !== 'none'): ?><span class="chip warn"><?= h(RISK_LABEL[$r['type']]) ?><?= $r['verified'] ? '' : '（要確認）' ?></span><?php endif; endforeach; ?>
      </summary>
      <p class="small"><a href="spot.php?id=<?= (int)$s['id'] ?>">詳細・変更履歴</a>・<a href="edit.php?id=<?= (int)$s['id'] ?>">位置・名前を直す</a>・<?= h($s['code']) ?>・時代感 <?= h($s['era_feel'] ?? '—') ?>
        ・<a target="_blank" rel="noopener" href="https://www.google.com/maps?q=<?= h($s['lat']) ?>,<?= h($s['lon']) ?>">地図</a></p>
      <?php if ($s['data_status'] === 'archived'): ?>
        <form method="post" class="row"><?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$s['id'] ?>"><input type="hidden" name="name" value="<?= h(nm($s['name'])) ?>">
          <input name="note" placeholder="メモ"><button name="action" value="restore" class="secondary">掲載に戻す</button></form>
      <?php else: ?>
      <form method="post" action="<?= h($qs(['p' => $page])) ?>">
        <?= csrf_field() ?>
        <input type="hidden" name="id" value="<?= (int)$s['id'] ?>">
        <input type="hidden" name="name" value="<?= h(nm($s['name'])) ?>">
        <label>説明<textarea name="summary" rows="2"><?= h($s['summary']) ?></textarea></label>
        <fieldset class="tags"><legend>タグ（チェックしたものを人の判断として保存）</legend>
          <?php foreach ($tags as $t): ?>
            <label class="tag"><input type="checkbox" name="tags[]" value="<?= h($t['code']) ?>" <?= isset($has[$t['code']]) ? 'checked' : '' ?>><?= h($t['name']) ?></label>
          <?php endforeach; ?>
        </fieldset>
        <div class="row">
          <label>確認の状態<select name="status">
            <?php foreach (STATUS_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $s['status'] === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
          <label>確度<select name="confidence">
            <?php foreach (CONF_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $s['confidence'] === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
          <?php if ($s['data_status'] === 'needs_review'): ?>
            <label class="tag"><input type="checkbox" name="resolve" value="1" checked> 要確認を解消する</label>
          <?php endif; ?>
        </div>
        <button>保存</button>
        <button name="action" value="archive" class="secondary">対象外にする</button>
      </form>
      <?php endif; ?>
    </details>
  <?php endforeach; ?>
  <nav class="pager">
    <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
    <?php if (count($rows) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
  </nav>
<?php endif; ?>
<?php page_footer();
