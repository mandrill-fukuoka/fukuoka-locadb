<?php
// 収集の記録（ログイン必須）：毎週の収集の結果・差分レポート・データの診断
require __DIR__ . '/bootstrap.php';
require_login();
$error = '';
try {
    $runs = sb_get('data_runs?select=id,mode,status,started_at,finished_at,source,code_version,rules_version,summary,report,error'
        . '&order=id.desc&limit=15') ?? [];
    $diag = sb_rpc('diag_summary') ?? [];
    $jobs = sb_get('job_runs?select=run_id,job,started_at,finished_at,stats,error&order=id.desc&limit=30') ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $runs = $jobs = []; $diag = []; }
$open = (int)($_GET['run'] ?? ($runs[0]['id'] ?? 0));
$MODE = ['apply' => '本番', 'dry_run' => '試し'];
$ST = ['ok' => '完了', 'failed' => '失敗', 'stopped' => '停止（反映せず）', 'running' => '実行中'];
page_header('収集の記録', 'runs.php');
?>
<section class="card">
  <h1>収集の記録</h1>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
  <?php if ($diag): ?>
  <h2>いまのデータ</h2>
  <dl class="diag">
    <dt>掲載中の地点</dt><dd><?= (int)$diag['spots_live'] ?></dd>
    <dt>要確認</dt><dd><a href="spots.php?queue=review"><?= (int)($diag['spots_by_data_status']['needs_review'] ?? 0) ?></a></dd>
    <dt>対象外</dt><dd><a href="spots.php?queue=archived"><?= (int)($diag['spots_by_data_status']['archived'] ?? 0) ?></a></dd>
    <dt>人が確認した地点</dt><dd><?= (int)$diag['protected'] ?></dd>
    <dt>重複の疑い</dt><dd><a href="spots.php?queue=dups"><?= (int)$diag['duplicate_pairs'] ?></a></dd>
    <dt>住所なし</dt><dd><?= (int)$diag['no_address'] ?></dd>
    <dt>OSMで見つからない要素</dt><dd><?= (int)$diag['features_missing'] ?></dd>
    <dt>再確認が必要な保存済みの流れ</dt><dd><a href="flows.php?f=saved"><?= (int)$diag['clusters_needing_review'] ?></a></dd>
    <dt>紐付けの不整合</dt><dd><?= (int)$diag['orphan_links'] + (int)$diag['spots_without_feature'] + (int)$diag['area_mismatch'] ?></dd>
  </dl>
  <?php endif; ?>
</section>

<?php foreach ($runs as $r): $s = $r['summary'] ?? []; ?>
  <details class="card run" <?= $r['id'] === $open ? 'open' : '' ?>>
    <summary>
      <strong>#<?= (int)$r['id'] ?></strong> <?= h(substr((string)$r['started_at'], 0, 16)) ?>
      <span class="chip <?= $r['status'] === 'ok' ? 'ok' : ($r['status'] === 'running' ? 'dim' : 'warn') ?>"><?= h($MODE[$r['mode']] ?? $r['mode']) ?>・<?= h($ST[$r['status']] ?? $r['status']) ?></span>
      <?php if ($s): ?><span class="small muted">新規 <?= (int)($s['created'] ?? 0) ?>・更新 <?= (int)($s['moved'] ?? 0) + (int)($s['renamed'] ?? 0) + (int)($s['recategorized'] ?? 0) ?>・要確認 <?= (int)($s['needs_review'] ?? 0) ?>・対象外 <?= (int)($s['archived'] ?? 0) ?></span><?php endif; ?>
    </summary>
    <p class="small muted">OSMデータの時点 <?= h($r['source']['osm_timestamp'] ?? '—') ?>・コード <?= h($r['code_version'] ?? '') ?>・収集条件 <?= h($r['rules_version'] ?? '') ?></p>
    <?php if ($r['error']): ?><p class="error small"><?= h($r['error']) ?></p><?php endif; ?>
    <?php if ($r['report']): ?><pre class="report"><?= h($r['report']) ?></pre><?php endif; ?>
    <?php $js = array_filter($jobs, fn($j) => (int)$j['run_id'] === (int)$r['id']); if ($js): ?>
      <ul class="compact small"><?php foreach ($js as $j): ?>
        <li><?= h($j['job']) ?>：<?= h(json_encode($j['stats'], JSON_UNESCAPED_UNICODE)) ?><?= $j['error'] ? ' <span class="error">' . h($j['error']) . '</span>' : '' ?></li>
      <?php endforeach; ?></ul>
    <?php endif; ?>
  </details>
<?php endforeach; ?>
<?php page_footer();
