<?php
// 旧URL（places.php）→ トップ（場所を探す）へ
$qs = $_SERVER['QUERY_STRING'] ?? '';
header('Location: index.php' . ($qs !== '' ? '?' . $qs : ''), true, 301);
exit;
