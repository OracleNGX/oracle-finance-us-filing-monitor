#!/usr/bin/env python3
"""Merge Oracle Finance US, China and NGX RSS feeds into one sorted global feed."""
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import html

BASE=Path(__file__).resolve().parent
DOCS=BASE/"docs"
SOURCES=[
 ("US","oracle-us-filings.xml"),
 ("China","oracle-china-filings.xml"),
 ("Nigeria / NGX","oracle-ngx-filings.xml"),
]
OUT=DOCS/"oracle-global-filings.xml"

def read_items(market, filename):
 path=DOCS/filename
 if not path.exists():
  print(f"Missing source feed: {filename}; skipping")
  return []
 try: root=ET.fromstring(path.read_text(encoding="utf-8"))
 except (ET.ParseError,OSError) as e:
  print(f"Invalid source feed {filename}: {e}; skipping")
  return []
 out=[]
 for node in root.findall("./channel/item"):
  title=(node.findtext("title") or "").strip()
  link=(node.findtext("link") or "").strip()
  date=(node.findtext("pubDate") or "").strip()
  desc=(node.findtext("description") or "").strip()
  if not title or not link: continue
  out.append({"market":market,"title":title,"link":link,"date":date,"desc":desc})
 return out

def date_key(item):
 try:
  dt=parsedate_to_datetime(item.get("date",""))
  if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
  return dt.timestamp()
 except (TypeError,ValueError,OverflowError):
  return 0

items=[]
for market,filename in SOURCES: items.extend(read_items(market,filename))
unique=[]; links=set()
for item in items:
 if item["link"] in links: continue
 links.add(item["link"]); unique.append(item)
unique.sort(key=date_key, reverse=True)
unique=unique[:1000]

def escape(v): return html.escape(str(v or ""), quote=True)
lines=['<?xml version="1.0" encoding="UTF-8"?>','<rss version="2.0"><channel>',
'<title>Oracle Finance — Global Filings Intelligence</title>',
'<link>https://github.com/OracleNGX/oracle-finance-us-filing-monitor</link>',
'<description>Unified corporate filings stream from US SEC, China CNINFO and Nigeria NGX monitored by Oracle Finance.</description>',
f'<lastBuildDate>{escape(datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"))}</lastBuildDate>']
for item in unique:
 lines.append('<item>')
 lines.append(f'<title>[{escape(item["market"])}] {escape(item["title"])}</title>')
 lines.append(f'<link>{escape(item["link"])}</link>')
 lines.append(f'<description>{escape(item["desc"])}</description>')
 if item["date"]: lines.append(f'<pubDate>{escape(item["date"])}</pubDate>')
 lines.append(f'<category>{escape(item["market"])}</category>')
 lines.append('</item>')
lines.extend(['</channel></rss>'])
OUT.write_text("\n".join(lines)+"\n",encoding="utf-8")
print(f"Global feed built: {len(unique)} unique items from {len(items)} source items, sorted newest first.")
print(f"Output: {OUT}")
