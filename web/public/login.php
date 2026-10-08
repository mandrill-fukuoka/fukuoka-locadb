<?php
require __DIR__ . '/bootstrap.php';
$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    check_csrf();
    if (try_login(trim($_POST['user'] ?? ''), $_POST['pass'] ?? '')) { header('Location: index.php'); exit; }
    $error = 'ユーザー名またはパスワードが違います';
}
page_header('ログイン');
?>
<form method="post" class="card narrow">
  <h1>ログイン</h1>
  <?= csrf_field() ?>
  <?php if ($error): ?><p class="error"><?= h($error) ?></p><?php endif; ?>
  <label>ユーザー名<input name="user" autocomplete="username" required></label>
  <label>パスワード<input name="pass" type="password" autocomplete="current-password" required></label>
  <button>ログイン</button>
</form>
<?php page_footer();
