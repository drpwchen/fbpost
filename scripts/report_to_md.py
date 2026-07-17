"""Convert a Facebook Professional Dashboard content report CSV to a markdown table.

Usage:
    uv run python scripts/report_to_md.py <report.csv> [--enriched <enriched.csv>]

- Report CSV comes from: professional_dashboard > 內容 > 匯出資料 > 建立資料報告.
  IMPORTANT when creating the report: un-check the 收益 metrics, otherwise all
  columns after them are silently dropped from the export.
- FB stamps 發佈時間 in US Pacific time; we convert to Taipei by adding 15 hours.
  (Pacific is UTC-7/-8; +15h matches PDT which covers Mar-Nov posting. Good enough
  for a tracking table; do not use for minute-level analysis.)
- --enriched: optional older analysis CSV carrying a 主題 column, joined by 貼文編號.
"""
import argparse
import csv
import sys
from datetime import datetime, timedelta

TAIPEI_OFFSET = timedelta(hours=15)


def first_line(title: str, maxlen: int = 28) -> str:
    line = title.strip().splitlines()[0] if title.strip() else "(no title)"
    line = line.replace("|", "\\|")
    return line[:maxlen] + ("…" if len(line) > maxlen else "")


def to_taipei(pacific_str: str) -> str:
    try:
        dt = datetime.strptime(pacific_str.strip(), "%m/%d/%Y %H:%M")
        return (dt + TAIPEI_OFFSET).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return pacific_str


def num(row: dict, key: str) -> str:
    v = (row.get(key) or "").strip()
    return v if v not in ("", "--") else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report_csv")
    ap.add_argument("--enriched", help="older enriched CSV with 主題 column, joined on 貼文編號")
    args = ap.parse_args()

    topics = {}
    if args.enriched:
        with open(args.enriched, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("主題"):
                    topics[r["貼文編號"]] = r["主題"]

    with open(args.report_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        r["_taipei"] = to_taipei(r.get("發佈時間", ""))
    rows.sort(key=lambda r: r["_taipei"], reverse=True)

    print("| 發佈(台北) | 標題 | 主題 | 觀看 | 瀏覽人數 | 互動 | 心情 | 留言 | 儲存 | 分享 | 連結 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        pid = r.get("貼文編號", "")
        url = (r.get("永久連結") or "").strip()
        link = f"[🔗]({url})" if url else "-"
        print(
            f"| {r['_taipei']} | {first_line(r.get('標題', ''))} | {topics.get(pid, '')} "
            f"| {num(r, '觀看次數')} | {num(r, '瀏覽人數')} | {num(r, '互動次數')} "
            f"| {num(r, '心情數')} | {num(r, '留言')} | {num(r, '儲存次數')} "
            f"| {num(r, '分享')} | {link} |"
        )
    print(f"\n{len(rows)} posts · exported {datetime.now().strftime('%Y-%m-%d')} · 時區已轉台北(+15h)", file=sys.stderr)


if __name__ == "__main__":
    main()
