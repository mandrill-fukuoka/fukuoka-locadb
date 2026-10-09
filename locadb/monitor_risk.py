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

KEYWORDS = [
    ("demolition", r"解体|取り壊し|除却"),
    ("closure", r"閉店|閉館|閉鎖|営業終了|閉院|廃校|閉校|廃止"),
    ("redevelopment", r"再開発|建て替え|建替|新築計画|都市計画決定"),
    ("use_change", r"用途変更|移転|跡地|リノベーション"),
]
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


def run() -> dict:
    with connect() as conn, job(conn, "monitor_risk") as stats:
        sync_config(conn)
        names = conn.execute(
            "select id, name from spots where status<>'archived' and char_length(name) >= 4 "
            "and name not like '（無名）%%'").fetchall()
        changed = found = 0
        for p in conn.execute("select * from watch_pages").fetchall():
            texts = []
            for url in expand_url(p["url"]):
                try:
                    r = requests.get(url, timeout=30, headers={"User-Agent": "fukuoka-locadb/0.1"})
                    r.raise_for_status()
                except requests.RequestException as e:
                    print(f"  取得失敗 {url}: {e}")
                    continue
                r.encoding = r.apparent_encoding
                texts.append(page_text(r.text))
            if not texts:
                continue
            text = " ".join(texts)
            src_url = expand_url(p["url"])[0]
            h = hashlib.sha256(text.encode()).hexdigest()
            conn.execute("update watch_pages set last_checked=now(), last_hash=%s where id=%s", (h, p["id"]))
            if h == p["last_hash"]:
                continue
            changed += 1
            for s in names:
                for m in re.finditer(re.escape(s["name"]), text):
                    ctx = text[max(0, m.start() - 120): m.end() + 120]
                    rtype = next((t for t, pat in KEYWORDS if re.search(pat, ctx)), None)
                    if not rtype:
                        continue
                    exists = conn.execute(
                        "select 1 from change_risks where spot_id=%s and source_url=%s and risk_type=%s",
                        (s["id"], src_url, rtype)).fetchone()
                    if not exists:
                        conn.execute(
                            """insert into change_risks (spot_id, risk_type, evidence, source_url, verified, review_by)
                               values (%s,%s,%s,%s,false, current_date + 14)""",
                            (s["id"], rtype, ctx, src_url))
                        found += 1
                        print(f"  要確認: {s['name']} [{rtype}] ← {src_url}")
                    break
            conn.commit()
        stats.update(pages_changed=changed, risks_flagged=found)
    return stats


if __name__ == "__main__":
    run()
