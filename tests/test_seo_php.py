"""検索エンジンに載せる：ページの robots・description・canonical（DBなし。php-cli で web/app/lib.php を読む。追補1）"""
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

SCRIPT = r"""<?php
$_SERVER['SCRIPT_NAME'] = '/x/index.php'; $_SERVER['REQUEST_METHOD'] = 'GET';
require __DIR__ . '/lib.php';
$a = ['area_code' => 'fuk-chuo', 'category_name' => '神社', 'address' => '福岡市中央区天神1-1', 'summary' => "朝の参道が静かな\n神社。"];
$out = ['desc' => seo_description($a, [['item' => '創設・開設年', 'value' => '1600年']]),
        'long' => mb_strlen(seo_description(['area_code' => 'x', 'category_name' => '海岸', 'summary' => str_repeat('あ', 300)]))];
foreach ([['index' => true, 'canonical' => 'spot.php?id=3', 'description' => 'd'], [], ['index' => false]] as $i => $m) {
    ob_start(); page_header('t', '', $m); $h = ob_get_clean();
    preg_match('/name="robots" content="([^"]+)"/', $h, $r);
    $out["h$i"] = [$r[1], strpos($h, 'rel="canonical" href="https://example.test/locadb/spot.php?id=3"') !== false];
}
echo json_encode($out, JSON_UNESCAPED_UNICODE);
"""


def run(allow):
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        for f in ("lib.php", "scene.php"):
            shutil.copy(ROOT / "web/app" / f, d / f)
        (d / "config.php").write_text(f"<?php return ['allow_indexing' => {'true' if allow else 'false'}, 'base_url' => 'https://example.test/locadb/'];")
        (d / "t.php").write_text(SCRIPT)
        r = subprocess.run(["php", str(d / "t.php")], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d)


def test_indexing_off_means_noindex_everywhere():
    o = run(False)
    assert [o[k][0] for k in ("h0", "h1", "h2")] == ["noindex,nofollow"] * 3


def test_indexing_on_only_for_pages_that_ask():
    o = run(True)
    assert o["h0"] == ["index,follow", True]                       # 求めたページだけ載せる。canonical は base_url から作る
    assert o["h1"][0] == "noindex,nofollow" and o["h2"][0] == "noindex,nofollow"   # 何も指定しないページ（流れ・ロケハンリスト・ログインなど）は載せない


def test_description_is_built_from_facts_and_trimmed():
    o = run(True)
    assert o["desc"] == "福岡市中央区の神社。福岡市中央区天神1-1。朝の参道が静かな 神社。創設・開設年：1600年。"
    assert o["long"] <= 140
