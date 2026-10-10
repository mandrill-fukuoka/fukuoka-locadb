<?php
// パスワードハッシュ作成用（一時ファイル）。ハッシュを config.php に貼ったら、このファイルは必ず削除してください。
header('X-Robots-Tag: noindex');
$hash = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST' && strlen($_POST['p'] ?? '') >= 10) {
    $hash = password_hash($_POST['p'], PASSWORD_DEFAULT);
}
?><!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>パスワードハッシュ作成</title>
<body style="font-family:sans-serif;max-width:520px;margin:40px auto;padding:0 16px">
<h1 style="font-size:18px">ログイン用パスワードのハッシュ作成</h1>
<p>10文字以上のパスワードを入力してください。パスワードはどこにも保存されません。</p>
<form method="post"><input type="password" name="p" minlength="10" required style="width:100%;padding:8px">
<p><button>作成</button></p></form>
<?php if ($hash): ?>
<p>次の文字列を config.php の users に貼り付けてください：</p>
<textarea readonly style="width:100%;height:70px"><?= htmlspecialchars($hash) ?></textarea>
<p style="color:#b5541c;font-weight:bold">貼り付けが終わったら、このファイル（make_hash.php）をサーバーから削除してください。</p>
<?php endif; ?>
