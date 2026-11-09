import io

import openpyxl

from locadb import bodik_probe as bp


def test_profile_rows_counts_filled_and_samples():
    p = bp.profile_rows([["名称", "開設年"], ["A公園", "1980"], ["B公園", None], [None, None]])
    assert p["rows"] == 2
    assert p["columns"][0] == {"name": "名称", "filled": 2, "sample": ["A公園", "B公園"]}
    assert p["columns"][1]["filled"] == 1


def test_probe_xlsx_reads_sheets():
    wb = openpyxl.Workbook()
    wb.active.title = "公園"
    wb.active.append(["名称", "面積"])
    wb.active.append(["A公園", 1.5])
    buf = io.BytesIO()
    wb.save(buf)
    out = bp.probe_xlsx(buf.getvalue())
    assert out["公園"]["rows"] == 1
    assert out["公園"]["columns"][1]["sample"] == ["1.5"]
