#!/usr/bin/env python3
"""Send Telegram alerts for newly added items in the unified Oracle Finance RSS."""
import os, json, html, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path

BASE=Path(__file__).resolve().parent
FEED=BASE/"docs/oracle-global-filings.xml"
STATE=BASE/"docs/telegram-alert-state.json"
token="".join(os.environ.get("TELEGRAM_BOT_TOKEN","").split())
chat="".join(os.environ.get("TELEGRAM_CHAT_ID","").split())
if not token or not chat:
    raise SystemExit("Telegram secrets are missing.")
if not FEED.exists():
    raise SystemExit("Global RSS feed does not exist.")

root=ET.parse(FEED).getroot()
items=[]
for node in root.findall("./channel/item"):
    title=(node.findtext("title") or "").strip()
    link=(node.findtext("link") or "").strip()
    date=(node.findtext("pubDate") or "").strip()
    if title and link: items.append({"title":title,"link":link,"date":date})
items=items[:1000]
ids=[x["link"] for x in items]

if not STATE.exists():
    STATE.write_text(json.dumps(ids,indent=2)+"\n",encoding="utf-8")
    print(f"Baseline initialized with {len(ids)} feed items; no Telegram alert sent.")
    raise SystemExit(0)

try:
    prior=json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(prior,list): raise ValueError("State file is not a list")
except (OSError,json.JSONDecodeError,ValueError) as e:
    raise SystemExit(f"Alert state file is unreadable; stopping to prevent duplicate alerts: {e}")
previous=set(prior)
new=[x for x in items if x["link"] not in previous]
# Limit each run, but do not mark undelivered items as sent; they remain eligible next cycle.
batch=new[:15]
delivered=[]
for item in reversed(batch):
    msg="📊 <b>Oracle Finance — New Filing</b>\n\n"+html.escape(item["title"])
    if item["date"]: msg+="\n\n🗓 "+html.escape(item["date"])
    msg+="\n\n<a href='"+html.escape(item["link"],quote=True)+"'>View filing</a>"
    data=urllib.parse.urlencode({"chat_id":chat,"text":msg,"parse_mode":"HTML","disable_web_page_preview":"true"}).encode()
    req=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=data)
    with urllib.request.urlopen(req,timeout=20) as response:
        result=json.loads(response.read().decode())
        if not result.get("ok"): raise RuntimeError("Telegram API rejected a message.")
    delivered.append(item["link"])
    # Persist after each successful send, so a failure midway won't resend delivered items.
    updated=list(previous.union(delivered))
    STATE.write_text(json.dumps(updated[-5000:],indent=2)+"\n",encoding="utf-8")
if delivered:
    print(f"Sent {len(delivered)} Telegram alert(s). {len(new)-len(delivered)} remain queued if still in the feed.")
else:
    print("No new filing items to alert.")
