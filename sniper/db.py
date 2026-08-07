import sqlite3
import threading
import time
from . import config

_lock = threading.Lock()
_src_cache = {"ts": 0, "rows": []}


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sources(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, topic_id INTEGER DEFAULT 0,
            name TEXT, mcap_max REAL, enabled INTEGER DEFAULT 1)""")
        c.execute("""CREATE TABLE IF NOT EXISTS calls(
            ca TEXT PRIMARY KEY, source_id INTEGER, ts INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS positions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ca TEXT, symbol TEXT, pair_address TEXT, chain_slug TEXT,
            pool_type TEXT, fee INTEGER,
            entry_price_usd REAL, tokens_total REAL, tokens_left REAL,
            eth_spent REAL, usd_spent REAL,
            realized_eth REAL DEFAULT 0,
            tp1_done INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            honeypot INTEGER DEFAULT 0,
            source_name TEXT, ts INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ca TEXT, symbol TEXT, role TEXT, action TEXT,
            conviction INTEGER, size_mult REAL,
            reason TEXT, risks TEXT, outcome TEXT, ts INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS blacklist(
            caller_id INTEGER PRIMARY KEY, caller_name TEXT,
            reason TEXT, ts INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS lessons(
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, ts INTEGER)""")
        for col in ("tp1_x REAL", "tp2_x REAL", "sl_x REAL",
                    "caller_id INTEGER", "caller_name TEXT", "buy_tx TEXT",
                    "peak_price REAL", "closed_at INTEGER"):
            try:
                c.execute(f"ALTER TABLE positions ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        for col in ("sender_id INTEGER", "sender_name TEXT"):
            try:
                c.execute(f"ALTER TABLE calls ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        for k, v in config.SETTING_DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        # seed source awal kalau tabel kosong
        if not c.execute("SELECT 1 FROM sources LIMIT 1").fetchone() and config.SEED_GROUP_ID:
            c.execute("INSERT INTO sources(chat_id,topic_id,name,mcap_max) VALUES(?,?,?,?)",
                      (config.SEED_GROUP_ID, config.SEED_TOPIC_DEGEN, "Robinhood Degen Room", 30000))
            c.execute("INSERT INTO sources(chat_id,topic_id,name,mcap_max) VALUES(?,?,?,?)",
                      (config.SEED_GROUP_ID, config.SEED_TOPIC_MEMBER, "RH Member Call", 10000))


# ---------- settings ----------
def get(key, cast=float):
    with _lock, _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    v = r["value"] if r else config.SETTING_DEFAULTS.get(key, "0")
    return cast(v)


def set_(key, value):
    with _lock, _conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def paused() -> bool:
    return get("paused", str) == "1"


def realized_pnl_window(since_ts: int) -> float:
    """Net realized PnL (ETH) dari posisi yang closed_at>=since_ts.
    Positif = profit, negatif = rugi. Dipakai circuit breaker."""
    with _lock, _conn() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(realized_eth),0) AS eth_back, "
            "COALESCE(SUM(eth_spent),0) AS eth_cost "
            "FROM positions WHERE closed_at>=?", (since_ts,)).fetchone()
        return r["eth_back"] - r["eth_cost"]


# ---------- sources ----------
def _invalidate():
    _src_cache["ts"] = 0


def sources(only_enabled=False):
    now = time.time()
    if now - _src_cache["ts"] > 5:
        with _lock, _conn() as c:
            _src_cache["rows"] = [dict(r) for r in c.execute("SELECT * FROM sources ORDER BY id")]
        _src_cache["ts"] = now
    rows = _src_cache["rows"]
    return [s for s in rows if s["enabled"]] if only_enabled else rows


def match_source(chat_id: int, topic_id: int):
    """Source enabled yang cocok dengan chat+topic. topic_id=0 di source = semua topic."""
    for s in sources(only_enabled=True):
        if s["chat_id"] == chat_id and (s["topic_id"] == 0 or s["topic_id"] == topic_id):
            return s
    return None


def add_source(chat_id, topic_id, name, mcap_max):
    with _lock, _conn() as c:
        c.execute("INSERT INTO sources(chat_id,topic_id,name,mcap_max) VALUES(?,?,?,?)",
                  (chat_id, topic_id, name, mcap_max))
    _invalidate()


def toggle_source(sid):
    with _lock, _conn() as c:
        c.execute("UPDATE sources SET enabled = 1 - enabled WHERE id=?", (sid,))
    _invalidate()


def del_source(sid):
    with _lock, _conn() as c:
        c.execute("DELETE FROM sources WHERE id=?", (sid,))
    _invalidate()


# ---------- calls / dedupe ----------
def seen_call(ca):
    with _lock, _conn() as c:
        return c.execute("SELECT 1 FROM calls WHERE ca=?", (ca.lower(),)).fetchone() is not None


def record_call(ca, source_id, sender_id=None, sender_name=None):
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO calls(ca,source_id,sender_id,sender_name,ts) VALUES(?,?,?,?,?)",
                  (ca.lower(), source_id, sender_id, sender_name, int(time.time())))


# ---------- positions ----------
def open_position(**kw):
    kw.setdefault("caller_id", None)
    kw.setdefault("caller_name", None)
    kw.setdefault("buy_tx", None)
    with _lock, _conn() as c:
        c.execute("""INSERT INTO positions
            (ca,symbol,pair_address,chain_slug,pool_type,fee,entry_price_usd,
             tokens_total,tokens_left,eth_spent,usd_spent,honeypot,source_name,
             caller_id,caller_name,buy_tx,ts)
            VALUES(:ca,:symbol,:pair_address,:chain_slug,:pool_type,:fee,:entry_price_usd,
                   :tokens_total,:tokens_left,:eth_spent,:usd_spent,:honeypot,:source_name,
                   :caller_id,:caller_name,:buy_tx,:ts)""",
            {**kw, "ts": int(time.time())})


def open_positions():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM positions WHERE status='open'")]


def position(pid):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def update_position(pid, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE positions SET {sets} WHERE id=?", (*fields.values(), pid))


def add_realized(pid, eth_out):
    with _lock, _conn() as c:
        c.execute("UPDATE positions SET realized_eth = realized_eth + ? WHERE id=?", (eth_out, pid))


def stats():
    with _lock, _conn() as c:
        row = lambda q: c.execute(q).fetchone()[0]
        return {
            "calls":   row("SELECT COUNT(*) FROM calls"),
            "buys":    row("SELECT COUNT(*) FROM positions"),
            "open":    row("SELECT COUNT(*) FROM positions WHERE status='open'"),
            "tp2":     row("SELECT COUNT(*) FROM positions WHERE status='closed_tp2'"),
            "sl":      row("SELECT COUNT(*) FROM positions WHERE status='closed_sl'"),
            "manual":  row("SELECT COUNT(*) FROM positions WHERE status='closed_manual'"),
            "rug":     row("SELECT COUNT(*) FROM positions WHERE status='closed_rug'"),
            "trail":   row("SELECT COUNT(*) FROM positions WHERE status='closed_trail'"),
            "tp1full": row("SELECT COUNT(*) FROM positions WHERE status='closed_tp1'"),
            "empty":   row("SELECT COUNT(*) FROM positions WHERE status='closed_empty'"),
            "spent":   row("SELECT COALESCE(SUM(usd_spent),0) FROM positions"),
            "eth_out": row("SELECT COALESCE(SUM(realized_eth),0) FROM positions"),
            "eth_in":  row("SELECT COALESCE(SUM(eth_spent),0) FROM positions"),
        }


# ---------- agent: decisions & lessons ----------
def log_decision(**kw):
    with _lock, _conn() as c:
        c.execute("""INSERT INTO decisions(ca,symbol,role,action,conviction,size_mult,reason,risks,ts)
            VALUES(:ca,:symbol,:role,:action,:conviction,:size_mult,:reason,:risks,:ts)""",
            {**kw, "ts": int(time.time())})


def recent_decisions(n=5, role=None):
    q = "SELECT * FROM decisions"
    if role:
        q += f" WHERE role='{role}'"
    q += " ORDER BY id DESC LIMIT ?"
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q, (n,))]


def mark_decision_outcome(ca, outcome):
    with _lock, _conn() as c:
        c.execute("""UPDATE decisions SET outcome=? WHERE id=(
            SELECT id FROM decisions WHERE ca=? AND role='screener' ORDER BY id DESC LIMIT 1)""",
            (outcome, ca.lower()))


def add_lesson(text):
    with _lock, _conn() as c:
        c.execute("INSERT INTO lessons(text,ts) VALUES(?,?)", (text, int(time.time())))


def recent_lessons(n=5):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM lessons ORDER BY id DESC LIMIT ?", (n,))]


def source_stats(name):
    with _lock, _conn() as c:
        r = c.execute("""SELECT COUNT(*) AS buys,
            SUM(status='closed_tp2') AS full_tp,
            SUM(tp1_done) AS hit_tp1,
            SUM(status='closed_sl') AS stopped
            FROM positions WHERE source_name=?""", (name,)).fetchone()
        return dict(r)


def closed_positions_with_decisions(n=20):
    with _lock, _conn() as c:
        rows = c.execute("""SELECT p.symbol, p.status, p.usd_spent, p.realized_eth,
            p.eth_spent, p.tp1_done, p.source_name,
            (SELECT reason FROM decisions d WHERE d.ca=p.ca AND d.role='screener'
             ORDER BY d.id DESC LIMIT 1) AS entry_reason,
            (SELECT conviction FROM decisions d WHERE d.ca=p.ca AND d.role='screener'
             ORDER BY d.id DESC LIMIT 1) AS conviction
            FROM positions p WHERE p.status LIKE 'closed%'
            ORDER BY p.id DESC LIMIT ?""", (n,)).fetchall()
        return [dict(r) for r in rows]


# ---------- caller scoring ----------
def caller_stats(caller_id):
    if not caller_id:
        return None
    with _lock, _conn() as c:
        r = c.execute("""SELECT COUNT(*) AS calls_bought,
            SUM(status='closed_tp2') AS full_tp,
            SUM(tp1_done) AS hit_tp1,
            SUM(status='closed_sl') AS stopped
            FROM positions WHERE caller_id=?""", (caller_id,)).fetchone()
        return dict(r)


def top_callers(n=10):
    with _lock, _conn() as c:
        rows = c.execute("""SELECT caller_name,
            COUNT(*) AS buys,
            SUM(tp1_done) AS hit_tp1,
            SUM(status='closed_tp2') AS full_tp,
            SUM(status='closed_sl') AS stopped
            FROM positions WHERE caller_id IS NOT NULL
            GROUP BY caller_id
            HAVING buys >= 1
            ORDER BY (CAST(SUM(tp1_done) AS REAL) / COUNT(*)) DESC, buys DESC
            LIMIT ?""", (n,)).fetchall()
        return [dict(r) for r in rows]


# ---------- rekap harian ----------
def recap_data(since_ts: int):
    with _lock, _conn() as c:
        row = lambda q, a=(): c.execute(q, a).fetchone()
        calls = row("SELECT COUNT(*) FROM calls WHERE ts>=?", (since_ts,))[0]
        buys = row("SELECT COUNT(*), COALESCE(SUM(usd_spent),0), COALESCE(SUM(eth_spent),0) "
                   "FROM positions WHERE ts>=?", (since_ts,))
        closed = [dict(r) for r in c.execute(
            "SELECT status, COUNT(*) AS n, COALESCE(SUM(realized_eth),0) AS eth_in, "
            "COALESCE(SUM(eth_spent),0) AS eth_out FROM positions "
            "WHERE closed_at>=? GROUP BY status", (since_ts,))]
        open_n = row("SELECT COUNT(*) FROM positions WHERE status='open'")[0]
        top = row("""SELECT caller_name, COUNT(*) AS n, SUM(tp1_done) AS tp1
            FROM positions WHERE ts>=? AND caller_id IS NOT NULL
            GROUP BY caller_id ORDER BY tp1 DESC, n DESC LIMIT 1""", (since_ts,))
        return {
            "calls": calls,
            "buys": buys[0], "usd_spent": buys[1], "eth_spent_new": buys[2],
            "closed": closed, "open": open_n,
            "top_caller": dict(top) if top and top["caller_name"] else None,
        }


# ---------- blacklist caller (kekuasaan Mata) ----------
def is_blacklisted(caller_id):
    if not caller_id:
        return False
    with _lock, _conn() as c:
        return c.execute("SELECT 1 FROM blacklist WHERE caller_id=?", (caller_id,)).fetchone() is not None


def add_blacklist(caller_id, caller_name, reason):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO blacklist(caller_id,caller_name,reason,ts) VALUES(?,?,?,?)",
                  (caller_id, caller_name, reason, int(time.time())))


def remove_blacklist(caller_id):
    with _lock, _conn() as c:
        c.execute("DELETE FROM blacklist WHERE caller_id=?", (caller_id,))


def list_blacklist():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM blacklist ORDER BY ts DESC")]
