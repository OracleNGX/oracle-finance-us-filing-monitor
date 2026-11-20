
import os
import re
import json
import time
import hashlib
import smtplib
import html
from pathlib import Path
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime

import requests
import feedparser


# ------------------------------------------------------------
# Oracle Finance — US Stock-Market Filing Monitor
# Sources: SEC company ticker/exchange directory + SEC RSS feed
# Output: docs/oracle-us-filings.xml
# State:  data/seen.json
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"

STATE_FILE = DATA_DIR / "seen.json"
OUTPUT_FILE = DOCS_DIR / "oracle-us-filings.xml"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()
RSS_URL = os.environ.get("SEC_LATEST_FILINGS_RSS", "").strip()

DEFAULT_FORMS = (
    "8-K,10-Q,10-K,4,3,5,SC 13D,SC 13G,13D,13G,"
    "S-1,S-3,DEF 14A,6-K,20-F,40-F"
)

WATCH_FORMS = {
    item.strip().upper()
    for item in os.environ.get("WATCH_FORMS", DEFAULT_FORMS).split(",")
    if item.strip()
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

if not USER_AGENT:
    raise RuntimeError(
        "Missing SEC_USER_AGENT. Add it under GitHub Actions repository secrets."
    )

if not RSS_URL:
    raise RuntimeError(
        "Missing SEC_LATEST_FILINGS_RSS. Add it under GitHub Actions repository variables."
    )

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*",
})


