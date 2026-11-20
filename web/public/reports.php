<?php
// 誤りの連絡（ログイン必須）：地点ページの「この情報は違う」から届いた連絡を見て、直したら「対応した」、取り合わないなら「見送り」にする
require __DIR__ . '/bootstrap.php';
require_login();
$error = ''; $msg = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    $st = (string)($_POST['status'] ?? '');
    try {
        sb_rpc('report_handle', ['p_id' => (int)($_POST['id'] ?? 0), 'p_status' => $st, 'p_user' => current_user(),
                                 'p_note' => trim((string)($_POST['note'] ?? '')) ?: null]);
        $msg = ['done' => '対応済みにしました', 'dismissed' => '見送りにしました', 'open' => '未対応に戻しました'][$st] ?? '';
    } catch (Throwable $e) { $error = preg_replace('/^.*?ERROR:\s*/su', '', $e->getMessage()); }
}
$view = in_array($_GET['status'] ?? 'open', ['open', 'done', 'dismissed', 'all'], true) ? ($_GET['status'] ?? 'open') : 'open';
$rows = [];
try { $rows = sb_rpc('reports_list', ['p_status' => $view, 'p_limit' => 200]) ?? []; } catch (Throwable $e) { $error = $error ?: $e->getMessage(); }
const REPORT_STATUS_LABEL = ['open' => '未対応', 'done' => '対応済み', 'dismissed' => '見送り'];
page_header('誤りの連絡', 'reports.php');
?>
<section class="card">
  <h1>誤りの連絡</h1>
  <p class="muted">地点ページの「この情報は違う」から届いた連絡です（名前・連絡先は取っていません。返事はしません）。直したら「対応した」にします。</p>
  <p><?php foreach (['open' => '未対応', 'done' => '対応済み', 'dismissed' => '見送り', 'all' => 'すべて'] as $k => $v): ?>
    <a class="chip<?= $view === $k ? ' on' : '' ?>" href="?status=<?= h($k) ?>"><?= h($v) ?></a><?php endforeach; ?></p>
  <?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
  <?php if (!$rows): ?><p class="muted">該当する連絡はありません</p><?php endif; ?>
  <?php foreach ($rows as $r): ?>
    <div class="block">
      <p><span class="chip"><?= h(REPORT_KIND_LABEL[$r['kind']] ?? $r['kind']) ?></span>
        <?php if ($r['spot_id']): ?><a href="spot.php?id=<?= (int)$r['spot_id'] ?>"><?= h(nm($r['spot_name'] ?? '')) ?></a><?php else: ?><span class="muted"><?= h($r['spot_name'] ?: '（地点なし）') ?></span><?php endif; ?>
        <span class="small muted"><?= h(substr((string)$r['created_at'], 0, 16)) ?>　<?= h(REPORT_STATUS_LABEL[$r['status']] ?? $r['status']) ?><?= $r['handled_by'] ? '（' . h($r['handled_by']) . '）' : '' ?></span></p>
      <p><?= nl2br(h($r['note'])) ?></p>
      <?php if (!empty($r['handled_note'])): ?><p class="small muted">メモ：<?= h($r['handled_note']) ?></p><?php endif; ?>
      <form method="post" class="admin-actions"><?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
        <input name="note" placeholder="メモ（任意）">
        <?php if ($r['status'] !== 'done'): ?><button name="status" value="done" class="secondary small-btn">対応した</button><?php endif; ?>
        <?php if ($r['status'] !== 'dismissed'): ?><button name="status" value="dismissed" class="secondary small-btn">見送り</button><?php endif; ?>
        <?php if ($r['status'] !== 'open'): ?><button name="status" value="open" class="secondary small-btn">未対応に戻す</button><?php endif; ?>
      </form>
    </div>
  <?php endforeach; ?>
</section>
<?php page_footer();
