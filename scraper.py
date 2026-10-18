"""Free price source: reads the public amazon.in product page (no API key, no cost).

The provider is pluggable: add another function to PROVIDERS (for example the free
Amazon Product Advertising API once you have an Associates account) and set
PRICE_PROVIDER=<name>. Each provider returns (title, price_in_rupees).
"""
import os
import random
import re

import requests
from bs4 import BeautifulSoup

import security


class ScrapeError(Exception):
    pass


class Blocked(ScrapeError):
    """Amazon is throttling us: stop the run instead of hammering it."""


_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Price selectors, most specific (the buy-box) first, so a sponsored or "related
# product" price is never picked up by mistake.
_PRICE_SELECTORS = [
    "#corePrice_feature_div .priceToPay .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#apex_desktop .priceToPay .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#centerCol .a-price .a-offscreen",
    "#ppd .a-price .a-offscreen",
]


def parse_price(text: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def parse_page(html: str) -> tuple[str | None, float]:
    soup = BeautifulSoup(html, "html.parser")
    low = html.lower()
    if "enter the characters you see below" in low or "api-services-support@amazon.com" in low:
        raise Blocked("captcha page")
    t = soup.find(id="productTitle")
    title = t.get_text().strip() if t else None
    for sel in _PRICE_SELECTORS:
        el = soup.select_one(sel)
        price = parse_price(el.get_text()) if el else None
        if price:
            return title, price
    raise ScrapeError("price not found (out of stock or layout changed)")


def fetch_direct(asin: str) -> tuple[str | None, float]:
    headers = {
        "User-Agent": random.choice(_UAS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # 'br' removed: requests can't decode it without brotli
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.amazon.in/",
        "DNT": "1",
    }
    try:
        r = requests.get(security.product_url(asin), headers=headers, timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        raise ScrapeError(f"network error: {type(e).__name__}") from e
    if r.status_code in (429, 503):
        raise Blocked(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise ScrapeError(f"HTTP {r.status_code}")
    return parse_page(r.text)


PROVIDERS = {"direct": fetch_direct}


def fetch(asin: str) -> tuple[str | None, float]:
    name = os.environ.get("PRICE_PROVIDER", "direct")
    if name not in PROVIDERS:
        raise ScrapeError(f"unknown PRICE_PROVIDER '{name}'")
    return PROVIDERS[name](asin)