def normalize_cik(value):
    """Convert a CIK to the SEC's ten-digit format."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def load_issuer_map():
    """
    Load SEC issuer records and retain records whose exchange is
    NYSE or Nasdaq.

    The SEC directory currently uses a column-oriented format:
    {"fields": [...], "data": [[...], [...]]}
    This function also tolerates row-oriented formats.
    """
    response = session.get(SEC_TICKERS_URL, timeout=30)
    response.raise_for_status()
    raw = response.json()

    if (
        isinstance(raw, dict)
        and isinstance(raw.get("fields"), list)
        and isinstance(raw.get("data"), list)
    ):
        fields = raw["fields"]
        rows = [
            dict(zip(fields, row))
            for row in raw["data"]
            if isinstance(row, (list, tuple))
        ]
    elif isinstance(raw, dict) and isinstance(raw.get("data"), list):
        rows = raw["data"]
    elif isinstance(raw, dict):
        rows = list(raw.values())
    elif isinstance(raw, list):
        rows = raw
    else:
        raise RuntimeError("Unrecognized SEC ticker-directory format.")

    issuers = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        exchange = str(row.get("exchange") or "").strip().upper()
        if exchange not in {"NYSE", "NASDAQ"}:
            continue

        cik = normalize_cik(row.get("cik_str") or row.get("cik"))
        ticker = str(row.get("ticker") or "").strip().upper()
        name = str(row.get("name") or row.get("title") or "").strip()

        if cik and ticker:
            issuers[cik] = {
                "ticker": ticker,
                "name": name,
                "exchange": exchange,
            }

    if not issuers:
        raise RuntimeError(
            "The SEC directory returned no NYSE/Nasdaq issuer records."
        )

    return issuers


def fetch_sec_feed():
    """Fetch the SEC RSS/Atom feed using the declared SEC User-Agent."""
    response = session.get(RSS_URL, timeout=30)
    response.raise_for_status()

    parsed = feedparser.parse(response.content)

    if parsed.bozo and not parsed.entries:
        raise RuntimeError(
            f"SEC RSS feed could not be parsed: {parsed.bozo_exception}"
        )

    return parsed.entries


def extract_cik(entry):
    """Extract the issuer CIK from the filing link or feed text."""
    candidates = [
        entry.get("link", ""),
        entry.get("id", ""),
        entry.get("summary", ""),
        entry.get("title", ""),
    ]

    patterns = [
        r"/data/0*(\d{1,10})/",
        r"\bCIK\s*=?\s*0*(\d{1,10})\b",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, str(candidate), re.IGNORECASE)
            if match:
                return normalize_cik(match.group(1))

    return ""


def extract_form(entry):
    """Identify a watched filing form from feed metadata or title."""
    text = " ".join([
        str(entry.get("form") or ""),
        str(entry.get("title") or ""),
        str(entry.get("summary") or ""),
    ])

    # Match longer form names first, avoiding partial matches.
    forms = sorted(WATCH_FORMS, key=len, reverse=True)

    for form in forms:
        pattern = (
            r"(?<![A-Z0-9])"
            + re.escape(form)
            + r"(?:/[A-Z])?"
            + r"(?![A-Z0-9])"
        )
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).upper()

    return ""


def get_entry_date(entry):
    """Return a valid RFC 2822 date for the RSS item."""
    published = entry.get("published") or entry.get("updated")

    if published:
        try:
            dt = parsedate_to_datetime(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt.astimezone(timezone.utc))
        except (TypeError, ValueError, OverflowError):
            pass

    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
            return format_datetime(dt)
        except (TypeError, ValueError, OverflowError):
            pass

    return format_datetime(datetime.now(timezone.utc))


def load_seen_state():
    """Read prior filing IDs; tolerate a missing or older state file."""
    if not STATE_FILE.exists():
        return []

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("WARNING: Could not read previous seen.json; starting a new state.")
        return []

    if isinstance(data, list):
        return [str(item) for item in data]

    if isinstance(data, dict):
        values = data.get("seen", [])
        if isinstance(values, list):
            return [str(item) for item in values]

    return []


def entry_identifier(entry):
    """Create a stable identifier to prevent repeat notifications."""
    value = entry.get("id") or entry.get("link")

    if not value:
        value = (
            str(entry.get("title", ""))
            + "|"
            + str(entry.get("published", ""))
        )

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def escape_xml(value):
    return html.escape(str(value or ""), quote=True)


def load_existing_rss_items():
    """Preserve previously published feed entries."""
    if not OUTPUT_FILE.exists():
        return []

    try:
        parsed = feedparser.parse(OUTPUT_FILE.read_bytes())
    except OSError:
        return []

    items = []

    for entry in parsed.entries:
        items.append({
            "title": entry.get("title", "SEC filing"),
            "link": entry.get("link", ""),
            "date": entry.get("published", ""),
            "form": "Previously published",
            "ticker": "",
            "name": "",
            "exchange": "",
        })

    return items


def write_rss_feed(new_items):
    """Write a valid RSS 2.0 feed, keeping recent items first."""
    old_items = load_existing_rss_items()

    combined = []
    seen_links = set()

    for item in new_items + old_items:
        link = item.get("link", "").strip()

        if link and link in seen_links:
            continue

        if link:
            seen_links.add(link)

        combined.append(item)

    combined = combined[:500]

    now = format_datetime(datetime.now(timezone.utc))

    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>Oracle Finance — US Market Filings</title>",
        "<link>https://www.sec.gov/</link>",
        (
            "<description>SEC filing alerts for SEC-mapped "
            "NYSE and Nasdaq issuers monitored by Oracle Finance.</description>"
        ),
        f"<lastBuildDate>{escape_xml(now)}</lastBuildDate>",
    ]

    for item in combined:
        title = (
            f"{item.get('ticker', '')} | "
            f"{item.get('form', '')} | "
            f"{item.get('title', 'SEC filing')}"
        ).strip(" |")

        description = (
            f"{item.get('name', '')} "
            f"({item.get('exchange', '')})"
        ).strip()

        xml.extend([
            "<item>",
            f"<title>{escape_xml(title)}</title>",
            f"<link>{escape_xml(item.get('link', ''))}</link>",
            f"<description>{escape_xml(description)}</description>",
            f"<pubDate>{escape_xml(item.get('date', now))}</pubDate>",
            "</item>",
        ])

    xml.extend(["</channel>", "</rss>"])

    OUTPUT_FILE.write_text("\n".join(xml), encoding="utf-8")


def send_email(items):
    """Send one email containing all new matched filings."""
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    recipient = os.environ.get("ALERT_TO", "").strip()

    if not user or not password or not recipient:
        print(
            "WARNING: New filings were found, but email was not sent. "
            "Check SMTP_USER, SMTP_APP_PASSWORD and ALERT_TO secrets."
        )
        return

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    message = EmailMessage()
    message["Subject"] = (
        f"[ORACLE FINANCE US FILINGS] {len(items)} new filing(s)"
    )
    message["From"] = user
    message["To"] = recipient

    lines = [
        "ORACLE FINANCE — NEW SEC FILING ALERTS",
        "",
        f"New matched filings: {len(items)}",
        "",
    ]

    for item in items:
        lines.extend([
            f"{item['ticker']} ({item['exchange']}) — {item['form']}",
            item["name"],
            item["title"],
            item["date"],
            item["link"],
            "",
        ])

    message.set_content("\n".join(lines))

    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(user, password)
        server.send_message(message)

    print(f"Email alert sent to {recipient}.")


def main():
    print("Starting Oracle Finance US filing monitor...")

    issuers = load_issuer_map()
    print(f"Loaded {len(issuers)} SEC-mapped NYSE/Nasdaq issuer records.")

    entries = fetch_sec_feed()
    print(f"SEC feed entries received: {len(entries)}")

    seen_ids = load_seen_state()
    seen_set = set(seen_ids)

    new_ids = []
    fresh_items = []

    for entry in entries:
        cik = extract_cik(entry)
        if not cik:
            continue

        issuer = issuers.get(cik)
        if not issuer:
            continue

        form = extract_form(entry)
        if not form:
            continue

        link = str(entry.get("link") or "").strip()
        title = str(entry.get("title") or "SEC filing").strip()

        identifier = entry_identifier(entry)
        if identifier in seen_set:
            continue

        item = {
            "title": title,
            "link": link,
            "date": get_entry_date(entry),
            "form": form,
            **issuer,
        }

        fresh_items.append(item)
        new_ids.append(identifier)
        seen_set.add(identifier)

    # Keep a bounded, deterministic duplicate-protection history.
    updated_ids = (seen_ids + new_ids)[-20000:]
    STATE_FILE.write_text(
        json.dumps(updated_ids, indent=2),
        encoding="utf-8",
    )

    write_rss_feed(fresh_items)

    if fresh_items:
        send_email(fresh_items)
    else:
        print("No new matched filings found in this feed check.")

    print(
        "Completed. "
        f"New matched filings: {len(fresh_items)}. "
        f"RSS output: {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
    
