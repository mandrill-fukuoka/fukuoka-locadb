"""道路使用許可の窓口の目安：区から所轄の警察署を出す（DBなし。php-cli で web/app/lib.php を読む。順7・相談先）"""
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

SCRIPT = r"""<?php
$_SERVER['SCRIPT_NAME'] = '/x/index.php'; $_SERVER['REQUEST_METHOD'] = 'GET';
require __DIR__ . '/lib.php';
echo json_encode(['sawara' => police_hint('fuk-sawara'), 'hakata' => police_hint('fuk-hakata'), 'none' => police_hint(null),
                  'unknown' => police_hint('x'), 'areas' => array_keys(POLICE_BY_AREA), 'labels' => array_keys(AREA_LABEL)], JSON_UNESCAPED_UNICODE);
"""


def run():
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        for f in ("lib.php", "scene.php"):
            shutil.copy(ROOT / "web/app" / f, d / f)
        (d / "config.php").write_text("<?php return ['base_url' => 'https://example.test/locadb/'];")
        (d / "t.php").write_text(SCRIPT)
        r = subprocess.run(["php", str(d / "t.php")], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)
    finally:
        shutil.rmtree(d)


def test_police_hint_names_the_station_of_the_ward():
    o = run()
    assert "早良区" in o["sawara"] and "早良警察署" in o["sawara"] and "博多臨港警察署" in o["sawara"]
    assert "博多警察署" in o["hakata"]
    assert o["none"] == "" and o["unknown"] == ""              # 区が分からなければ何も足さない
    assert sorted(o["areas"]) == sorted(o["labels"])           # 7区すべてに署がある
