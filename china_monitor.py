import os
import json
import hashlib
import smtplib
import html
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from email.utils import format_datetime

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS = BASE / "docs"
STATE = DATA / "china_seen.json"
FEED = DOCS / "oracle-china-filings.xml"

CNINFO_API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_HOME = "https://www.cninfo.com.cn/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OracleFinanceFilings/1.0; +mailto:officialoraclefinance@gmail.com)",
    "Referer": CNINFO_HOME,
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}

DATA.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)


def fetch_announcements():
    """Fetch recent listed-company announcements from CNINFO's public disclosure search."""
    shanghai_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start = (shanghai_now - timedelta(days=3)).strftime("%Y-%m-%d")
    end = shanghai_now.strftime("%Y-%m-%d")
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []

    # CNINFO's all-market disclosure search includes Shenzhen, Shanghai and Beijing.
    for page in range(1, 6):
        payload = {
            "pageNum": page,
            "pageSize": 100,
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{start}~{end}",
            "sortName": "",
            "sortType": "desc",
            "isHLtitle": "true",
            "source": "",
            "isQuote": "",
        }
        response = session.post(CNINFO_API, data=payload, timeout=40)
        response.raise_for_status()
        data = response.json()
        rows = data.get("announcements") or []
        results.extend(rows)
        total = int(data.get("totalRecordNum") or 0)
        if not rows or page * 100 >= total:
            break

    if not results:
        raise RuntimeError("CNINFO returned no announcement records; check endpoint or query response.")
    return results


def load_seen():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def announcement_item(row):
    code = str(row.get("secCode") or "").strip()
    name = str(row.get("secName") or "").strip()
    title = str(row.get("announcementTitle") or "").strip()
    raw_time = row.get("announcementTime")
    if not code or not title or not raw_time:
        return None

    published = datetime.fromtimestamp(float(raw_time) / 1000, tz=timezone.utc)
    attachment = str(row.get("adjunctUrl") or "").strip()
    link = ("https://static.cninfo.com.cn/" + attachment.lstrip("/")) if attachment else CNINFO_HOME
    market = str(row.get("columnId") or "").strip()
    identity = str(row.get("announcementId") or (code + "|" + title + "|" + str(raw_time)))
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "code": code,
        "name": name,
        "title": title,
        "date": format_datetime(published),
        "link": link,
        "market": market,
    }


def xml_escape(value):
    return html.escape(str(value or ""), quote=True)


def write_feed(items):
    old = []
    if FEED.exists():
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(FEED.read_text(encoding="utf-8"))
            for node in root.findall("./channel/item"):
                old.append({
                    "code": "",
                    "name": "",
                    "title": node.findtext("title", "China filing"),
                    "date": node.findtext("pubDate", ""),
                    "link": node.findtext("link", ""),
                })
        except Exception:
            old = []

    merged, links = [], set()
    for item in items + old:
        link = item.get("link", "")
        if link and link in links:
            continue
        if link:
            links.add(link)
        merged.append(item)
    merged = merged[:500]

    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        '<title>Oracle Finance — China Market Filings</title>',
        '<link>https://www.cninfo.com.cn/</link>',
        '<description>Listed-company announcements from CNINFO covering Shanghai, Shenzhen and Beijing markets.</description>',
        f'<lastBuildDate>{xml_escape(now)}</lastBuildDate>',
    ]
    for item in merged:
        title = f"{item.get('code', '')} | {item.get('name', '')} | {item.get('title', 'China filing')}".strip(" |")
        lines.extend([
            '<item>',
            f'<title>{xml_escape(title)}</title>',
            f'<link>{xml_escape(item.get("link", ""))}</link>',
            f'<description>{xml_escape(item.get("name", ""))}</description>',
            f'<pubDate>{xml_escape(item.get("date", now))}</pubDate>',
            '</item>',
        ])
    lines.append('</channel></rss>')
    FEED.write_text("\n".join(lines), encoding="utf-8")


def send_email(items):
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    recipient = os.environ.get("ALERT_TO", "").strip()
    if not (user and password and recipient):
        print("WARNING: Missing SMTP_USER, SMTP_APP_PASSWORD or ALERT_TO; email skipped.")
        return

    msg = EmailMessage()
    msg["Subject"] = f"[ORACLE FINANCE CHINA FILINGS] {len(items)} new announcement(s)"
    msg["From"] = user
    msg["To"] = recipient
    lines = [
        "ORACLE FINANCE — CHINA MARKET DISCLOSURE ALERTS",
        "Source: CNINFO official disclosure search",
        "",
        f"New announcements: {len(items)}",
        "",
    ]
    for item in items:
        lines.extend([
            f"{item['code']} {item['name']}",
            item["title"],
            item["date"],
            item["link"],
            "",
        ])
    msg.set_content("\n".join(lines))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(user, password)
        server.send_message(msg)
    print(f"China filing email sent to {recipient}.")


def main():
    print("Starting Oracle Finance China filings monitor...")
    rows = fetch_announcements()
    print(f"CNINFO announcements received: {len(rows)}")
    seen = set(load_seen())
    fresh = []
    for row in rows:
        item = announcement_item(row)
        if not item or item["id"] in seen:
            continue
        fresh.append(item)
        seen.add(item["id"])

    # Keep the duplicate history bounded.
    STATE.write_text(json.dumps(list(seen)[-30000:], ensure_ascii=False, indent=2), encoding="utf-8")
    write_feed(fresh)
    if fresh:
        send_email(fresh)
    else:
        print("No new announcements since the previous successful check.")
    print(f"Completed. New announcements: {len(fresh)}. RSS: {FEED}")


if __name__ == "__main__":
    main()
