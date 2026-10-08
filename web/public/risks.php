<?php
require __DIR__ . '/bootstrap.php';
require_login();
$msg = ''; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    try {
        $action = $_POST['action'] ?? '';
        sb_rpc('review_risk', [
            'p_id' => (int)$_POST['id'],
            'p_verified' => $action === 'verify',
            'p_dismiss' => $action === 'dismiss',
            'p_note' => current_user() . ' ' . trim($_POST['note'] ?? ''),
        ]);
        $msg = $action === 'dismiss' ? '該当なしとして閉じました' : '確認済みにしました';
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

try {
    $rows = sb_get('change_risks?verified=is.false&risk_type=neq.none'
        . '&select=id,risk_type,evidence,source_url,detected_at,review_by,spots(code,name)&order=review_by.asc&limit=100') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $rows = []; }

page_header('変化リスク', 'risks.php');
?>
<section class="card">
  <h1>変化リスク（要確認）</h1>
  <p class="muted small">監視ページで地点名と「解体・閉店・再開発」等の語が一緒に見つかったものです。一次資料で確認してから「確認済み」にしてください。</p>
</section>
<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
<?php if (!$rows && !$error): ?><p class="card">要確認の項目はありません。</p><?php endif; ?>

<?php foreach ($rows as $r): ?>
  <article class="card">
    <h3><?= h($r['spots']['name'] ?? '') ?> <span class="chip warn"><?= h(RISK_LABEL[$r['risk_type']] ?? $r['risk_type']) ?></span></h3>
    <p class="small muted">検出 <?= h(substr($r['detected_at'], 0, 10)) ?>・確認期限 <?= h($r['review_by']) ?>・<?= h($r['spots']['code'] ?? '') ?></p>
    <blockquote class="small"><?= nl2br(h($r['evidence'])) ?></blockquote>
    <?php if (preg_match('#^https?://#', (string)$r['source_url'])): ?><p class="small"><a href="<?= h($r['source_url']) ?>" target="_blank" rel="noopener">出典ページ</a></p><?php endif; ?>
    <form method="post" class="row">
      <?= csrf_field() ?>
      <input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
      <input name="note" placeholder="メモ（確認した資料など）">
      <button name="action" value="verify">確認済み</button>
      <button name="action" value="dismiss" class="secondary">該当なし</button>
    </form>
  </article>
<?php endforeach; ?>
<?php page_footer();
