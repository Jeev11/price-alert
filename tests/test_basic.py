import os, sys, tempfile, unittest
from unittest import mock

os.environ.update(
    SECRET_KEY="x" * 40,
    ENCRYPTION_KEY="Pl3aseReplaceWithRealFernetKey_0000000000000=",
    DATABASE_PATH=os.path.join(tempfile.mkdtemp(), "t.db"),
    BASE_URL="http://test", RUN_SCHEDULER="0", SMTP_USER="site@example.com", SMTP_PASSWORD="x",
)
from cryptography.fernet import Fernet
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as appmod, db, scraper, security, worker  # noqa: E402


class Basics(unittest.TestCase):
    def test_url_validation(self):
        ok = "https://www.amazon.in/Some-Item/dp/B0ABCDEFGH/ref=sr_1_1?x=1"
        self.assertEqual(security.parse_amazon_url(ok), "B0ABCDEFGH")
        for bad in ["https://evil.com/dp/B0ABCDEFGH", "http://127.0.0.1/dp/B0ABCDEFGH",
                    "https://amazon.in.evil.com/dp/B0ABCDEFGH", "https://amzn.in/d/abc",
                    "https://www.amazon.in/", "file:///etc/passwd", "https://u:p@www.amazon.in/dp/B0ABCDEFGH"]:
            self.assertIsNone(security.parse_amazon_url(bad), bad)

    def test_price_parse(self):
        self.assertEqual(scraper.parse_price("₹1,299.00"), 1299.0)
        self.assertEqual(scraper.parse_price("₹ 54,999"), 54999.0)

    def test_encryption_roundtrip(self):
        enc = security.encrypt_email("a@b.com")
        self.assertNotIn("a@b.com", enc)
        self.assertEqual(security.decrypt_email(enc), "a@b.com")


class Flow(unittest.TestCase):
    def setUp(self):
        self.app = appmod.create_app(testing=True)
        self.c = self.app.test_client()
        self.sent = []
        p1 = mock.patch.object(appmod.mailer, "_send", lambda m: self.sent.append(m))
        p1.start(); self.addCleanup(p1.stop)

    def test_end_to_end(self):
        body = dict(email="U@Example.com", url="https://www.amazon.in/x/dp/B0ABCDEFGH", min_price=1000, max_price=2000)
        r = self.c.post("/api/track", json=body)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(len(self.sent), 1)
        # honeypot + bad input
        self.assertEqual(self.c.post("/api/track", json={**body, "website": "x"}).status_code, 200)
        self.assertEqual(self.c.post("/api/track", json={**body, "url": "https://evil.com/dp/B0ABCDEFGH"}).status_code, 400)
        self.assertEqual(self.c.post("/api/track", json={**body, "min_price": 5, "max_price": 1}).status_code, 400)

        # confirm via signed link
        text = self.sent[0].get_body(("plain",)).get_content()
        link = [w for w in text.split() if "/verify/" in w][0]
        self.assertEqual(self.c.get(link.replace("http://test", "")).status_code, 200)
        self.assertEqual(self.c.get("/verify/garbage").status_code, 400)

        sent_before = len(self.sent)
        seq = [(1500, 1), (1500, 0), (1400, 1), (1700, 1), (2500, 0), (1800, 1)]  # (price, emails expected)
        for price, n in seq:
            with mock.patch.object(scraper, "fetch", return_value=("Test Phone", float(price))):
                worker._check("B0ABCDEFGH")
            self.assertEqual(len(self.sent) - sent_before, n, f"price {price}")
            sent_before = len(self.sent)

        subjects = [m["Subject"] for m in self.sent[1:]]
        self.assertIn("dropped", subjects[1].lower())
        self.assertIn("rose", subjects[2].lower())

        # unsubscribe deletes everything
        tr = db.get_tracker(1)
        tok = security.make_token("unsub", {"t": tr["id"], "h": tr["email_hash"]})
        self.assertEqual(self.c.get(f"/unsubscribe/{tok}").status_code, 200)
        self.c.post(f"/unsubscribe/{tok}", data={"all": "1"})
        self.assertEqual(db.total_trackers(), 0)
        self.assertIsNone(db.get_product("B0ABCDEFGH"))

    def test_headers(self):
        r = self.c.get("/")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
