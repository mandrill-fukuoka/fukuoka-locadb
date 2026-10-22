<?php
// 地点の確認（ログイン必須）：要確認・未確認・対象外・重複の疑い・説明文の下書き・自動チェック・事実カード・写真候補
require __DIR__ . '/bootstrap.php';
require_login();

$queue  = $_GET['queue'] ?? 'review';
if (!in_array($queue, ['review', 'unreviewed', 'manual', 'drafts', 'checks', 'facts', 'photos', 'archived', 'all', 'dups'], true)) $queue = 'review';
$area   = $_GET['area'] ?? '';
$status = $_GET['status'] ?? '';
$q      = trim($_GET['q'] ?? '');
$target = $_GET['target'] ?? '';                       // 自動チェック：category / address / summary
if (!in_array($target, ['', 'category', 'address', 'summary'], true)) $target = '';
$check  = preg_replace('/[^a-z_]/', '', (string)($_GET['check'] ?? ''));
$item   = $_GET['item'] ?? '';                       // 事実カード：項目
if (!in_array($item, FACT_ITEMS, true)) $item = '';
$flag   = $_GET['flag'] ?? '';                         // 説明文の下書き：指摘あり（1）・なし（0）
if (!in_array($flag, ['', '0', '1'], true)) $flag = '';
$page   = max(0, (int)($_GET['p'] ?? 0));
$per    = 30;
$msg = ''; $error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    try {
        if (($_POST['action'] ?? '') === 'check_close') {
            // 自動チェックの指摘を「問題なし」で閉じる（同じ指摘は次の実行でも開き直さない）
            sb_rpc('check_close', ['p_id' => (int)$_POST['check_id'], 'p_user' => current_user(),
                                   'p_note' => trim((string)($_POST['note'] ?? '')) ?: null]);
            $msg = '「' . ($_POST['name'] ?? '') . '」の指摘を閉じました';
        } elseif (($_POST['action'] ?? '') === 'photo_decide' && in_array($_POST['decision'] ?? '', ['adopt', 'reject', 'reset'], true)) {
            // 写真候補の採否（採用すると出典つきで地点ページに出る。決めるのは人だけ）
            $r = sb_rpc('photo_decide', ['p_id' => (int)$_POST['cand_id'], 'p_decision' => $_POST['decision'], 'p_user' => current_user()]);
            if (!empty($r['ok'])) {
                $msg = '「' . ($_POST['name'] ?? '') . '」の写真を' . ['adopt' => '採用しました（地点ページに出ます）', 'reject' => '不採用にしました', 'reset' => '候補に戻しました'][$_POST['decision']];
            } else { $error = $r['reason'] ?? '保存できませんでした'; }
        } elseif (($_POST['action'] ?? '') === 'fact_revoke') {
            // 事実カードの取り消し（行は残る。公開の画面から消える）
            sb_rpc('fact_revoke', ['p_id' => (int)$_POST['fact_id'], 'p_user' => current_user(),
                                   'p_note' => trim((string)($_POST['note'] ?? '')) ?: null]);
            $msg = '「' . ($_POST['name'] ?? '') . '」の事実を取り消しました（公開の画面から消えます）';
        } elseif (in_array($_POST['action'] ?? '', ['ai_accept', 'ai_reject'], true)) {
            // AIの説明文の下書き：採用（そのまま／直して）・却下
            sb_rpc('ai_review', ['p_note_id' => (int)$_POST['note_id'], 'p_action' => $_POST['action'] === 'ai_accept' ? 'accept' : 'reject',
                                 'p_user' => current_user(), 'p_text' => trim((string)($_POST['text'] ?? '')) ?: null]);
            $msg = '「' . ($_POST['name'] ?? '') . '」の下書きを' . ($_POST['action'] === 'ai_accept' ? '採用しました（公開されます）' : '却下しました');
        } elseif (($_POST['action'] ?? '') === 'merge') {
            sb_rpc('merge_spots', ['p_keep' => (int)$_POST['keep'], 'p_drop' => (int)$_POST['drop'], 'p_user' => current_user()]);
            $msg = '2つの地点を1つにまとめました';
        } elseif (in_array($_POST['action'] ?? '', ['archive', 'restore'], true)) {
            sb_rpc('set_spot_state', ['p_id' => (int)$_POST['id'], 'p_action' => $_POST['action'], 'p_user' => current_user(),
                                      'p_note' => trim($_POST['note'] ?? '') ?: null]);
            $msg = '「' . ($_POST['name'] ?? '') . '」を' . ($_POST['action'] === 'archive' ? '対象外にしました' : '掲載に戻しました');
        } elseif (!isset($_POST['id'])) {
            throw new RuntimeException('送信内容が正しくありません（操作を特定できません）');
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

$rows = []; $dups = []; $tags = []; $drafts = []; $checks = []; $ccount = ['items' => [], 'last_run' => null];
$facts = []; $fcount = ['items' => [], 'spots' => 0];
$photos = []; $pcount = ['candidate' => 0, 'adopted' => 0, 'rejected' => 0, 'spots' => 0];
$pstatus = in_array($_GET['pstatus'] ?? '', ['candidate', 'adopted', 'rejected'], true) ? $_GET['pstatus'] : 'candidate';
try {
    if ($queue === 'drafts') {
        $drafts = sb_rpc('ai_drafts', ['p_area' => $area ?: null, 'p_q' => $q ?: null,
                                       'p_offset' => $page * $per, 'p_limit' => $per,
                                       'p_flagged' => $flag === '' ? null : $flag === '1']) ?? [];
    } elseif ($queue === 'checks') {
        $ccount = sb_rpc('checks_counts') ?? $ccount;
        $checks = sb_rpc('checks_list', ['p_target' => $target ?: null, 'p_check' => $check ?: null, 'p_area' => $area ?: null,
                                         'p_q' => $q ?: null, 'p_offset' => $page * $per, 'p_limit' => $per]) ?? [];
    } elseif ($queue === 'facts') {
        $fcount = sb_rpc('facts_counts') ?? $fcount;
        $facts = sb_rpc('facts_list', ['p_item' => $item ?: null, 'p_area' => $area ?: null, 'p_q' => $q ?: null,
                                       'p_offset' => $page * $per, 'p_limit' => $per]) ?? [];
    } elseif ($queue === 'photos') {
        $pcount = sb_rpc('photos_counts') ?? $pcount;
        $photos = sb_rpc('photo_candidates_list', ['p_status' => $pstatus, 'p_offset' => $page * $per, 'p_limit' => $per]) ?? [];
    } elseif ($queue === 'dups') {
        $dups = sb_rpc('duplicate_candidates', ['p_limit' => 100, 'p_meters' => 150]) ?? [];
    } else {
        $tags = sb_get('tags?select=code,name,facet&order=facet,code') ?? [];
        $rows = sb_rpc('review_queue', [
            'p_area' => $area ?: null, 'p_status' => $status ?: null, 'p_q' => $q ?: null,
            'p_offset' => $page * $per, 'p_limit' => $per, 'p_queue' => $queue,
        ]) ?? [];
    }
} catch (Throwable $e) { $error = $e->getMessage(); }

$qs = fn(array $o) => '?' . http_build_query(array_merge(['queue' => $queue, 'area' => $area, 'status' => $status, 'q' => $q,
                                                          'target' => $target, 'check' => $check, 'flag' => $flag, 'item' => $item, 'pstatus' => $pstatus], $o));
$tabs = ['review' => '要確認', 'unreviewed' => '未確認', 'manual' => '手入力・人が直した', 'drafts' => '説明文の下書き',
         'checks' => '自動チェック', 'facts' => '事実カード', 'photos' => '写真候補', 'dups' => '重複の疑い', 'archived' => '対象外', 'all' => 'すべて'];
$TARGET_LABEL = ['category' => '種別', 'address' => '住所', 'summary' => '説明文'];
page_header('地点の確認', 'spots.php');
?>
<section class="card">
  <h1>地点の確認</h1>
  <p class="small muted">「要確認」は、人が確認した地点でOSMの位置・名前・種別が変わったもの、OSMから見つからなくなったものです。自動では変更せず、ここで判断します。</p>
  <nav class="tabs">
    <?php foreach ($tabs as $k => $v): ?><a href="<?= h($qs(['queue' => $k, 'p' => 0, 'target' => '', 'check' => '', 'flag' => '', 'item' => ''])) ?>" class="<?= $queue === $k ? 'on' : '' ?>"><?= $v ?></a><?php endforeach; ?>
  </nav>
  <?php if ($queue !== 'dups'): ?>
  <form class="row filters" method="get">
    <input type="hidden" name="queue" value="<?= h($queue) ?>">
    <label>エリア<select name="area"><option value="">すべて</option>
      <?php foreach (AREA_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $area === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <?php if ($queue === 'checks'): ?>
    <label>対象<select name="target"><option value="">すべて</option>
      <?php foreach ($TARGET_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $target === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <input type="hidden" name="check" value="<?= h($check) ?>">
    <?php elseif ($queue === 'photos'): ?>
  <section class="card">
    <p class="small muted">Wikimedia Commons と Mapillary（file名が mapillary: のもの＝街路写真）の写真を、地点ごとの候補として自動で集めたものです（AIは使っていません）。「採用」を押した写真だけが、作者・ライセンス・出典つきで地点ページと一覧に出ます。
      写っているものが地点と合っているか、人の顔・商標・私有地など配慮が要るものでないかを見て決めてください。候補は CC0・パブリックドメイン・CC BY・CC BY-SA のものだけです。</p>
    <nav class="tabs">
      <?php foreach (['candidate' => '候補', 'adopted' => '採用済み', 'rejected' => '不採用'] as $k => $v): ?>
        <a href="<?= h($qs(['pstatus' => $k, 'p' => 0])) ?>" class="<?= $pstatus === $k ? 'on' : '' ?>"><?= $v ?>（<?= number_format((int)($pcount[$k] ?? 0)) ?>）</a>
      <?php endforeach; ?>
    </nav>
    <p class="small muted">候補のある地点：<?= number_format((int)$pcount['spots']) ?>か所</p>
  </section>
  <?php foreach ($photos as $c): ?>
    <article class="card check photocand">
      <a href="<?= h($c['page_url']) ?>" target="_blank" rel="noopener"><img src="<?= h($c['image_url']) ?>" alt="<?= h($c['file_title']) ?>" loading="lazy" referrerpolicy="no-referrer"></a>
      <div class="body">
        <h3><a href="spot.php?id=<?= (int)$c['spot_id'] ?>"><?= h(nm($c['name'])) ?></a>
          <span class="muted small"><?= h($c['category']) ?>・<?= h($c['address'] ?: '') ?>・<?= h($c['code']) ?></span></h3>
        <p class="small"><?= h($c['file_title']) ?>
          <span class="chip dim">地点から <?= (int)$c['dist_m'] ?>m</span>
          <?php if (!empty($c['name_match'])): ?><span class="chip">名前が一致</span><?php endif; ?></p>
        <?php if (!empty($c['description'])): ?><p class="small muted"><?= h($c['description']) ?></p><?php endif; ?>
        <p class="small muted"><?= photo_credit_html(['author' => $c['author'], 'license' => $c['license'], 'license_url' => $c['license_url'], 'source_url' => $c['page_url']]) ?>
          <?php if (!empty($c['shot_at'])): ?>・撮影：<?= h($c['shot_at']) ?><?php endif; ?>
          <?php if ($c['status'] !== 'candidate'): ?>・<?= h($c['decided_by']) ?> <?= h(substr((string)$c['decided_at'], 0, 10)) ?><?php endif; ?></p>
        <form method="post" action="<?= h($qs(['p' => $page])) ?>" class="row"><?= csrf_field() ?>
          <input type="hidden" name="action" value="photo_decide"><input type="hidden" name="cand_id" value="<?= (int)$c['id'] ?>"><input type="hidden" name="name" value="<?= h(nm($c['name'])) ?>">
          <?php if ($c['status'] === 'candidate'): ?>
            <button name="decision" value="adopt" class="small-btn">採用（公開）</button>
            <button name="decision" value="reject" class="secondary small-btn">不採用</button>
          <?php else: ?>
            <button name="decision" value="reset" class="secondary small-btn"><?= $c['status'] === 'adopted' ? '採用を取り消す（候補に戻す）' : '候補に戻す' ?></button>
          <?php endif; ?>
        </form>
      </div>
    </article>
  <?php endforeach; ?>
  <?php if (!$photos && !$error): ?><p class="card">該当する写真はありません。</p><?php endif; ?>
  <nav class="pager">
    <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
    <?php if (count($photos) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
  </nav>
<?php elseif ($queue === 'facts'): ?>
    <label>項目<select name="item"><option value="">すべて</option>
      <?php foreach (FACT_ITEMS as $v): ?><option value="<?= h($v) ?>" <?= $item === $v ? 'selected' : '' ?>><?= h($v) ?></option><?php endforeach; ?></select></label>
    <?php elseif ($queue === 'drafts'): ?>
    <label>自動チェック<select name="flag"><option value="">すべて（指摘のあるものが先）</option>
      <option value="1" <?= $flag === '1' ? 'selected' : '' ?>>指摘あり</option><option value="0" <?= $flag === '0' ? 'selected' : '' ?>>指摘なし</option></select></label>
    <?php else: ?>
    <label>確認の状態<select name="status"><option value="">すべて</option>
      <?php foreach (STATUS_LABEL as $k => $v): ?><option value="<?= $k ?>" <?= $status === $k ? 'selected' : '' ?>><?= $v ?></option><?php endforeach; ?></select></label>
    <?php endif; ?>
    <label>名称・住所<input name="q" value="<?= h($q) ?>" placeholder="部分一致"></label>
    <button>絞り込み</button>
  </form>
  <?php endif; ?>
</section>

<?php if ($msg): ?><p class="ok"><?= h($msg) ?></p><?php endif; ?>
<?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>

<?php if ($queue === 'drafts'): ?>
  <p class="small muted">AI（LAYA など）が作った説明文の下書きです。採用すると、その地点の説明文としてログインなしの画面にも出ます。直してから採用することもできます。却下した下書きは使われません。</p>
  <?php if (!$drafts && !$error): ?><p class="card">採用待ちの下書きはありません。</p><?php endif; ?>
  <?php foreach ($drafts as $d): ?>
    <article class="card draft">
      <h3><a href="spot.php?id=<?= (int)$d['spot_id'] ?>"><?= h(nm($d['name'])) ?></a>
        <span class="muted small"><?= h($d['category']) ?>・<?= h($d['address'] ?: (AREA_LABEL[$d['area_code']] ?? '')) ?>・<?= h($d['code']) ?></span></h3>
      <?php if (!empty($d['checks'])): ?><p class="checks"><?php foreach ($d['checks'] as $c): ?>
        <span class="chip <?= $c['severity'] === 'warn' ? 'warn' : 'dim' ?>" title="<?= h($c['detail']) ?>"><?= h($c['label']) ?>：<?= h(mb_strimwidth($c['detail'], 0, 60, '…')) ?></span>
      <?php endforeach; ?></p><?php endif; ?>
      <?php if (!empty($d['current_summary'])): ?><p class="small">いま公開中の説明文：<?= h($d['current_summary']) ?></p><?php endif; ?>
      <form method="post" action="<?= h($qs(['p' => $page])) ?>"><?= csrf_field() ?>
        <input type="hidden" name="note_id" value="<?= (int)$d['id'] ?>"><input type="hidden" name="name" value="<?= h(nm($d['name'])) ?>">
        <p class="small muted">下書き（<?= h(AI_SOURCE_LABEL[$d['source']] ?? $d['source']) ?><?= $d['model'] ? '・' . h($d['model']) : '' ?>・<?= h(substr((string)$d['created_at'], 0, 10)) ?>）</p>
        <textarea name="text" rows="3"><?= h($d['body']) ?></textarea>
        <p class="actions"><button name="action" value="ai_accept" class="small-btn">採用する（直した内容で）</button>
          <button name="action" value="ai_reject" class="secondary small-btn">却下</button></p>
      </form>
    </article>
  <?php endforeach; ?>
  <nav class="pager">
    <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
    <?php if (count($drafts) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
  </nav>
<?php elseif ($queue === 'checks'): ?>
  <section class="card">
    <p class="small muted">種別・住所・説明文の下書きを機械的に照合した結果です（毎週、自動収集の後に更新。AIは使っていません）。地点や下書きは自動では変えません。
      直すときは地点ページ・「説明文の下書き」タブで直してください。直ったものは次の実行で自動的に消えます。誤りでなければ「問題なし」で閉じると、次から出ません。
      <?php if (!empty($ccount['last_run'])): ?>最後の確認：<?= h(str_replace("T", " ", substr((string)$ccount["last_run"], 0, 16))) ?><?php endif; ?></p>
    <?php if (!$ccount['items']): ?><p>未対応の指摘はありません。</p><?php else: ?>
    <table class="checks-summary"><tr><th>対象</th><th>チェック</th><th class="cnt">件数</th></tr>
      <?php foreach ($ccount['items'] as $it): ?>
      <tr class="<?= $check === $it['check_code'] ? 'on' : '' ?>"><td><?= h($TARGET_LABEL[$it['target']] ?? $it['target']) ?></td>
        <td><a href="<?= h($qs(['check' => $it['check_code'], 'target' => '', 'p' => 0])) ?>"><?= h($it['label']) ?></a>
          <?php if ($it['severity'] === 'warn'): ?><span class="chip warn">要確認</span><?php else: ?><span class="chip dim">参考</span><?php endif; ?></td>
        <td class="cnt"><?= number_format((int)$it['n']) ?></td></tr>
      <?php endforeach; ?></table>
    <?php if ($check): ?><p class="small"><a href="<?= h($qs(['check' => '', 'p' => 0])) ?>">すべてのチェックを表示</a></p><?php endif; ?>
    <?php endif; ?>
  </section>
  <?php foreach ($checks as $c): ?>
    <article class="card check">
      <h3><a href="spot.php?id=<?= (int)$c['spot_id'] ?>"><?= h(nm($c['name'])) ?></a>
        <span class="muted small"><?= h($c['category']) ?>・<?= h($c['address'] ?: (AREA_LABEL[$c['area_code']] ?? '')) ?>・<?= h($c['code']) ?></span></h3>
      <p><span class="chip <?= $c['severity'] === 'warn' ? 'warn' : 'dim' ?>"><?= h($TARGET_LABEL[$c['target']] ?? '') ?>：<?= h($c['label']) ?></span> <?= h($c['detail']) ?></p>
      <?php if (!empty($c['note_body'])): ?><p class="small muted">下書き：<?= h($c['note_body']) ?>…（<a href="spot.php?id=<?= (int)$c['spot_id'] ?>">地点ページで下書きを直す・却下する</a>）</p><?php endif; ?>
      <form method="post" action="<?= h($qs(['p' => $page])) ?>" class="row"><?= csrf_field() ?>
        <input type="hidden" name="check_id" value="<?= (int)$c['id'] ?>"><input type="hidden" name="name" value="<?= h(nm($c['name'])) ?>">
        <input name="note" placeholder="メモ（任意）"><button name="action" value="check_close" class="secondary small-btn">問題なし（閉じる）</button>
        <a class="small" href="edit.php?id=<?= (int)$c['spot_id'] ?>">位置・名前・種別を直す</a>
      </form>
    </article>
  <?php endforeach; ?>
  <?php if (!$checks && !$error && $ccount['items']): ?><p class="card">該当する指摘はありません。</p><?php endif; ?>
  <nav class="pager">
    <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
    <?php if (count($checks) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
  </nav>
<?php elseif ($queue === 'facts'): ?>
  <section class="card">
    <p class="small muted">人が承認した事実（項目・値・出典）です。ここにあるものは、ログインなしの地点ページにも出ます。誤りが見つかったら「取り消す」と、公開の画面から消えます（行は残ります）。
      追加は、承認済みの一覧を data/facts に入れて facts-import で取り込みます。撮影の可否は書きません。</p>
    <?php if (!$fcount['items']): ?><p>承認済みの事実はまだありません。</p><?php else: ?>
    <table class="checks-summary"><tr><th>項目</th><th class="cnt">件数</th><th class="cnt">地点数</th></tr>
      <?php foreach ($fcount['items'] as $it): ?>
      <tr class="<?= $item === $it['item'] ? 'on' : '' ?>"><td><a href="<?= h($qs(['item' => $it['item'], 'p' => 0])) ?>"><?= h($it['item']) ?></a></td>
        <td class="cnt"><?= number_format((int)$it['n']) ?></td><td class="cnt"><?= number_format((int)$it['spots']) ?></td></tr>
      <?php endforeach; ?>
      <tr><td>合計（地点）</td><td class="cnt"></td><td class="cnt"><?= number_format((int)$fcount['spots']) ?></td></tr></table>
    <?php if ($item): ?><p class="small"><a href="<?= h($qs(['item' => '', 'p' => 0])) ?>">すべての項目を表示</a></p><?php endif; ?>
    <?php endif; ?>
  </section>
  <?php foreach ($facts as $f): ?>
    <article class="card check">
      <h3><a href="spot.php?id=<?= (int)$f['spot_id'] ?>"><?= h(nm($f['name'])) ?></a>
        <span class="muted small"><?= h($f['category']) ?>・<?= h($f['address'] ?: (AREA_LABEL[$f['area_code']] ?? '')) ?>・<?= h($f['code']) ?></span></h3>
      <p><span class="chip"><?= h($f['item']) ?></span> <strong><?= h($f['value']) ?></strong>
        <?php if (!empty($f['as_of'])): ?><span class="muted small">（<?= h($f['as_of']) ?>）</span><?php endif; ?>
        <?php if (!empty($f['note'])): ?><span class="muted small">・<?= h($f['note']) ?></span><?php endif; ?></p>
      <p class="small muted">出典：<?php if (!empty($f['source_url'])): ?><a href="<?= h($f['source_url']) ?>" target="_blank" rel="noopener"><?= h($f['source_title'] ?: (FACT_SOURCE_LABEL[$f['source_kind']] ?? $f['source_kind'])) ?></a><?php else: ?><?= h(FACT_SOURCE_LABEL[$f['source_kind']] ?? $f['source_kind']) ?><?php endif; ?>
        <?php if (!empty($f['license'])): ?>（<?= h($f['license']) ?>）<?php endif; ?>・承認：<?= h($f['approved_by']) ?> <?= h(substr((string)$f['approved_at'], 0, 10)) ?></p>
      <?php if (!empty($f['quote'])): ?><p class="small muted">根拠の原文：<?= h($f['quote']) ?></p><?php endif; ?>
      <form method="post" action="<?= h($qs(['p' => $page])) ?>" class="row"><?= csrf_field() ?>
        <input type="hidden" name="fact_id" value="<?= (int)$f['id'] ?>"><input type="hidden" name="name" value="<?= h(nm($f['name'])) ?>">
        <input name="note" placeholder="取り消す理由（任意）"><button name="action" value="fact_revoke" class="secondary small-btn" onclick="return confirm('この事実を取り消しますか？（公開の画面から消えます）')">取り消す</button>
      </form>
    </article>
  <?php endforeach; ?>
  <?php if (!$facts && !$error && $fcount['items']): ?><p class="card">該当する事実はありません。</p><?php endif; ?>
  <nav class="pager">
    <?php if ($page > 0): ?><a href="<?= h($qs(['p' => $page - 1])) ?>">← 前へ</a><?php endif; ?>
    <?php if (count($facts) === $per): ?><a href="<?= h($qs(['p' => $page + 1])) ?>">次へ →</a><?php endif; ?>
  </nav>
<?php elseif ($queue === 'dups'): ?>
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
<script src="assets/lightbox.js"></script>
<?php page_footer();
