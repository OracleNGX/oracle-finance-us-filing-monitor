import os,json,hashlib,smtplib,html,re
from pathlib import Path
from datetime import datetime,timezone
from email.message import EmailMessage
from email.utils import format_datetime
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
import requests

BASE=Path(__file__).resolve().parent
DATA,DOCS=BASE/"data",BASE/"docs"
STATE,FEED=DATA/"ngx_seen.json",DOCS/"oracle-ngx-filings.xml"
SOURCE="https://ngxwebsite-dc69c46e9b-hxcxhmabe6aubmg9.a03.azurefd.net/exchange/data/corporate-disclosures/"
ALT="https://www.ngxgroup.com/exchange/data/corporate-disclosures/"
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; OracleFinanceNGXMonitor/2.0; +mailto:officialoraclefinance@gmail.com)","Accept":"text/html,application/xhtml+xml"}
DATA.mkdir(exist_ok=True); DOCS.mkdir(exist_ok=True)
S=requests.Session(); S.headers.update(HEADERS)
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def doclink(u): 
 u=u.lower(); return ".pdf" in u or "doclib.ngxgroup.com" in u or "financial_newsdocs" in u or "newsdocs" in u
def parse(soup,url):
 out=[]
 for table in soup.find_all("table"):
  rows=table.find_all("tr")
  if len(rows)<2: continue
  hrow=next((r for r in rows if r.find("th")),rows[0])
  hs=[clean(c.get_text(" ",strip=True)).lower() for c in hrow.find_all(["th","td"])]
  ci=next((i for i,h in enumerate(hs) if "company" in h or "issuer" in h),None)
  ti=next((i for i,h in enumerate(hs) if any(k in h for k in ("disclosure","announcement","filing"))),None)
  di=next((i for i,h in enumerate(hs) if "date" in h),None)
  if None in (ci,ti,di): continue
  for row in rows:
   if row is hrow: continue
   cs=row.find_all(["td","th"])
   if max(ci,ti,di)>=len(cs): continue
   a=cs[ti].find("a",href=True) or row.find("a",href=True)
   co=clean(cs[ci].get_text(" ",strip=True)); title=clean(cs[ti].get_text(" ",strip=True)); date=clean(cs[di].get_text(" ",strip=True))
   if co and title and date and a:
    link=urljoin(url,a["href"])
    if doclink(link): out.append({"company":co,"title":title,"submitted":date,"link":link})
 if not out:
  for a in soup.find_all("a",href=True):
   link=urljoin(url,a["href"]); label=clean(a.get_text(" ",strip=True))
   if not doclink(link) or not label or label.lower() in {"download","read more","offer documents"}: continue
   context=clean(a.parent.parent.parent.get_text(" ",strip=True)) if a.parent and a.parent.parent and a.parent.parent.parent else label
   date=re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9} \d{1,2},? \d{4})",context)
   out.append({"company":"NGX issuer","title":context[:500] if context else label,"submitted":date.group(0) if date else datetime.now(timezone.utc).strftime("%Y-%m-%d"),"link":link})
 return out
def fetch_disclosures():
 errors=[]
 for base in (SOURCE,ALT):
  records=[]; queue=[base]; visited=set()
  for _ in range(4):
   if not queue: break
   u=queue.pop(0)
   if u in visited: continue
   visited.add(u)
   try:
    r=S.get(u,timeout=40); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser")
    records+=parse(soup,r.url)
    for a in soup.find_all("a",href=True):
     label=clean(a.get_text(" ",strip=True)).lower(); nxt=urljoin(r.url,a["href"])
     if (label in {"next","2","3","older"} or "page=" in nxt.lower()) and urlparse(nxt).netloc==urlparse(r.url).netloc and nxt not in visited: queue.append(nxt)
   except Exception as e: errors.append(f"{u}: {e}")
  unique=[]; links=set()
  for x in records:
   if x["link"] not in links and doclink(x["link"]): links.add(x["link"]); unique.append(x)
  if unique: print(f"Source: {base}; pages checked: {len(visited)}"); return unique
 raise RuntimeError("No verifiable NGX filing-document records extracted; generic links were rejected. "+" | ".join(errors[-3:]))
def load_seen():
 try:
  v=json.loads(STATE.read_text()); return v if isinstance(v,list) else []
 except (OSError,json.JSONDecodeError): return []
def oldfeed():
 if not FEED.exists(): return []
 import xml.etree.ElementTree as ET
 try: root=ET.fromstring(FEED.read_text())
 except (OSError,ET.ParseError): return []
 return [{"company":"","title":n.findtext("title",""),"submitted":n.findtext("pubDate",""),"link":n.findtext("link","")} for n in root.findall("./channel/item")]
def writefeed(items):
 allitems=[]; seen=set()
 for x in items+oldfeed():
  if x.get("link") and x["link"] not in seen: seen.add(x["link"]); allitems.append(x)
 now=format_datetime(datetime.now(timezone.utc))
 lines=['<?xml version="1.0" encoding="UTF-8"?>','<rss version="2.0"><channel>','<title>Oracle Finance — NGX Corporate Disclosures</title>',f'<link>{html.escape(SOURCE)}</link>','<description>NGX issuer filing documents.</description>',f'<lastBuildDate>{html.escape(now)}</lastBuildDate>']
 for x in allitems[:500]:
  title=f'{x.get("company","")} | {x.get("title","")}'.strip(" |")
  lines+=['<item>',f'<title>{html.escape(title,quote=True)}</title>',f'<link>{html.escape(x["link"],quote=True)}</link>',f'<description>{html.escape(x.get("company",""),quote=True)}</description>',f'<pubDate>{html.escape(x.get("submitted") or now,quote=True)}</pubDate>','</item>']
 lines+=['</channel></rss>']; FEED.write_text("\n".join(lines),encoding="utf-8")
def email_alert(items):
 user=os.getenv("SMTP_USER","").strip(); pwd=os.getenv("SMTP_APP_PASSWORD","").strip(); to=os.getenv("ALERT_TO","").strip()
 if not(user and pwd and to): print("WARNING: SMTP settings missing; email skipped."); return
 m=EmailMessage(); m["Subject"]=f"[ORACLE FINANCE NGX DISCLOSURES] {len(items)} new filing(s)"; m["From"]=user;m["To"]=to
 lines=["ORACLE FINANCE — NEW NGX CORPORATE DISCLOSURES","Source: Nigerian Exchange Group","",f"New disclosures: {len(items)}",""]
 for x in items: lines.extend([x["company"],x["title"],f'Date submitted: {x["submitted"]}',x["link"],""])
 m.set_content("\n".join(lines))
 with smtplib.SMTP_SSL(os.getenv("SMTP_HOST","smtp.gmail.com"),int(os.getenv("SMTP_PORT","465")),timeout=30) as s: s.login(user,pwd);s.send_message(m)
 print("NGX disclosure email sent.")
def main():
 print("Starting Oracle Finance NGX monitor v2...")
 records=fetch_disclosures(); print(f"Verified document records retrieved: {len(records)}")
 seen=set(load_seen()); fresh=[]
 for x in records:
  key=hashlib.sha256("|".join(x[k] for k in ("company","title","submitted","link")).encode()).hexdigest()
  if key not in seen: seen.add(key);fresh.append(x)
 STATE.write_text(json.dumps(list(seen)[-30000:],ensure_ascii=False,indent=2),encoding="utf-8")
 writefeed(fresh)
 if fresh: email_alert(fresh)
 else: print("No new disclosures.")
 print(f"Completed. New disclosures: {len(fresh)}. RSS: {FEED}")
if __name__=="__main__": main()
