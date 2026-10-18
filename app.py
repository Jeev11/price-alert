import logging
import os
import threading
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
import db, mailer, security, worker  # noqa: E402

MAX_PER_EMAIL = int(os.environ.get("MAX_PER_EMAIL", 3))
MAX_TOTAL = int(os.environ.get("MAX_TOTAL_TRACKERS", 2000))
MAX_PRICE = 10_000_000
OK_MSG = "Check your inbox: we sent a confirmation email. Tracking starts once you confirm."


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=security._secret(),
        MAX_CONTENT_LENGTH=8 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
        WTF_CSRF_ENABLED=not testing,
        TESTING=testing,
    )
    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    CSRFProtect(app)
    limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"], storage_uri="memory://")
    db.init()

    @app.after_request
    def harden(resp):
        if request.path != "/" and not request.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return security.add_security_headers(resp)

    @app.get("/")
    def index():
        return render_template("index.html", max_per_email=MAX_PER_EMAIL)

    @app.get("/healthz")
    @limiter.exempt
    def healthz():
        return "ok"

    @app.post("/api/track")
    @limiter.limit("5 per hour; 15 per day")
    def track():
        data = request.get_json(silent=True) or {}
        if data.get("website"):  # honeypot: bots fill hidden fields
            return jsonify(ok=True, message=OK_MSG)

        email = str(data.get("email", "")).strip().lower()
        asin = security.parse_amazon_url(str(data.get("url", "")))
        try:
            lo, hi = round(float(data.get("min_price")), 2), round(float(data.get("max_price")), 2)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Enter your price range as numbers."), 400

        if not security.valid_email(email):
            return jsonify(ok=False, error="Enter a valid email address."), 400
        if not asin:
            return jsonify(ok=False, error="Paste a full amazon.in product link (it contains /dp/ followed by a 10-character code)."), 400
        if not (0 <= lo <= hi <= MAX_PRICE):
            return jsonify(ok=False, error="The lowest price must be 0 or more and not above the highest price."), 400

        h = security.email_hash(email)
        existing = db.find_tracker(h, asin)
        if existing:
            if not existing["verified"]:
                db.update_unverified(existing["id"], lo, hi)
                _send_verification(email, existing["id"], h, asin, lo, hi)
            # verified duplicates: answer identically and change nothing, so nobody
            # can edit or probe another person's alert by typing their address.
            return jsonify(ok=True, message=OK_MSG)

        if db.count_for_email(h) >= MAX_PER_EMAIL:
            return jsonify(ok=False, error=f"Limit reached: {MAX_PER_EMAIL} products per email. Delete one with the link in your emails first."), 400
        if db.total_trackers() >= MAX_TOTAL:
            return jsonify(ok=False, error="The service is full right now. Try again later."), 503

        tid = db.create_tracker(security.encrypt_email(email), h, asin, security.product_url(asin), lo, hi)
        _send_verification(email, tid, h, asin, lo, hi)
        return jsonify(ok=True, message=OK_MSG)

    def _send_verification(email, tid, h, asin, lo, hi):
        base = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
        v = f"{base}/verify/{security.make_token('verify', {'t': tid})}"
        u = f"{base}/unsubscribe/{security.make_token('unsub', {'t': tid, 'h': h})}"
        try:
            mailer.send_verification(email, asin, lo, hi, v, u)
        except Exception as e:
            app.logger.error("verification mail failed: %s", type(e).__name__)

    @app.get("/verify/<token>")
    def verify(token):
        data = security.read_token("verify", token, max_age=86400)
        t = db.get_tracker(data["t"]) if data else None
        if not t:
            return render_template("message.html", title="Link expired", text="This confirmation link is invalid or has expired. Submit the form again to get a new one."), 400
        db.verify(t["id"])
        if not app.config["TESTING"]:
            threading.Thread(target=worker.check_asin, args=(t["asin"],), daemon=True).start()
        return render_template("message.html", title="Tracking started", text="You're confirmed. We'll email you whenever the price changes and lands inside your range.")

    @app.route("/unsubscribe/<token>", methods=["GET", "POST"])
    def unsubscribe(token):
        data = security.read_token("unsub", token)
        if not data:
            return render_template("message.html", title="Link not valid", text="This link is invalid."), 400
        if request.method == "GET":  # confirm page, so mail scanners that prefetch links can't delete anything
            return render_template("unsubscribe.html", token=token)
        if request.form.get("all"):
            db.delete_all_for_hash(data["h"])
            text = "All of your alerts and your email address have been deleted."
        else:
            db.delete_tracker(data["t"])
            text = "This alert has been deleted. If it was your only one, your email address is gone too."
        return render_template("message.html", title="Done", text=text)

    @app.errorhandler(429)
    def too_many(_):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="Too many requests. Please try again later."), 429
        return render_template("message.html", title="Slow down", text="Too many requests. Please try again later."), 429

    @app.errorhandler(404)
    def nf(_):
        return render_template("message.html", title="Not found", text="That page doesn't exist."), 404

    if not testing and os.environ.get("RUN_SCHEDULER", "1") == "1":
        from apscheduler.schedulers.background import BackgroundScheduler

        s = BackgroundScheduler(daemon=True)
        s.add_job(worker.run_check, "interval", minutes=int(os.environ.get("CHECK_INTERVAL_MINUTES", 120)),
                  max_instances=1, coalesce=True, next_run_time=datetime.now() + timedelta(seconds=30))
        s.add_job(db.purge, "interval", hours=6)
        s.start()
    return app


logging.basicConfig(level=logging.INFO)
if os.environ.get("SECRET_KEY"):
    app = create_app()

if __name__ == "__main__":
    create_app().run(debug=False)
