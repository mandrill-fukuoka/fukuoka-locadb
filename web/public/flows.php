<?php
// おすすめの流れ：検索から蓄積された流れ・保存した流れ・自動生成の流れ
require __DIR__ . '/bootstrap.php';
// 閲覧は誰でも可。保存・外すはログインが必要

$filter = $_GET['f'] ?? 'all';
if (!in_array($filter, ['all', 'saved', 'popular', 'auto'], true)) $filter = 'all';
$area = $_GET['area'] ?? '';
if ($area !== '' && !isset(AREA_LABEL[$area])) $area = '';
$msg = ''; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    deny_unless_logged_in();
    try {
        if (($_POST['action'] ?? '') === 'reviewed') {
            sb_rpc('clear_flow_review', ['p_id' => (int)$_POST['id'], 'p_user' => current_user()]);
            $msg = '再確認済みにしました';
        } elseif (($_POST['action'] ?? '') === 'archive') {
            sb_rpc('archive_flow', ['p_id' => (int)$_POST['id']]);
            $msg = 'おすすめから外しました';
        } elseif (($_POST['action'] ?? '') === 'save') {
            // 一覧に出ている流れを保存（route_key から再取得して保存）
            $key = (string)($_POST['route_key'] ?? '');
            $flow = $_SESSION['flow_cache'][$key] ?? null;
            if ($flow) { sb_rpc('upsert_route', ['p_route' => $flow, 'p_origin' => 'search', 'p_user' => current_user()]); $msg = '保存しました'; }
        }
    } catch (Throwable $e) { $error = $e->getMessage(); }
}

try {
    $flows = sb_rpc('recommended_flows', ['p_area' => $area ?: null, 'p_filter' => $filter, 'p_limit' => 30]) ?? [];
    $groups = sb_get('landscape_groups?select=code,name') ?? [];
    $popular = sb_rpc('popular_sequences', ['p_limit' => 12]) ?? [];
} catch (Throwable $e) { $error = $e->getMessage(); $flows = $popular = $groups = []; }
$groupNames = array_column($groups, 'name', 'code');
$_SESSION['flow_cache'] = [];
foreach ($flows as $f) if (!empty($f['route_key'])) $_SESSION['flow_cache'][$f['route_key']] = $f;

$tabs = ['all' => 'おすすめ', 'saved' => '保存した流れ', 'popular' => 'よく出る流れ', 'auto' => '自動生成'];
page_header('おすすめの流れ', 'flows.php');
?>
<section class="card">
  <h1>おすすめの流れ</h1>
  <p class="small muted">「流れで探す」の検索結果（上位3件）を自動で記録しています。よく出る流れほど上に表示され、「☆ おすすめに保存」した流れは最上位に固定されます。</p>
  <nav class="tabs">
    <?php foreach ($tabs as $k => $v): ?>
      <a href="?<?= h(http_build_query(['f' => $k, 'area' => $area])) ?>" class="<?= $filter === $k ? 'on' : '' ?>"><?= $v ?></a>
    <?php endforeach; ?>
  </nav>
  <form method="get" class="row filters">
    <input type="hidden" name="f" value="<?= h($filter) ?>">
    <label>エリア<select name="area" onchange="this.form.submit()"><option value="">すべて</option>
      <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
  </form>
  <?php if ($popular): ?>
    <h2>よく探される景観の流れ</h2>
    <p class="popular">
      <?php foreach ($popular as $p): $seq = $p['seq'] ?? []; ?>
        <a class="chip" href="flow.php?<?= h(http_build_query(['seq' => implode(',', $seq), 'area' => $area])) ?>"><?= h(implode(' → ', array_map(fn($g) => $groupNames[$g] ?? $g, $seq))) ?>（<?= (int)$p['times'] ?>回）</a>
      <?php endforeach; ?>
    </p>
    <p class="small muted">押すと、その流れで新しくルートを組み立てます。</p>
  <?php endif; ?>
</section>

<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
<?php if (!$flows && !$error): ?>
  <p class="card">まだ流れがありません。「流れで探す」で検索すると、ここに蓄積されます。</p>
<?php endif; ?>

<?php foreach ($flows as $i => $f):
    $saved = in_array($f['status'], ['reviewed', 'verified'], true);
    $extra = '<span>検索で' . (int)$f['hit_count'] . '回表示</span>';
    if ($saved && !empty($f['saved_by']) && current_user()) $extra .= '<span>保存：' . h($f['saved_by']) . '</span>';
    if ($f['origin'] === 'auto') $extra = '<span>自動生成</span>';
    if (!empty($f['needs_review']) && current_user()) {
        $extra .= '<form method="post" class="inline">' . csrf_field() . '<input type="hidden" name="action" value="reviewed">'
                . '<input type="hidden" name="id" value="' . (int)$f['id'] . '"><button class="secondary small-btn">再確認済みにする</button></form>';
    }
    render_route_card($f, $i, [
        'saved' => $saved,
        'save_key' => (current_user() && !$saved && $f['origin'] !== 'auto') ? $f['route_key'] : null,
        'archive_id' => (current_user() && $f['origin'] !== 'auto') ? $f['id'] : null,
        'extra' => $extra,
    ]);
endforeach; ?>

<?php leaflet_script(); page_footer();
