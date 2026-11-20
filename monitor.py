import os, json, time, smtplib, hashlib
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path
import requests
import feedparser

BASE = Path(__file__).parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
STATE = DATA / 'seen.json'
OUT = BASE / 'docs' / 'oracle-us-filings.xml'
OUT.parent.mkdir(exist_ok=True)
SEC_TICKERS = 'https://www.sec.gov/files/company_tickers_exchange.json'
USER_AGENT = os.environ['SEC_USER_AGENT']
RSS_URL = os.environ['SEC_LATEST_FILINGS_RSS']
FORMS = set(os.getenv('WATCH_FORMS', '8-K,10-Q,10-K,4,3,5,SC 13D,SC 13G,13D,13G,S-1,S-3,DEF 14A,6-K,20-F,40-F').split(','))

session = requests.Session()
session.headers.update({'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip, deflate', 'Host': 'www.sec.gov'})


def load_issuer_map():
        return out


def esc(s):
    r.raise_for_status()
    raw = r.json()

    # SEC directory uses fields + data arrays.
    if isinstance(raw, dict) and isinstance(raw.get('fields'), list) and isinstance(raw.get('data'), list):
        fields = raw['fields']
        rows = [
            dict(zip(fields, row))
            for row in raw['data']
            if isinstance(row, list)
        ]
    elif isinstance(raw, dict) and isinstance(raw.get('data'), list):
        rows = raw['data']
    elif isinstance(raw, dict):
        rows = list(raw.values())
    else:
        rows = raw

    out = {}

    for x in rows:
        if not isinstance(x, dict):
            continue

        exchange = str(x.get('exchange', '')).strip().upper()
        if exchange not in {'NYSE', 'NASDAQ'}:
            continue

        cik = str(x.get('cik_str') or x.get('cik') or '').zfill(10)
        ticker = str(x.get('ticker', '')).strip().upper()

        if cik and ticker:
            out[cik] = {
                'ticker': ticker,
                'name': x.get('name') or x.get('title', ''),
                'exchange': exchange
            }

    return out
    
    r = session.get(SEC_TICKERS, timeout=30); r.raise_for_status()
    raw = r.json()
    rows = raw.values() if isinstance(raw, dict) else raw
    out = {}
    for x in rows:
        exchange = str(x.get('exchange','')).strip().upper()
        if exchange not in {'NYSE', 'NASDAQ'}: continue
        cik = str(x.get('cik_str') or x.get('cik') or '').zfill(10)
        ticker = str(x.get('ticker','')).strip().upper()
        if cik and ticker: out[cik] = {'ticker': ticker, 'name': x.get('name') or x.get('title',''), 'exchange': exchange}
    return out

def esc(s):
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def main():
    issuers = load_issuer_map()
    parsed = feedparser.parse(RSS_URL)
    if parsed.bozo and not parsed.entries: raise RuntimeError(f'RSS parse failed: {parsed.bozo_exception}')
    seen = json.loads(STATE.read_text()) if STATE.exists() else []
    seen_set = set(seen)
    fresh=[]
    for e in parsed.entries:
        link = e.get('link','')
        # SEC filing URLs include /data/{CIK}/; extract the numeric CIK.
        import re
        m = re.search(r'/data/(\d+)/', link)
        if not m: m = re.search(r'\bCIK\s*=?\s*(\d+)', e.get('summary',''), re.I)
        if not m: continue
        cik = m.group(1).zfill(10)
        issuer = issuers.get(cik)
        if not issuer: continue
        title = e.get('title','SEC filing')
        form = (e.get('form') or '').strip()
        if not form:
            fm = re.search(r'\b(8-K|10-Q|10-K|20-F|6-K|40-F|S-1|S-3|DEF 14A|SC 13D|SC 13G|13D|13G|4|3|5)\b', title, re.I)
            form = fm.group(1).upper() if fm else ''
        if form and form not in FORMS: continue
        uid = e.get('id') or link or hashlib.sha256((title+link).encode()).hexdigest()
        if uid in seen_set: continue
        item = {'title':title, 'link':link, 'date':e.get('published',datetime.now(timezone.utc).isoformat()), 'form':form or 'Unclassified', **issuer}
        fresh.append(item); seen_set.add(uid)
    # Persist deduplication state (retain most recent 20,000 IDs).
    STATE.write_text(json.dumps(list(seen_set)[-20000:], indent=2))
    existing=[]
    if OUT.exists():
        old=feedparser.parse(str(OUT))
        for e in old.entries:
            existing.append({'title':e.title,'link':e.link,'date':e.get('published',''),'form':'Previously published','ticker':'','name':'','exchange':''})
    all_items = (fresh + existing)[:500]
    now=datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    xml=['<?xml version="1.0" encoding="UTF-8"?>','<rss version="2.0"><channel>',
         '<title>Oracle Finance — US Market Filings</title>',
         '<link>https://www.sec.gov/</link>',
         '<description>Filtered SEC filings for NYSE and Nasdaq issuers monitored by Oracle Finance.</description>',
         f'<lastBuildDate>{now}</lastBuildDate>']
    for x in all_items:
        xml += ['<item>',f'<title>{esc(x["ticker"])} | {esc(x["form"])} | {esc(x["title"])}</title>',
                f'<link>{esc(x["link"])}</link>',f'<description>{esc(x["name"])} ({esc(x["exchange"])})</description>',
                f'<pubDate>{esc(x["date"])}</pubDate>','</item>']
    xml += ['</channel></rss>']
    OUT.write_text('\n'.join(xml), encoding='utf-8')
    if fresh:
        send_email(fresh)
    print(f'Issuer map: {len(issuers)} SEC-mapped NYSE/Nasdaq issuer records; new matched filings: {len(fresh)}')

def send_email(items):
    host=os.environ.get('SMTP_HOST','smtp.gmail.com'); port=int(os.environ.get('SMTP_PORT','465'))
    user=os.environ['SMTP_USER']; password=os.environ['SMTP_APP_PASSWORD']; recipient=os.environ['ALERT_TO']
    msg=EmailMessage(); msg['Subject']=f'[ORACLE US FILINGS] {len(items)} new filing(s)'
    msg['From']=user; msg['To']=recipient
    lines=['Oracle Finance — new SEC filing alerts','']
    for x in items:
        lines += [f"{x['ticker']} ({x['exchange']}) — {x['form']}", x['name'], x['title'], x['date'], x['link'], '']
    msg.set_content('\n'.join(lines))
    with smtplib.SMTP_SSL(host,port,timeout=30) as s:
        s.login(user,password); s.send_message(msg)

if __name__ == '__main__': main()
