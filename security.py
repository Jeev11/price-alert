"""Privacy + security helpers: encryption, hashing, URL validation, tokens, headers."""
import hashlib
import hmac
import os
import re
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer


def _secret() -> str:
    s = os.environ.get("SECRET_KEY", "")
    if len(s) < 32:
        raise RuntimeError("SECRET_KEY must be set to a random string of 32+ characters.")
    return s


def _fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set (see .env.example).")
    return Fernet(key.encode())


# ---- email protection -------------------------------------------------------
# Emails are stored ENCRYPTED (Fernet/AES). A keyed hash is stored beside it so we
# can detect duplicates without ever decrypting. A stolen database alone reveals
# no addresses; the attacker would also need ENCRYPTION_KEY from the server env.
def email_hash(email: str) -> str:
    return hmac.new(_secret().encode(), b"email:" + email.lower().encode(), hashlib.sha256).hexdigest()


def encrypt_email(email: str) -> str:
    return _fernet().encrypt(email.encode()).decode()


def decrypt_email(blob: str) -> str | None:
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken:
        return None


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(_EMAIL_RE.match(email))


# ---- URL validation (SSRF defence) -----------------------------------------
# We never fetch what the user typed. We only extract the 10-char ASIN from an
# amazon.in link and rebuild the URL ourselves, so users cannot point the server
# at internal addresses or other sites.
_ALLOWED_HOSTS = {"amazon.in", "www.amazon.in"}
_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?#]|$)")


def parse_amazon_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw or len(raw) > 2048:
        return None
    try:
        p = urlparse(raw)
        if p.scheme not in ("http", "https"):
            return None
        if (p.hostname or "").lower() not in _ALLOWED_HOSTS:
            return None
        if p.username or p.password or p.port not in (None, 80, 443):
            return None
    except ValueError:
        return None
    m = _ASIN_RE.search(p.path)
    return m.group(1) if m else None


def product_url(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}"


# ---- signed tokens (no token storage needed) --------------------------------
def make_token(purpose: str, data: dict) -> str:
    return URLSafeTimedSerializer(_secret(), salt=purpose).dumps(data)


def read_token(purpose: str, token: str, max_age: int | None = None) -> dict | None:
    try:
        return URLSafeTimedSerializer(_secret(), salt=purpose).loads(token, max_age=max_age)
    except BadSignature:
        return None


# ---- HTTP hardening ----------------------------------------------------------
def add_security_headers(resp):
    h = resp.headers
    h["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), interest-cohort=()"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    return resp
