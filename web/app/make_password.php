<?php
// 使い方: php make_password.php  → パスワードを入力するとハッシュを表示
echo "パスワード: ";
$p = trim((string)fgets(STDIN));
if (strlen($p) < 10) exit("10文字以上にしてください\n");
echo password_hash($p, PASSWORD_DEFAULT), "\n";
