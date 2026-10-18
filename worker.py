"""Price checking + alert logic. Run by the scheduler, or once via `python worker.py`."""
import logging
import os
import random
import threading
import time

import db
import mailer
import scraper
import security

log = logging.getLogger("worker")
_lock = threading.Lock()


def _base() -> str:
    return os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


def unsub_url(tracker) -> str:
    tok = security.make_token("unsub", {"t": tracker["id"], "h": tracker["email_hash"]})
    return f"{_base()}/unsubscribe/{tok}"


def _check(asin: str):
    prod = db.get_product(asin)
    if not prod:
        return
    previous = prod["last_price"]
    title, price = scraper.fetch(asin)
    db.save_price(asin, title, price)
    title = title or prod["title"]

    for t in db.verified_trackers(asin):
        in_range = t["min_price"] <= price <= t["max_price"]
        last = t["last_notified_price"]
        if not in_range:
            if last is not None:
                db.set_notified(t["id"], None)  # left the range: re-arm for next entry
            continue
        if last is not None and abs(last - price) < 0.005:
            continue  # already told them about this exact price
        ref = last if last is not None else previous
        if ref is None or abs(ref - price) < 0.005:
            direction = "in_range"
        else:
            direction = "dropped" if price < ref else "increased"
        email = security.decrypt_email(t["email_enc"])
        if not email:
            continue
        try:
            mailer.send_alert(email, title, asin, price, ref, direction, t["min_price"], t["max_price"], unsub_url(t))
            db.set_notified(t["id"], price)
        except Exception as e:  # never log the address
            log.error("alert mail failed for tracker %s: %s", t["id"], type(e).__name__)


def check_asin(asin: str):
    """Single product (used right after a user confirms their email)."""
    with _lock:
        try:
            _check(asin)
        except scraper.ScrapeError as e:
            log.info("check %s failed: %s", asin, e)
            db.mark_failure(asin)


def run_check():
    if not _lock.acquire(blocking=False):
        return
    try:
        asins = db.asins_to_check()
        random.shuffle(asins)
        for i, asin in enumerate(asins):
            try:
                _check(asin)
            except scraper.Blocked as e:
                log.warning("blocked (%s); ending this run early", e)
                break
            except scraper.ScrapeError as e:
                log.info("check %s failed: %s", asin, e)
                db.mark_failure(asin)
            except Exception:
                log.exception("unexpected error for %s", asin)
            if i < len(asins) - 1:
                time.sleep(random.uniform(4, 9))  # polite spacing between products
    finally:
        _lock.release()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    db.init()
    run_check()
    db.purge()
