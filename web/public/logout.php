<?php
require __DIR__ . '/bootstrap.php';
$_SESSION = [];
session_destroy();
header('Location: login.php');
