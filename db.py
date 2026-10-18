"""SQLite storage. One row per product (shared by all users) + one per tracker."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

MAX_FAILS = 20  # consecutive failed fetches before a product is paused


def _path() -> str:
    return os.environ.get("DATABASE_PATH", "data/tracker.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get():
    c = sqlite3.connect(_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
    with get() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS products(
                asin TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT,
                last_price REAL, last_checked TEXT, fail_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS trackers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_enc TEXT NOT NULL, email_hash TEXT NOT NULL,
                asin TEXT NOT NULL REFERENCES products(asin) ON DELETE CASCADE,
                min_price REAL NOT NULL, max_price REAL NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                last_notified_price REAL, created_at TEXT NOT NULL,
                UNIQUE(email_hash, asin));
            CREATE TABLE IF NOT EXISTS price_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, asin TEXT NOT NULL,
                price REAL NOT NULL, ts TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_hist ON price_history(asin, ts);
            """
        )
    try:
        os.chmod(_path(), 0o600)
    except OSError:
        pass


# ---- trackers ---------------------------------------------------------------
def count_for_email(h: str) -> int:
    with get() as c:
        return c.execute("SELECT COUNT(*) FROM trackers WHERE email_hash=?", (h,)).fetchone()[0]


def total_trackers() -> int:
    with get() as c:
        return c.execute("SELECT COUNT(*) FROM trackers").fetchone()[0]


def find_tracker(h: str, asin: str):
    with get() as c:
        return c.execute("SELECT * FROM trackers WHERE email_hash=? AND asin=?", (h, asin)).fetchone()


def create_tracker(email_enc, h, asin, url, lo, hi) -> int:
    with get() as c:
        c.execute("INSERT OR IGNORE INTO products(asin,url) VALUES(?,?)", (asin, url))
        cur = c.execute(
            "INSERT INTO trackers(email_enc,email_hash,asin,min_price,max_price,created_at) VALUES(?,?,?,?,?,?)",
            (email_enc, h, asin, lo, hi, _now()),
        )
        return cur.lastrowid


def update_unverified(tid, lo, hi):
    with get() as c:
        c.execute("UPDATE trackers SET min_price=?, max_price=? WHERE id=? AND verified=0", (lo, hi, tid))


def get_tracker(tid: int):
    with get() as c:
        return c.execute(
            "SELECT t.*, p.title, p.url FROM trackers t JOIN products p USING(asin) WHERE t.id=?", (tid,)
        ).fetchone()


def verify(tid: int) -> bool:
    with get() as c:
        return c.execute("UPDATE trackers SET verified=1 WHERE id=?", (tid,)).rowcount > 0


def delete_tracker(tid: int):
    with get() as c:
        c.execute("DELETE FROM trackers WHERE id=?", (tid,))
    cleanup_orphans()


def delete_all_for_hash(h: str):
    with get() as c:
        c.execute("DELETE FROM trackers WHERE email_hash=?", (h,))
    cleanup_orphans()


def cleanup_orphans():
    with get() as c:
        c.execute("DELETE FROM price_history WHERE asin NOT IN (SELECT asin FROM trackers)")
        c.execute("DELETE FROM products WHERE asin NOT IN (SELECT asin FROM trackers)")


def set_notified(tid: int, price):
    with get() as c:
        c.execute("UPDATE trackers SET last_notified_price=? WHERE id=?", (price, tid))


# ---- products ---------------------------------------------------------------
def asins_to_check() -> list[str]:
    with get() as c:
        rows = c.execute(
            "SELECT DISTINCT p.asin FROM products p JOIN trackers t USING(asin) "
            "WHERE t.verified=1 AND p.fail_count < ?", (MAX_FAILS,)
        ).fetchall()
    return [r["asin"] for r in rows]


def get_product(asin: str):
    with get() as c:
        return c.execute("SELECT * FROM products WHERE asin=?", (asin,)).fetchone()


def verified_trackers(asin: str):
    with get() as c:
        return c.execute("SELECT * FROM trackers WHERE asin=? AND verified=1", (asin,)).fetchall()


def save_price(asin: str, title, price: float):
    with get() as c:
        c.execute(
            "UPDATE products SET title=COALESCE(?,title), last_price=?, last_checked=?, fail_count=0 WHERE asin=?",
            (title, price, _now(), asin),
        )
        c.execute("INSERT INTO price_history(asin,price,ts) VALUES(?,?,?)", (asin, price, _now()))


def mark_failure(asin: str):
    with get() as c:
        c.execute("UPDATE products SET fail_count=fail_count+1, last_checked=? WHERE asin=?", (_now(), asin))


# ---- housekeeping (data minimisation) ---------------------------------------
def purge():
    old_unverified = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    old_history = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    with get() as c:
        c.execute("DELETE FROM trackers WHERE verified=0 AND created_at<?", (old_unverified,))
        c.execute("DELETE FROM price_history WHERE ts<?", (old_history,))
    cleanup_orphans()
