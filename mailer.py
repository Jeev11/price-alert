"""Outgoing mail from the site's own mailbox (Gmail SMTP + app password, free)."""
import html
import os
import re
import smtplib
import ssl
from email.message import EmailMessage


def _inr(x: float) -> str:
    return f"₹{x:,.2f}"


def _clean(s: str | None, n: int = 90) -> str:
    return re.sub(r"\s+", " ", s or "this product").strip()[:n]


def _send(msg: EmailMessage):
    user, pw = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg["From"] = f'{os.environ.get("MAIL_FROM_NAME", "Price Alert")} <{user}>'
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", 587)), timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


def _build(to, subject, text, body_html, unsub_url=None) -> EmailMessage:
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    if unsub_url:
        m["List-Unsubscribe"] = f"<{unsub_url}>"
    m.set_content(text)
    m.add_alternative(f"<div style='font-family:Arial,sans-serif;line-height:1.5;max-width:520px'>{body_html}</div>", subtype="html")
    return m


def send_verification(to, asin, lo, hi, verify_url, unsub_url):
    text = (
        f"Confirm your price alert.\n\nProduct: https://www.amazon.in/dp/{asin}\n"
        f"Alert range: {_inr(lo)} to {_inr(hi)}\n\nConfirm: {verify_url}\n(valid for 24 hours)\n\n"
        f"Didn't request this? Ignore this email, nothing will be stored after 24 hours, "
        f"or delete it now: {unsub_url}\n"
    )
    h = (
        f"<h2>Confirm your price alert</h2><p>Product: <a href='https://www.amazon.in/dp/{asin}'>amazon.in/dp/{asin}</a><br>"
        f"Alert range: <b>{_inr(lo)} to {_inr(hi)}</b></p><p><a href='{html.escape(verify_url)}'>Confirm and start tracking</a> (valid for 24 hours)</p>"
        f"<p style='color:#666;font-size:13px'>Didn't request this? Ignore it, unconfirmed requests are deleted after 24 hours.</p>"
    )
    _send(_build(to, "Confirm your price alert", text, h, unsub_url))


def send_alert(to, title, asin, price, ref, direction, lo, hi, unsub_url):
    t = _clean(title)
    if direction == "dropped":
        head, sub = f"Price dropped to {_inr(price)}", f"Down from {_inr(ref)}."
    elif direction == "increased":
        head, sub = f"Price rose to {_inr(price)}", f"Up from {_inr(ref)}."
    else:
        head, sub = f"Now {_inr(price)}", "This is inside your range."
    link = f"https://www.amazon.in/dp/{asin}"
    text = f"{head}\n{t}\n{sub} Your range: {_inr(lo)} to {_inr(hi)}.\n\nView: {link}\n\nStop alerts / delete my data: {unsub_url}\n"
    h = (
        f"<h2>{html.escape(head)}</h2><p><b>{html.escape(t)}</b></p><p>{html.escape(sub)} Your range: {_inr(lo)} to {_inr(hi)}.</p>"
        f"<p><a href='{link}'>View on Amazon.in</a></p><p style='color:#666;font-size:13px'><a href='{html.escape(unsub_url)}'>Stop alerts and delete my data</a></p>"
    )
    _send(_build(to, f"{head}: {t[:60]}", text, h, unsub_url))
