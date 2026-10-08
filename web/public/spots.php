<?php
require __DIR__ . '/bootstrap.php';
require_login();

$area   = $_GET['area'] ?? '';
$status = $_GET['status'] ?? 'ai_tagged';
$q      = trim($_GET['q'] ?? '');
$page   = max(0, (int)($_GET['p'] ?? 0));
$per    = 30;
$msg = ''; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    try {
        sb_rpc('review_spot', [
            'p_id' => (int)$_POST['id'],
            'p_status' => array_key_exists($_POST['status'] ?? '', STATUS_LABEL) ? $_POST['status'] : 'ai_tagged',
            'p_confidence' => array_key_exists($_POST['confidence'] ?? '', CONF_LABEL) ? $_POST['confidence'] : 'ai_estimated',
            'p_summary' => trim($_POST['summary'] ?? '') ?: null,
            'p_tags' => array_values($_POST['tags'] ?? []),
            'p_reviewer' => current_user(),
        ]);
        $msg = '「' . ($_POST['name'] ?? '') . '」を保存しました';
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

try {
    $tags = sb_get('tags?select=code,name,facet&order=facet,code');
    $rows = sb_rpc('review_queue', [
        'p_area' => $area ?: null, 'p_status' => $status ?: null, 'p_q' => $q ?: null,
        'p_offset' => $page * $per, 'p_limit' => $per,
    ]) ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $rows = []; $tags = []; }

$qs = fn(array $o) => '?' . http_build_query(array_merge(['area' => $area, 'status' => $status, 'q' => $q], $o));
page_header('地点の確認', 'spots.php');
?>
<form class="card row filters" method="get">
  <label>エリア<select name="area"><option value="">すべて</option>
    <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
  <label>状態<select name="status"><option value="">すべて</option>
    <?php foreach (STATUS_LABEL as $k => $v): if ($k === 'archived') continue; ?><option value="<?= $k ?>" <?= $status === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
  <label>名称<input name="q" value="<?= h($q) ?>" placeholder="部分一致"></label>
  <button>絞り込み</button>
</form>

<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
<?php if (!$rows && !$error): ?><p class="card">該当する地点はありません。</p><?php endif; ?>

<?php foreach ($rows as $s):
    $has = array_column($s['tags'] ?? [], 'origin', 'code'); ?>
  <details class="card spot">
    <summary>
      <strong><?= h($s['name']) ?></strong>
      <span class="muted"><?= h($s['category']) ?>・<?= h(AREA_LABEL[$s['area_code']] ?? '') ?></span>
      <span class="chip dim"><?= h(CONF_LABEL[$s['confidence']] ?? '') ?></span>
      <?php foreach (($s['risks'] ?? []) as $r): if ($r['type'] !== 'none'): ?><span class="chip warn"><?= h(RISK_LABEL[$r['type']]) ?><?= $r['verified'] ? '' : '（要確認）' ?></span><?php endif; endforeach; ?>
    </summary>
    <form method="post" action="<?= h($qs(['p' => $page])) ?>">
      <?= csrf_field() ?>
      <input type="hidden" name="id" value="<?= (int)$s['id'] ?>">
      <input type="hidden" name="name" value="<?= h($s['name']) ?>">
      <p class="small"><?= h($s['code']) ?>・時代感 <?= h($s['era_feel'] ?? '—') ?>・撮影範囲 <?= h($s['shoot_scope']) ?>
        ・<a target="_blank" rel="noopener" href="https://www.google.com/maps?q=<?= h($s['lat']) ?>,<?= h($s['lon']) ?>">地図</a></p>
      <label>説明<textarea name="summary" rows="2"><?= h($s['summary']) ?></textarea></label>
      <fieldset class="tags"><legend>タグ（チェックしたものを人の判断として保存）</legend>
        <?php foreach ($tags as $t): ?>
          <label class="tag"><input type="checkbox" name="tags[]" value="<?= h($t['code']) ?>" <?= isset($has[$t['code']]) ? 'checked' : '' ?>><?= h($t['name']) ?></label>
        <?php endforeach; ?>
      </fieldset>
      <div class="row">
        <label>状態<select name="status">
          <?php foreach (STATUS_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $s['status'] === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
        <label>確度<select name="confidence">
          <?php foreach (CONF_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $s['confidence'] === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
      </div>
      <button>保存</button>
    </form>
  </details>
<?php endforeach; ?>

<nav class="pager">
  <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
  <?php if (count($rows) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
</nav>
<?php page_footer();
