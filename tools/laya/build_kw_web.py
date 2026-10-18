"""承認済み（採否=採）のWeb候補から knowledge_web.php を作る。読むだけ・書くのは knowledge_web.php だけ。
使い方: python3 build_kw_web.py web_candidates_review.xlsx   （.tsv も可）
材料の文は、引用に出てくる項目名と値をそのまま使う短い文にする（言い換え・補足をしない）。
団地全体の値は「団地全体の」を付けて範囲を明記する。"""
import csv, re, sys, collections, datetime

COLS = ["地点ID", "地点名", "候補No", "種類", "候補の文", "出典URL", "採否（採/不採/保留）", "備考", "既存材料", "取得経路", "原文の引用", "確度", "取得日"]


def read_rows(path):
    if path.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        rows = [[("" if c is None else str(c)) for c in r] for r in load_workbook(path).active.iter_rows(values_only=True)]
    else:
        rows = list(csv.reader(open(path, encoding="utf-8-sig"), delimiter="\t", quotechar=None))
    return [r + [""] * (len(COLS) - len(r)) for r in rows[1:] if r and r[0].strip().isdigit()]


def sentence(attr, value, quote, note):
    m = re.search(r"『([^』]+)』", note)                       # 表の列名
    label = m.group(1) if m else None
    if not label:
        for k in ("業務開始", "創設", "敷地面積", "延床面積", "管理戸数", "戸数", "延長", "階数", "竣工", "開設", "完成"):
            if k in quote:
                label = k
                break
    label = label or {"built": "建設年", "scale": "規模", "owner": "管理者", "status": "現況"}[attr]
    scope = "団地全体の" if "団地全体" in note else ""
    return f"{scope}{label}は{value}。"


def main(path, out="knowledge_web.php"):
    res, names = collections.defaultdict(list), {}
    for r in read_rows(path):
        if r[6].strip() != "採":
            continue
        sid = int(r[0]); names[sid] = r[1]
        value = r[4].split("：", 1)[-1].strip()
        s = sentence(r[3], value, r[10], r[7])
        if s not in res[sid]:
            res[sid].append(s)
    lines = ["<?php", f"// Web（公式ページ）由来の承認済み材料（{datetime.date.today().isoformat()}、人が承認）。地点ID => [材料...]", "return ["]
    for sid in sorted(res):
        lines.append("%d => [%s],  // %s" % (sid, ", ".join("'" + x.replace("\\", "\\\\").replace("'", "\\'") + "'" for x in res[sid]), names[sid]))
    lines.append("];")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(len(res), "地点", sum(len(v) for v in res.values()), "文")


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3]))
