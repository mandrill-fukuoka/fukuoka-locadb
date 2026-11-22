"""変化リスク監視：登録した公式ページ等を定期取得し、更新があれば
登録地点名との一致を探して change_risks に「要確認」で登録する

監視対象は config/watch_pages.txt（1行1URL、タブ区切りでラベル可）に記載。
自動で「確認済み」にはしない（一次資料での確認は人が行う）。
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .db import connect, job

# 語は「その地点がなくなる・変わる」とはっきり言えるものだけにする（2026-11-22。変化の材料は OSM のタグの変化・
# Overture の消失に任せ、ここは公式ページの一次の言い回しだけを拾う）。「廃止」（路線・制度の廃止）、「跡地」（なくなった後の話）、
# 「リノベーション」（良い方向の改修）、「都市計画決定」（地区全体の手続き）は地点の変化とは言えず誤検知のもとだったので外した
KEYWORDS = [
    ("demolition", r"解体|取り壊し|除却"),
    ("closure", r"閉店|閉館|閉鎖|営業終了|閉院|廃校|閉校"),
    ("redevelopment", r"再開発|建て替え|建替|新築計画"),
    ("use_change", r"用途変更|移転"),
]
# 橋・道路・川・線路などは、再開発の記事に「経路の目印」として名前が出るだけで反応しやすい（2026-11-17：天神ビッグバンの
# ページに西大橋・天神橋・博多大橋が反応）。建物・施設と違い、記事の語で「なくなる」とは言えないので照合の対象にしない
INFRA_CATEGORIES = ("bridge_overpass", "underpass", "expressway", "railway", "river", "riverbank", "coast",
                    "breakwater", "alley", "slope_street")
CTX = 60            # 根拠として見る範囲：地点名の前後の文字数（同じ文の中だけ）
CONFIG = Path(__file__).resolve().parent.parent / "config" / "watch_pages.txt"


def expand_url(url: str) -> list[str]:
    """{YYYY}/{M} を含むURLは、今月と先月の2件に展開する（月別の報道発表一覧など）"""
    if "{YYYY}" not in url and "{M}" not in url:
        return [url]
    now = datetime.now(timezone(timedelta(hours=9)))
    prev = (now.replace(day=1) - timedelta(days=1))
    return [url.replace("{YYYY}", str(d.year)).replace("{M}", str(d.month)) for d in (now, prev)]


def page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def context(text: str, start: int, end: int) -> str:
    """地点名の前後 CTX 文字。ただし文の区切り（。！？）は越えない（別の話題の語を拾わない）"""
    lo = max(start - CTX, text.rfind("。", 0, start) + 1, text.rfind("！", 0, start) + 1, text.rfind("？", 0, start) + 1)
    stops = [i for i in (text.find(c, end) for c in "。！？") if i != -1]
    hi = min([end + CTX] + stops)
    return text[lo:hi]


def sync_config(conn):
    if not CONFIG.exists():
        return
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, label = line.partition("\t")
        conn.execute("insert into watch_pages (url, label) values (%s,%s) on conflict (url) do nothing",
                     (url, label or None))
    conn.commit()


def run(run_id: int | None = None) -> dict:
    with connect() as conn, job(conn, "monitor_risk", run_id) as stats:
        sync_config(conn)
        # 同じ名前の地点（川の流域の地点など）は1件だけ照合する（同じ根拠で何件も「要確認」にしない）
        names = conn.execute(
            "select distinct on (name) id, name from spots where data_status<>'archived' and char_length(name) >= 4 "
            "and name not like '（無名）%%' and category <> all(%s) order by name, (sample_key is not null), id",
            (list(INFRA_CATEGORIES),)).fetchall()
        changed = found = failed = 0
        for p in conn.execute("select * from watch_pages").fetchall():
            texts, errors = [], []
            for url in expand_url(p["url"]):
                try:
                    r = requests.get(url, timeout=30, headers={"User-Agent": "fukuoka-locadb/2.0"})
                    r.raise_for_status()
                except requests.RequestException as e:
                    print(f"  取得失敗 {url}: {e}")
                    errors.append(f"{url}: {type(e).__name__}")
                    continue
                r.encoding = r.apparent_encoding
                texts.append(page_text(r.text))
            if not texts:
                # ページを取得できなかったことは、施設の閉鎖・消失とは別の情報として記録するだけ
                failed += 1
                conn.execute("update watch_pages set last_checked=now(), last_status='fetch_failed', last_error=%s "
                             "where id=%s", ("; ".join(errors)[:500], p["id"]))
                conn.commit()
                continue
            text = " ".join(texts)
            src_url = expand_url(p["url"])[0]
            h = hashlib.sha256(text.encode()).hexdigest()
            conn.execute("update watch_pages set last_checked=now(), last_hash=%s, last_status='ok', last_error=null "
                         "where id=%s", (h, p["id"]))
            if h == p["last_hash"]:
                continue
            changed += 1
            for s in names:
                for m in re.finditer(re.escape(s["name"]), text):
                    ctx = context(text, m.start(), m.end())
                    rtype = next((t for t, pat in KEYWORDS if re.search(pat, ctx)), None)
                    if not rtype:
                        continue
                    exists = conn.execute(
                        "select 1 from change_risks where spot_id=%s and source_url=%s and risk_type=%s",
                        (s["id"], src_url, rtype)).fetchone()
                    if not exists:
                        conn.execute(
                            """insert into change_risks (spot_id, risk_type, evidence, source_url, fetched_at,
                                                         certainty, verified, review_by)
                               values (%s,%s,%s,%s,now(),'keyword',false, current_date + 14)""",
                            (s["id"], rtype, ctx, src_url))
                        found += 1
                        print(f"  要確認: {s['name']} [{rtype}] ← {src_url}")
                    break
            conn.commit()
        stats.update(pages_changed=changed, risks_flagged=found, pages_failed=failed)
    return stats


if __name__ == "__main__":
    run()
