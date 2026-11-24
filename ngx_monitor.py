import os
import json
import hashlib
import smtplib
import html
from pathlib import Path
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS = BASE / "docs"
STATE = DATA / "ngx_seen.json"
FEED = DOCS / "oracle-ngx-filings.xml"
SOURCE = "https://www.ngxgroup.com/exchange/data/corporate-disclosures/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OracleFinanceNGXMonitor/1.0; +mailto:officialoraclefinance@gmail.com)",
    "Accept": "text/html,application/xhtml+xml",
}

DATA.mkdir(exist_ok=True)
DOCS.mkdir(exist_ok=True)


class DisclosureTableParser(HTMLParser):
    """Extract rows from the NGX public Corporate Disclosures table."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_text = []
        self.row = []
        self.rows = []
        self.current_link = None
        self.cell_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in ("td", "th"):
            self.in_cell = True
            self.cell_text = []
            self.cell_link = None
        elif self.in_cell and tag == "a":
            href = attrs.get("href")
            if href:
                self.cell_link = href

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag):
        if self.in_cell and tag in ("td", "th"):
            self.row.append({
                "text": " ".join(" ".join(self.cell_text).split()),
                "link": self.cell_link,
            })
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
        elif tag == "table" and self.in_table:
            self.in_table = False


def fetch_disclosures():
    response = requests.get(SOURCE, headers=HEADERS, timeout=45)
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("NGX disclosures page did not return HTML.")

    parser = DisclosureTableParser()
    parser.feed(response.text)

    records = []
    for row in parser.rows:
        cells = [cell.get("text", "").strip() for cell in row]
        if len(cells) < 3:
            continue
        normalized = " ".join(cells).lower()
        if "company" in normalized and "disclosure" in normalized and "date submitted" in normalized:
            continue

        company = cells[0]
        disclosure = cells[1]
        submitted = cells[2]
        link = row[1].get("link") or row[0].get("link")

        if not company or not disclosure or not submitted:
            continue
        if len(company) > 180 or len(disclosure) > 500:
            continue
        if not link:
            continue

        records.append({
            "company": company,
            "title": disclosure,
            "submitted": submitted,
            "link": urljoin(SOURCE, link),
        })

    if not records:
        raise RuntimeError(
            "No disclosure rows could be parsed from the NGX page. "
            "The page may have changed or may require JavaScript; no empty feed was published."
        )
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
