# Amazon.in Price Alert

A small, privacy-first website. A visitor enters **their email, an amazon.in product link, and a price range**. The site emails them whenever the price **drops or rises and lands inside that range**. Built on the original `price_tracker.py` (same scraping idea, rewritten for many users and safe storage).

Everything here is free: no paid APIs, no paid services.

## How it works

1. User submits the form, and receives a confirmation email from the site's mailbox.
2. After they confirm, a background job checks each product every `CHECK_INTERVAL_MINUTES` (default 120). One fetch per product, however many users track it.
3. For every tracker: if `min <= price <= max` and the price differs from the last one we told them about, an email is sent saying it **dropped**, **rose**, or is **now in range**. When the price leaves the range the alert re-arms.
4. Every email has a link that deletes that alert, or all of the user's data.

## Privacy and security (the "firewall")

| Protection | What it does |
|---|---|
| Encrypted emails | Stored with Fernet (AES). A keyed HMAC is stored beside it for duplicate checks. A leaked database alone exposes no addresses. |
| Double opt-in | Nothing is tracked until the owner confirms by email. Unconfirmed rows are purged after 24 h. |
| One-click deletion | Signed link in every email; GET only shows a confirm page, so mail scanners cannot trigger deletion. |
| No enumeration | Duplicate or existing addresses get the same response as new ones; verified alerts can't be edited by re-submitting. |
| SSRF defence | The server never fetches the user's URL. It extracts the ASIN from an `amazon.in` link and rebuilds the URL itself; redirects are disabled. |
| Rate limiting | 5 signups/hour and 15/day per IP, 200 requests/hour overall; per-email and global tracker caps. |
| CSRF + honeypot | Flask-WTF tokens on every POST, hidden bot trap field. |
| HTTP hardening | Strict CSP (no inline scripts), HSTS, no framing, no referrer, nosniff, `no-store` on token pages, secure cookies. |
| Data minimisation | No logging of emails, 90-day price history, orphaned products deleted. |
| Secrets | Only in environment variables. Use a **dedicated Gmail account** for the site so a leak never touches a personal account. |

This is an application-level firewall. For network-level protection (DDoS, bad-bot filtering, hidden origin IP), put the site behind **Cloudflare's free plan** and set `TRUST_PROXY=1`.

## Free price source: honest notes

There is no official Amazon price API that is both free and unrestricted. This project uses the free approach from your original script (reading the public product page, with the buy-box price selectors and polite spacing). Limits to know:

- Amazon's terms discourage scraping and it blocks cloud IPs. On a 503/captcha the worker stops the run and tries again next cycle. Products failing 20 times in a row are paused.
- Amazon's free **Product Advertising API** is a possible swap-in (needs an Associates account with qualifying sales). Add a function to `PROVIDERS` in `scraper.py` and set `PRICE_PROVIDER`.
- Keep the user count small (`MAX_TOTAL_TRACKERS`) to stay under Amazon's radar.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill in SECRET_KEY, ENCRYPTION_KEY, SMTP_USER, SMTP_PASSWORD
python app.py            # http://localhost:5000
python -m unittest tests.test_basic -v
```

Gmail app password: Google Account → Security → 2-Step Verification → App passwords.

## Deploy (free options change often, check current terms)

- Run with `gunicorn app:app --workers 1 --threads 4` (one worker, so only one scheduler runs). See `Procfile`.
- SQLite needs a **persistent disk**. Hosts whose free tier wipes the disk on restart will lose data. A free always-on VM or a home server/Raspberry Pi behind a Cloudflare Tunnel works well.
- Set `BASE_URL` to your HTTPS address, `COOKIE_SECURE=1`, and never commit `.env`.
- **Back up `ENCRYPTION_KEY` and `SECRET_KEY`**. Losing them makes stored emails unreadable and existing links invalid.

## Files

`app.py` routes and scheduler · `worker.py` check + alert logic · `scraper.py` price source · `mailer.py` emails · `db.py` storage · `security.py` crypto, validation, headers · `templates/`, `static/` the site · `tests/` smoke tests

Not legal advice: because you store email addresses, add a short privacy policy page (India's DPDP Act applies).
