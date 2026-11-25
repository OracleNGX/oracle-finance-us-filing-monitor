import os
import json
import hashlib
import smtplib
import html
from pathlib import Path
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS = BASE / "docs"
STATE = DATA / "ngx_seen.json"
FEED = DOCS / "oracle-ngx-filings.xml"
SOURCE = "https://ngxwebsite-dc69c46e9b-hxcxhmabe6aubmg9.a03.azurefd.net/exchange/data/corporate-disclosures/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OracleFinanceNGXMonitor/1.0; +mailto:officialoraclefinance@gmail.com)",
    "Accept": "text/html,application/xhtml+xml",
}

DATA.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)


def fetch_disclosures():
    response = requests.get(SOURCE, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    records = []
    # Find tables using their header labels instead of brittle table state parsing.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [cell.get_text(" ", strip=True).lower() for cell in rows[0].find_all(["th", "td"])]
        if not headers:
            continue
        company_idx = next((i for i, h in enumerate(headers) if "company" in h), None)
        disclosure_idx = next((i for i, h in enumerate(headers) if "disclosure" in h), None)
        date_idx = next((i for i, h in enumerate(headers) if "date" in h and ("submitted" in h or "date" in h)), None)
        if company_idx is None or disclosure_idx is None or date_idx is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if max(company_idx, disclosure_idx, date_idx) >= len(cells):
                continue
            company_cell = cells[company_idx]
            disclosure_cell = cells[disclosure_idx]
            date_cell = cells[date_idx]
            company = company_cell.get_text(" ", strip=True)
            title = disclosure_cell.get_text(" ", strip=True)
            submitted = date_cell.get_text(" ", strip=True)
            anchor = disclosure_cell.find("a", href=True) or company_cell.find("a", href=True)
            if not anchor:
                anchor = row.find("a", href=True)
            link = anchor["href"] if anchor else ""
            if not company or not title or not submitted or not link:
                continue
            records.append({
                "company": company,
                "title": title,
                "submitted": submitted,
                "link": urljoin(SOURCE, link),
            })

    if not records:
        # Fallback: try common NGX disclosure cards/links with row text and document links.
        for anchor in soup.find_all("a", href=True):
            text = anchor.get_text(" ", strip=True)
            href = anchor["href"]
            if not text or not (".pdf" in href.lower() or "document" in href.lower()):
                continue
            parent = anchor.parent
            block = parent.get_text(" ", strip=True) if parent else text
            if len(block) < 15:
                continue
            records.append({
                "company": "NGX disclosure",
                "title": block[:500],
                "submitted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "link": urljoin(SOURCE, href),
            })

    if not records:
        raise RuntimeError("No disclosure records found in NGX page HTML. Page structure may be JavaScript-driven.")
    return records


def load_seen():
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def load_old_feed():
    if not FEED.exists():
        return []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(FEED.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError):
        return []
    items = []
    for node in root.findall("./channel/item"):
        items.append({
            "company": "",
            "title": node.findtext("title", "NGX disclosure"),
            "submitted": node.findtext("pubDate", ""),
            "link": node.findtext("link", ""),
        })
    return items


def write_feed(items):
    combined = []
    seen_links = set()
    for item in items + load_old_feed():
        link = item.get("link", "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        combined.append(item)
    combined = combined[:500]

    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        '<title>Oracle Finance — NGX Corporate Disclosures</title>',
        f'<link>{html.escape(SOURCE)}</link>',
        '<description>Company disclosures published by Nigerian Exchange Group.</description>',
        f'<lastBuildDate>{html.escape(now)}</lastBuildDate>',
    ]
    for item in combined:
        title = f"{item.get('company', '')} | {item.get('title', 'NGX disclosure')}".strip(" |")
        pubdate = item.get("submitted") or now
        lines.extend([
            '<item>',
            f'<title>{html.escape(title, quote=True)}</title>',
            f'<link>{html.escape(item["link"], quote=True)}</link>',
            f'<description>{html.escape(item.get("company", ""), quote=True)}</description>',
            f'<pubDate>{html.escape(pubdate, quote=True)}</pubDate>',
            '</item>',
        ])
    lines.append('</channel></rss>')
    FEED.write_text("\n".join(lines), encoding="utf-8")


def send_email(items):
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    recipient = os.environ.get("ALERT_TO", "").strip()
    if not (user and password and recipient):
        print("WARNING: SMTP settings missing; email skipped.")
        return

    message = EmailMessage()
    message["Subject"] = f"[ORACLE FINANCE NGX DISCLOSURES] {len(items)} new filing(s)"
    message["From"] = user
    message["To"] = recipient
    lines = [
        "ORACLE FINANCE — NEW NGX CORPORATE DISCLOSURES",
        "Source: Nigerian Exchange Group",
        "",
        f"New disclosures: {len(items)}",
        "",
    ]
    for item in items:
        lines.extend([
            item["company"],
            item["title"],
            f"Date submitted: {item['submitted']}",
            item["link"],
            "",
        ])
    message.set_content("\n".join(lines))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(user, password)
        server.send_message(message)
    print(f"NGX disclosure email sent to {recipient}.")


def main():
    print("Starting Oracle Finance NGX disclosure monitor...")
    records = fetch_disclosures()
    print(f"NGX disclosure records retrieved: {len(records)}")

    seen = set(load_seen())
    fresh = []
    for record in records:
        identity = hashlib.sha256(
            (record["company"] + "|" + record["title"] + "|" +
             record["submitted"] + "|" + record["link"]).encode("utf-8")
        ).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        fresh.append(record)

    STATE.write_text(
        json.dumps(list(seen)[-30000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_feed(fresh)

    if fresh:
        send_email(fresh)
    else:
        print("No new NGX disclosures since the previous successful check.")
    print(f"Completed. New disclosures: {len(fresh)}. RSS: {FEED}")


if __name__ == "__main__":
    main()
