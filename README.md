# Oracle Finance US Filing Monitor — starter kit

This starter polls a SEC Latest Filings RSS search feed, filters entries against SEC's exchange/ticker/CIK mapping for NYSE and Nasdaq, sends Gmail alerts, and writes an RSS 2.0 feed at `docs/oracle-us-filings.xml`.

## Important coverage caveat

This is a practical starter, not a guarantee of exhaustive real-time coverage. The SEC ticker/exchange mapping is periodically updated and is not a definitive security-master classification of every common-stock class. Validate the issuer universe against the NYSE and Nasdaq listing directories, explicitly exclude ETFs, preferreds, warrants, units, rights and other non-common securities, and reconcile against SEC daily indexes. RSS feeds are best-effort notification sources; daily index reconciliation is required for completeness. Foreign private issuers may file 6-K/20-F; include those forms if desired.

## Setup

1. Create a **public** GitHub repository from this folder (GitHub Pages is free for public repositories).
2. In SEC EDGAR Latest Filings search, choose the desired filing types, set company to all, then use the RSS link above the results. Copy the actual RSS URL.
3. In GitHub repo Settings → Secrets and variables → Actions, add:
   - Secret `SEC_USER_AGENT`: `Oracle Finance filing monitor contact officialoraclefinance@gmail.com`
   - Variable `SEC_LATEST_FILINGS_RSS`: the RSS URL copied from SEC EDGAR.
   - Secret `SMTP_USER`: the Gmail account used to send alerts.
   - Secret `SMTP_APP_PASSWORD`: a Google App Password (requires 2-Step Verification; never use your normal Gmail password).
   - Secret `ALERT_TO`: `officialoraclefinance@gmail.com` (or another approved destination).
4. Enable GitHub Actions and Pages. Configure Pages to deploy from branch `main`, folder `/docs`.
5. Run Actions → Oracle Finance US filing monitor → Run workflow. Check the action log and open the published `oracle-us-filings.xml` feed URL.
6. Subscribe to the published RSS URL in Inoreader or another RSS reader.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SEC_USER_AGENT='Oracle Finance contact officialoraclefinance@gmail.com'
export SEC_LATEST_FILINGS_RSS='PASTE_SEC_RSS_URL'
export SMTP_USER='sender@gmail.com'
export SMTP_APP_PASSWORD='GMAIL_APP_PASSWORD'
export ALERT_TO='officialoraclefinance@gmail.com'
python monitor.py
```

## Testing checklist

- Confirm SEC RSS URL returns entries and is not an HTML search page.
- Confirm issuer map contains known NYSE and Nasdaq tickers.
- Confirm non-NYSE/Nasdaq issuers are excluded.
- Trigger a manual workflow and confirm one email arrives.
- Confirm duplicate run does not resend the same filing.
- Confirm RSS XML parses in an RSS reader.
- Compare a sample of alerts against SEC Latest Filings and the day's master index.
- Test an 8-K, 10-Q/10-K, Form 4, and 13D/13G separately.
- Monitor GitHub Actions failures and SEC rate-limit responses.

## Operational notes

- Scheduled GitHub Actions jobs are not guaranteed to start at the exact cron minute; use the run logs to measure actual latency.
- The current workflow runs every 15 minutes on weekdays. SEC filings are generally accepted/disseminated during weekday US business hours, with after-hours processing and index timing exceptions.
- Do not parallelize requests to SEC.gov. Respect the SEC's current fair-access limit and declared User-Agent policy.
- Add daily SEC master-index reconciliation before describing the system as complete or audit-grade.
