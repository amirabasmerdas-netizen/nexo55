import psycopg2
import psycopg2.extras
import psycopg2.pool
import hashlib
import os
import datetime
import threading
import time as _time
from config import DATABASE_URL


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 Connection Pooling بهینه‌شده
# ══════════════════════════════════════════════════════════════════════════════
_connection_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                try:
                    _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                        minconn=5,
                        maxconn=20,
                        dsn=DATABASE_URL,
                        connect_timeout=3,
                        options="-c statement_timeout=5000 -c idle_in_transaction_session_timeout=10000"
                    )
                    print("✅ Connection pool ایجاد شد (min=5, max=20)")
                except Exception as e:
                    print(f"❌ خطا در ایجاد pool: {e}")
                    raise
    return _connection_pool


def get_conn():
    try:
        return _get_pool().getconn()
    except Exception:
        return psycopg2.connect(DATABASE_URL, connect_timeout=3)


def release_conn(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 Multi-Layer Cache System
# ══════════════════════════════════════════════════════════════════════════════
class MultiLayerCache:
    def __init__(self, max_size=5000, ttl=120):
        self._cache = {}
        self._timestamps = {}
        self._access_count = {}
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()
    
    def get(self, key, default=None):
        now = _time.time()
        with self._lock:
            if key in self._cache:
                if now - self._timestamps[key] < self._ttl:
                    self._access_count[key] = self._access_count.get(key, 0) + 1
                    return self._cache[key]
                else:
                    del self._cache[key]
                    del self._timestamps[key]
                    self._access_count.pop(key, None)
        return default
    
    def set(self, key, value, ttl=None):
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = _time.time()
            self._access_count[key] = self._access_count.get(key, 0) + 1
            
            while len(self._cache) > self._max_size:
                min_key = min(self._access_count, key=self._access_count.get)
                del self._cache[min_key]
                del self._timestamps[min_key]
                del self._access_count[min_key]
    
    def invalidate(self, pattern=None):
        with self._lock:
            if pattern is None:
                self._cache.clear()
                self._timestamps.clear()
                self._access_count.clear()
            else:
                keys_to_del = [k for k in list(self._cache.keys()) if pattern in k]
                for k in keys_to_del:
                    self._cache.pop(k, None)
                    self._timestamps.pop(k, None)
                    self._access_count.pop(k, None)


settings_cache = MultiLayerCache(max_size=3000, ttl=120)
account_cache = MultiLayerCache(max_size=1000, ttl=600)
list_cache = MultiLayerCache(max_size=2000, ttl=180)
token_cache = MultiLayerCache(max_size=1000, ttl=60)


# ══════════════════════════════════════════════════════════════════════════════
# init_db - با ALTER TABLE برای ستون‌های جدید
# ══════════════════════════════════════════════════════════════════════════════
def init_db():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ─── حساب‌های پنل ────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, telegram_user_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ─── چنل‌های اجباری ──────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS forced_channels (
            id SERIAL PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ─── تنظیمات ─────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            owner_id BIGINT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (owner_id, key))""")

        # ─── دشمن ─────────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS enemies (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            username TEXT, name TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (owner_id, user_id))""")

        # ─── دوست ────────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS friends (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            username TEXT, name TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (owner_id, user_id))""")

        # ─── سایلنت چت ────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS silent_chats (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (owner_id, chat_id))""")

        # ─── سایلنت کاربر ─────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS silent_users (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (owner_id, user_id))""")

        # ─── پیام‌های ذخیره‌شده ────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS saved_messages (
            owner_id BIGINT NOT NULL, slot INTEGER NOT NULL, content TEXT, media_path TEXT,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (owner_id, slot))""")

        # ─── پیام‌های حذف‌شده ─────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS deleted_messages (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, chat_id BIGINT, sender_id BIGINT,
            sender_name TEXT, message TEXT, media_type TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ─── پیام‌های زمان‌بندی‌شده ───────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS scheduled_messages (
            id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
            message TEXT NOT NULL, send_at TIMESTAMP NOT NULL, sent INTEGER DEFAULT 0)""")

        # ─── الماس‌ها ────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS tokens (
            owner_id BIGINT PRIMARY KEY, balance INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT NULL, total_earned INTEGER DEFAULT 0)""")

        # ─── رفرال‌ها ──────────────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY, referrer_owner_id BIGINT NOT NULL,
            referred_tg_id BIGINT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ─── چالش‌های جام جهانی ──────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS world_cup_challenges (
            id SERIAL PRIMARY KEY, team1 TEXT NOT NULL, team2 TEXT NOT NULL,
            match_time TEXT NOT NULL, bet_amount INTEGER NOT NULL,
            winner_team TEXT DEFAULT NULL, status TEXT DEFAULT 'active',
            message_id BIGINT DEFAULT NULL, chat_id BIGINT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        c.execute("""CREATE TABLE IF NOT EXISTS world_cup_bets (
            id SERIAL PRIMARY KEY, challenge_id BIGINT NOT NULL,
            user_tg_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
            team_choice TEXT NOT NULL, bet_amount INTEGER NOT NULL,
            result TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (challenge_id, user_tg_id))""")

        # ─── قرعه‌کشی‌ها - با ستون entry_fee جدید ─────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS lotteries (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            creator_tg_id BIGINT NOT NULL,
            prize_amount INTEGER NOT NULL,
            entry_fee INTEGER DEFAULT 1,
            end_time TIMESTAMP NOT NULL,
            winner_tg_id BIGINT DEFAULT NULL,
            status TEXT DEFAULT 'active',
            message_id BIGINT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ✅ اضافه کردن ستون entry_fee اگر جدول قبلاً وجود داشته باشد
        try:
            c.execute("""ALTER TABLE lotteries 
                         ADD COLUMN IF NOT EXISTS entry_fee INTEGER DEFAULT 1""")
        except Exception:
            pass

        c.execute("""CREATE TABLE IF NOT EXISTS lottery_participants (
            id SERIAL PRIMARY KEY, lottery_id BIGINT NOT NULL, user_tg_id BIGINT NOT NULL,
            owner_id BIGINT NOT NULL, bet_amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (lottery_id, user_tg_id))""")

        # ─── تراکنش‌های الماس ────────────────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS diamond_transactions (
            id SERIAL PRIMARY KEY, from_owner_id BIGINT NOT NULL, to_owner_id BIGINT NOT NULL,
            amount INTEGER NOT NULL, type TEXT NOT NULL, description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

        # ✅ Index‌های بهینه‌شده
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_accounts_tg ON accounts(telegram_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(username)",
            "CREATE INDEX IF NOT EXISTS idx_settings_owner_key ON settings(owner_id, key)",
            "CREATE INDEX IF NOT EXISTS idx_tokens_owner ON tokens(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_friends_owner ON friends(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_enemies_owner ON enemies(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_wc_bets_challenge ON world_cup_bets(challenge_id)",
            "CREATE INDEX IF NOT EXISTS idx_lottery_part ON lottery_participants(lottery_id)",
            "CREATE INDEX IF NOT EXISTS idx_referrals_ref ON referrals(referrer_owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_deleted_owner_time ON deleted_messages(owner_id, deleted_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_pending ON scheduled_messages(owner_id, sent, send_at)",
            "CREATE INDEX IF NOT EXISTS idx_forced_channels_username ON forced_channels(username)",
            "CREATE INDEX IF NOT EXISTS idx_world_cup_challenges_status ON world_cup_challenges(status)",
            "CREATE INDEX IF NOT EXISTS idx_lotteries_status ON lotteries(status, end_time)",
        ]
        
        for idx_sql in indexes:
            try:
                c.execute(idx_sql)
            except Exception:
                pass

        conn.commit()
    finally:
        release_conn(conn)


# ════════════════════════════════════════════════════════════════════════════
# مدیریت حساب
# ══════════════════════════════════════════════════════════════════════════════
def create_account(username: str, password: str):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            "INSERT INTO accounts (username, password_hash) VALUES (%s, %s) RETURNING id",
            (username.strip(), _hash_pw(password)),
        )
        new_id = c.fetchone()["id"]
        conn.commit()
        _init_tokens_by_id(new_id)
        return new_id
    except Exception:
        return None
    finally:
        release_conn(conn)


def _init_tokens_by_id(owner_id: int):
    from config import WELCOME_TOKENS
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO tokens (owner_id, balance, total_earned) VALUES (%s, %s, %s) ON CONFLICT (owner_id) DO NOTHING",
            (owner_id, WELCOME_TOKENS, WELCOME_TOKENS),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        release_conn(conn)


def verify_account(username: str, password: str):
    cache_key = f"verify:{username}:{_hash_pw(password)}"
    cached = account_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id FROM accounts WHERE username = %s AND password_hash = %s",
                  (username.strip(), _hash_pw(password)))
        row = c.fetchone()
        result = row["id"] if row else None
        if result:
            account_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


def get_account(owner_id: int):
    cache_key = f"account:{owner_id}"
    cached = account_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, username, telegram_user_id, created_at FROM accounts WHERE id = %s", (owner_id,))
        row = c.fetchone()
        result = dict(row) if row else None
        if result:
            account_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


def get_account_by_username(username: str):
    cache_key = f"account_user:{username}"
    cached = account_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, username, telegram_user_id, created_at FROM accounts WHERE username = %s",
                  (username.strip(),))
        row = c.fetchone()
        result = dict(row) if row else None
        if result:
            account_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


def get_account_by_tg_id(tg_id: int):
    cache_key = f"account_tg:{tg_id}"
    cached = account_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, username, telegram_user_id, created_at FROM accounts WHERE telegram_user_id = %s",
                  (tg_id,))
        row = c.fetchone()
        result = dict(row) if row else None
        if result:
            account_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


def get_all_accounts():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT id, username, created_at FROM accounts ORDER BY created_at")
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def account_exists():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT EXISTS(SELECT 1 FROM accounts LIMIT 1) as exists")
        return c.fetchone()["exists"]
    finally:
        release_conn(conn)


def save_telegram_user_id(owner_id: int, tg_user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE accounts SET telegram_user_id = %s WHERE id = %s", (tg_user_id, owner_id))
        conn.commit()
        account_cache.invalidate(f"account:{owner_id}")
    finally:
        release_conn(conn)


def get_telegram_id_by_owner(owner_id: int):
    cache_key = f"tg_id:{owner_id}"
    cached = account_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT telegram_user_id FROM accounts WHERE id = %s", (owner_id,))
        row = c.fetchone()
        result = row["telegram_user_id"] if row else None
        if result:
            account_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# تنظیمات
# ═════════════════════════════════════════════════════════════════════════════
SETTING_DEFAULTS = {
    "self_bot_active": "0", "secretary_active": "0", "anti_delete_active": "0",
    "anti_link_active": "0", "auto_seen_active": "0", "auto_reaction_active": "0",
    "private_lock_active": "0", "enemy_reply_active": "0", "auto_save_media": "0",
    "clock_name_active": "0", "clock_bio_active": "0", "selected_font": "0",
    "secretary_message": "در حال حاضر در دسترس نیستم.", "auto_reaction_emoji": "❤️",
    "spam_active": "0", "channel_save_active": "0", "spam_delay": "2",
    "session_data": "", "logged_in": "0",
}


def init_user_settings(owner_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        for key, value in SETTING_DEFAULTS.items():
            c.execute(
                "INSERT INTO settings (owner_id, key, value) VALUES (%s, %s, %s) ON CONFLICT (owner_id, key) DO NOTHING",
                (owner_id, key, value),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        release_conn(conn)
    _init_tokens_by_id(owner_id)


def get_setting(owner_id: int, key: str, default=None):
    cache_key = f"setting:{owner_id}:{key}"
    cached = settings_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT value FROM settings WHERE owner_id = %s AND key = %s", (owner_id, key))
        row = c.fetchone()
        value = row["value"] if row else SETTING_DEFAULTS.get(key, default)
        settings_cache.set(cache_key, value)
        return value
    finally:
        release_conn(conn)


def set_setting(owner_id: int, key: str, value):
    cache_key = f"setting:{owner_id}:{key}"
    
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO settings (owner_id, key, value) VALUES (%s, %s, %s)
                     ON CONFLICT (owner_id, key) DO UPDATE SET value = EXCLUDED.value""",
                  (owner_id, key, str(value)))
        conn.commit()
        settings_cache.set(cache_key, str(value))
    finally:
        release_conn(conn)


def toggle_setting(owner_id: int, key: str):
    current = get_setting(owner_id, key, "0")
    new_val = "0" if current == "1" else "1"
    set_setting(owner_id, key, new_val)
    return new_val == "1"


def get_all_logged_in_users():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT owner_id FROM settings WHERE key = 'logged_in' AND value = '1'")
        return [r["owner_id"] for r in c.fetchall()]
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# سیستم الماس
# ══════════════════════════════════════════════════════════════════════════════
def _ensure_tokens_row(conn, owner_id: int):
    c = conn.cursor()
    c.execute("INSERT INTO tokens (owner_id, balance, total_earned) VALUES (%s, 0, 0) ON CONFLICT (owner_id) DO NOTHING",
              (owner_id,))
    conn.commit()


def get_token_balance(owner_id: int) -> int:
    cache_key = f"token_balance:{owner_id}"
    cached = token_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT balance FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        result = row["balance"] if row else 0
        token_cache.set(cache_key, result)
        return result
    finally:
        release_conn(conn)


def add_tokens(owner_id: int, amount: int):
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor()
        c.execute("UPDATE tokens SET balance = balance + %s, total_earned = total_earned + %s WHERE owner_id = %s",
                  (amount, amount, owner_id))
        conn.commit()
        token_cache.invalidate(f"token_balance:{owner_id}")
    finally:
        release_conn(conn)


def deduct_tokens(owner_id: int, amount: int) -> bool:
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT balance FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        if not row or row["balance"] < amount:
            return False
        c2 = conn.cursor()
        c2.execute("UPDATE tokens SET balance = balance - %s WHERE owner_id = %s", (amount, owner_id))
        conn.commit()
        token_cache.invalidate(f"token_balance:{owner_id}")
        return True
    finally:
        release_conn(conn)


def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int) -> tuple:
    if amount <= 0:
        return False, " مقدار باید بزرگ‌تر از صفر باشد."
    if from_owner_id == to_owner_id:
        return False, "❌ نمی‌توانید به خودتان الماس انتقال دهید."
    
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, from_owner_id)
        _ensure_tokens_row(conn, to_owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        c.execute("SELECT balance FROM tokens WHERE owner_id = %s", (from_owner_id,))
        row = c.fetchone()
        if not row or row["balance"] < amount:
            return False, f"❌ موجودی کافی ندارید. موجودی: {row['balance'] if row else 0} الماس"
        
        c_upd = conn.cursor()
        c_upd.execute("UPDATE tokens SET balance = balance - %s WHERE owner_id = %s", (amount, from_owner_id))
        c_upd.execute("UPDATE tokens SET balance = balance + %s WHERE owner_id = %s", (amount, to_owner_id))
        c_upd.execute("""INSERT INTO diamond_transactions (from_owner_id, to_owner_id, amount, type, description)
                         VALUES (%s, %s, %s, 'transfer', 'انتقال الماس')""",
                      (from_owner_id, to_owner_id, amount))
        
        conn.commit()
        token_cache.invalidate(f"token_balance:{from_owner_id}")
        token_cache.invalidate(f"token_balance:{to_owner_id}")
        return True, f"✅ {amount} الماس با موفقیت انتقال یافت."
    except Exception as e:
        return False, f"❌ خطا در انتقال: {str(e)}"
    finally:
        release_conn(conn)


def claim_daily_token(owner_id: int):
    from config import DAILY_TOKEN_GIFT
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT last_daily FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        today = datetime.date.today().isoformat()
        if row and row["last_daily"] == today:
            return False, "⏰ امروز قبلاً هدیه روزانه دریافت کردید."
        
        c_upd = conn.cursor()
        c_upd.execute("""UPDATE tokens SET balance = balance + %s, total_earned = total_earned + %s, 
                         last_daily = %s WHERE owner_id = %s""",
                      (DAILY_TOKEN_GIFT, DAILY_TOKEN_GIFT, today, owner_id))
        conn.commit()
        token_cache.invalidate(f"token_balance:{owner_id}")
        return True, f" {DAILY_TOKEN_GIFT} الماس روزانه دریافت کردید!"
    finally:
        release_conn(conn)


def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    from config import REFERRAL_TOKENS
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT 1 FROM referrals WHERE referred_tg_id = %s", (referred_tg_id,))
        if c.fetchone():
            return False
        c.execute("SELECT 1 FROM accounts WHERE id = %s", (referrer_owner_id,))
        if not c.fetchone():
            return False
        
        c_ins = conn.cursor()
        c_ins.execute("INSERT INTO referrals (referrer_owner_id, referred_tg_id) VALUES (%s, %s)",
                      (referrer_owner_id, referred_tg_id))
        _ensure_tokens_row(conn, referrer_owner_id)
        c_ins.execute("UPDATE tokens SET balance = balance + %s, total_earned = total_earned + %s WHERE owner_id = %s",
                      (REFERRAL_TOKENS, REFERRAL_TOKENS, referrer_owner_id))
        conn.commit()
        token_cache.invalidate(f"token_balance:{referrer_owner_id}")
        return True
    except Exception:
        return False
    finally:
        release_conn(conn)


def get_referral_count(owner_id: int) -> int:
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT COUNT(*) as cnt FROM referrals WHERE referrer_owner_id = %s", (owner_id,))
        row = c.fetchone()
        return row["cnt"] if row else 0
    finally:
        release_conn(conn)


def get_token_stats(owner_id: int) -> dict:
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT balance, last_daily, total_earned FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        if not row:
            return {"balance": 0, "last_daily": None, "total_earned": 0}
        today = datetime.date.today().isoformat()
        can_claim = row["last_daily"] != today
        return {
            "balance": row["balance"],
            "last_daily": row["last_daily"],
            "total_earned": row["total_earned"],
            "can_claim_daily": can_claim,
        }
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# دشمن و دوست
# ══════════════════════════════════════════════════════════════════════════════
def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO enemies (owner_id, user_id, username, name) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (owner_id, user_id) DO UPDATE SET username=EXCLUDED.username, name=EXCLUDED.name""",
                  (owner_id, user_id, username, name))
        conn.commit()
        list_cache.invalidate(f"enemies:{owner_id}")
        return True
    except Exception:
        return False
    finally:
        release_conn(conn)


def remove_enemy(owner_id: int, user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM enemies WHERE owner_id = %s AND user_id = %s", (owner_id, user_id))
        affected = c.rowcount
        conn.commit()
        if affected > 0:
            list_cache.invalidate(f"enemies:{owner_id}")
        return affected > 0
    finally:
        release_conn(conn)


def get_enemies(owner_id: int):
    cache_key = f"enemies:{owner_id}"
    cached = list_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM enemies WHERE owner_id = %s ORDER BY added_at DESC", (owner_id,))
        rows = [dict(r) for r in c.fetchall()]
        list_cache.set(cache_key, rows)
        return rows
    finally:
        release_conn(conn)


def is_enemy(owner_id: int, user_id: int):
    enemies = get_enemies(owner_id)
    return any(e["user_id"] == user_id for e in enemies)


def clear_enemies(owner_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM enemies WHERE owner_id = %s", (owner_id,))
        conn.commit()
        list_cache.invalidate(f"enemies:{owner_id}")
    finally:
        release_conn(conn)


def add_friend(owner_id: int, user_id: int, username=None, name=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO friends (owner_id, user_id, username, name) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (owner_id, user_id) DO UPDATE SET username=EXCLUDED.username, name=EXCLUDED.name""",
                  (owner_id, user_id, username, name))
        conn.commit()
        list_cache.invalidate(f"friends:{owner_id}")
        return True
    except Exception:
        return False
    finally:
        release_conn(conn)


def remove_friend(owner_id: int, user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM friends WHERE owner_id = %s AND user_id = %s", (owner_id, user_id))
        affected = c.rowcount
        conn.commit()
        if affected > 0:
            list_cache.invalidate(f"friends:{owner_id}")
        return affected > 0
    finally:
        release_conn(conn)


def get_friends(owner_id: int):
    cache_key = f"friends:{owner_id}"
    cached = list_cache.get(cache_key)
    if cached is not None:
        return cached
    
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM friends WHERE owner_id = %s ORDER BY added_at DESC", (owner_id,))
        rows = [dict(r) for r in c.fetchall()]
        list_cache.set(cache_key, rows)
        return rows
    finally:
        release_conn(conn)


def is_friend(owner_id: int, user_id: int):
    friends = get_friends(owner_id)
    return any(f["user_id"] == user_id for f in friends)


def clear_friends(owner_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM friends WHERE owner_id = %s", (owner_id,))
        conn.commit()
        list_cache.invalidate(f"friends:{owner_id}")
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# سایلنت
# ══════════════════════════════════════════════════════════════════════════════
def add_silent_chat(owner_id: int, chat_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO silent_chats (owner_id, chat_id) VALUES (%s, %s) ON CONFLICT (owner_id, chat_id) DO NOTHING",
                  (owner_id, chat_id))
        conn.commit()
    finally:
        release_conn(conn)


def remove_silent_chat(owner_id: int, chat_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM silent_chats WHERE owner_id = %s AND chat_id = %s", (owner_id, chat_id))
        conn.commit()
    finally:
        release_conn(conn)


def is_silent_chat(owner_id: int, chat_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT 1 FROM silent_chats WHERE owner_id = %s AND chat_id = %s", (owner_id, chat_id))
        return c.fetchone() is not None
    finally:
        release_conn(conn)


def add_silent_user(owner_id: int, user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO silent_users (owner_id, user_id) VALUES (%s, %s) ON CONFLICT (owner_id, user_id) DO NOTHING",
                  (owner_id, user_id))
        conn.commit()
    finally:
        release_conn(conn)


def remove_silent_user(owner_id: int, user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM silent_users WHERE owner_id = %s AND user_id = %s", (owner_id, user_id))
        conn.commit()
    finally:
        release_conn(conn)


def is_silent_user(owner_id: int, user_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT 1 FROM silent_users WHERE owner_id = %s AND user_id = %s", (owner_id, user_id))
        return c.fetchone() is not None
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# پیام‌ها
# ══════════════════════════════════════════════════════════════════════════════
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO saved_messages (owner_id, slot, content, media_path) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (owner_id, slot) DO UPDATE SET content=EXCLUDED.content, media_path=EXCLUDED.media_path""",
                  (owner_id, slot, content, media_path))
        conn.commit()
    finally:
        release_conn(conn)


def get_message_slot(owner_id: int, slot: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM saved_messages WHERE owner_id = %s AND slot = %s", (owner_id, slot))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        release_conn(conn)


def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO deleted_messages (owner_id, chat_id, sender_id, sender_name, message, media_type)
                     VALUES (%s, %s, %s, %s, %s, %s)""",
                  (owner_id, chat_id, sender_id, sender_name, message, media_type))
        conn.commit()
    finally:
        release_conn(conn)


def get_deleted_messages(owner_id: int, limit=50):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM deleted_messages WHERE owner_id = %s ORDER BY deleted_at DESC LIMIT %s",
                  (owner_id, limit))
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("""INSERT INTO scheduled_messages (owner_id, chat_id, message, send_at)
                     VALUES (%s, %s, %s, %s) RETURNING id""",
                  (owner_id, chat_id, message, send_at))
        last_id = c.fetchone()["id"]
        conn.commit()
        return last_id
    finally:
        release_conn(conn)


def get_pending_scheduled(owner_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("""SELECT * FROM scheduled_messages 
                     WHERE owner_id = %s AND sent = 0 AND send_at <= CURRENT_TIMESTAMP 
                     ORDER BY send_at""", (owner_id,))
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def mark_scheduled_sent(msg_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE scheduled_messages SET sent = 1 WHERE id = %s", (msg_id,))
        conn.commit()
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# چنل‌های اجباری
# ══════════════════════════════════════════════════════════════════════════════
def get_forced_channels():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT username FROM forced_channels ORDER BY added_at DESC")
        return [r["username"] for r in c.fetchall()]
    finally:
        release_conn(conn)


def add_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO forced_channels (username) VALUES (%s)", (username,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        release_conn(conn)


def remove_forced_channel(username: str) -> bool:
    if not username.startswith("@"):
        username = "@" + username
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM forced_channels WHERE username = %s", (username,))
        affected = c.rowcount
        conn.commit()
        return affected > 0
    finally:
        release_conn(conn)


def check_user_membership(bot, user_id: int) -> tuple:
    channels = get_forced_channels()
    if not channels:
        return True, []
    missing = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return len(missing) == 0, missing


# ══════════════════════════════════════════════════════════════════════════════
# جام جهانی
# ══════════════════════════════════════════════════════════════════════════════
def create_world_cup_challenge(team1: str, team2: str, match_time: str, bet_amount: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("""INSERT INTO world_cup_challenges (team1, team2, match_time, bet_amount, status)
                     VALUES (%s, %s, %s, %s, 'active') RETURNING id""",
                  (team1, team2, match_time, bet_amount))
        challenge_id = c.fetchone()["id"]
        conn.commit()
        return challenge_id
    finally:
        release_conn(conn)


def update_challenge_message(challenge_id: int, message_id: int, chat_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE world_cup_challenges SET message_id = %s, chat_id = %s WHERE id = %s",
                  (message_id, chat_id, challenge_id))
        conn.commit()
    finally:
        release_conn(conn)


def get_active_challenges():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM world_cup_challenges WHERE status = 'active' ORDER BY created_at DESC")
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def get_challenge(challenge_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM world_cup_challenges WHERE id = %s", (challenge_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        release_conn(conn)


def place_bet(challenge_id: int, user_tg_id: int, owner_id: int, team_choice: str, bet_amount: int) -> tuple:
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        c.execute("SELECT balance FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        if not row or row["balance"] < bet_amount:
            return False, f"❌ موجودی کافی ندارید. موجودی: {row['balance'] if row else 0} الماس"
        
        c.execute("SELECT 1 FROM world_cup_bets WHERE challenge_id = %s AND user_tg_id = %s",
                  (challenge_id, user_tg_id))
        if c.fetchone():
            return False, "❌ شما قبلاً در این چالش شرکت کرده‌اید."
        
        c_upd = conn.cursor()
        c_upd.execute("UPDATE tokens SET balance = balance - %s WHERE owner_id = %s", (bet_amount, owner_id))
        c_upd.execute("""INSERT INTO world_cup_bets (challenge_id, user_tg_id, owner_id, team_choice, bet_amount)
                         VALUES (%s, %s, %s, %s, %s)""",
                      (challenge_id, user_tg_id, owner_id, team_choice, bet_amount))
        
        conn.commit()
        token_cache.invalidate(f"token_balance:{owner_id}")
        return True, f"✅ شرط {bet_amount} الماس روی {team_choice} ثبت شد."
    except Exception as e:
        return False, f"❌ خطا: {str(e)}"
    finally:
        release_conn(conn)


def get_challenge_bets(challenge_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM world_cup_bets WHERE challenge_id = %s", (challenge_id,))
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def set_challenge_winner(challenge_id: int, winner_team: str):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE world_cup_challenges SET winner_team = %s, status = 'finished' WHERE id = %s",
                  (winner_team, challenge_id))
        conn.commit()
    finally:
        release_conn(conn)


def settle_challenge_bets(challenge_id: int):
    challenge = get_challenge(challenge_id)
    if not challenge or not challenge["winner_team"]:
        return False, "❌ چالش یافت نشد یا برنده مشخص نشده."
    
    bets = get_challenge_bets(challenge_id)
    results = []
    
    conn = get_conn()
    try:
        c = conn.cursor()
        for bet in bets:
            if bet["team_choice"] == challenge["winner_team"]:
                winnings = bet["bet_amount"] * 2
                c.execute("UPDATE tokens SET balance = balance + %s WHERE owner_id = %s",
                          (winnings, bet["owner_id"]))
                c.execute("UPDATE world_cup_bets SET result = 'won' WHERE id = %s", (bet["id"],))
                results.append({"user_tg_id": bet["user_tg_id"], "owner_id": bet["owner_id"],
                                "result": "won", "amount": winnings})
            else:
                c.execute("UPDATE world_cup_bets SET result = 'lost' WHERE id = %s", (bet["id"],))
                results.append({"user_tg_id": bet["user_tg_id"], "owner_id": bet["owner_id"],
                                "result": "lost", "amount": bet["bet_amount"]})
        
        conn.commit()
        for r in results:
            token_cache.invalidate(f"token_balance:{r['owner_id']}")
        return True, results
    except Exception as e:
        return False, str(e)
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════════
# قرعه‌کشی - با entry_fee
# ══════════════════════════════════════════════════════════════════════════════
def create_lottery(chat_id: int, creator_tg_id: int, prize_amount: int, duration_minutes: int, entry_fee: int = 1):
    """ایجاد قرعه‌کشی با entry_fee قابل تنظیم"""
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=duration_minutes)
        c.execute("""INSERT INTO lotteries (chat_id, creator_tg_id, prize_amount, entry_fee, end_time, status)
                     VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id""",
                  (chat_id, creator_tg_id, prize_amount, entry_fee, end_time.isoformat()))
        lottery_id = c.fetchone()["id"]
        conn.commit()
        return lottery_id
    finally:
        release_conn(conn)


def update_lottery_message(lottery_id: int, message_id: int):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE lotteries SET message_id = %s WHERE id = %s", (message_id, lottery_id))
        conn.commit()
    finally:
        release_conn(conn)


def get_active_lotteries():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM lotteries WHERE status = 'active' ORDER BY created_at DESC")
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def get_lottery(lottery_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM lotteries WHERE id = %s", (lottery_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        release_conn(conn)


def join_lottery(lottery_id: int, user_tg_id: int, owner_id: int) -> tuple:
    """شرکت در قرعه‌کشی - entry_fee از جدول خوانده می‌شود"""
    conn = get_conn()
    try:
        _ensure_tokens_row(conn, owner_id)
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # خواندن entry_fee از جدول
        c.execute("SELECT entry_fee FROM lotteries WHERE id = %s AND status = 'active'", (lottery_id,))
        lottery_row = c.fetchone()
        if not lottery_row:
            return False, "❌ قرعه‌کشی فعال نیست یا یافت نشد."
        
        entry_fee = lottery_row["entry_fee"]
        
        # بررسی موجودی
        c.execute("SELECT balance FROM tokens WHERE owner_id = %s", (owner_id,))
        row = c.fetchone()
        if not row or row["balance"] < entry_fee:
            return False, f"❌ موجودی کافی ندارید. موجودی: {row['balance'] if row else 0} الماس | هزینه: {entry_fee} الماس"
        
        # بررسی شرکت قبلی
        c.execute("SELECT 1 FROM lottery_participants WHERE lottery_id = %s AND user_tg_id = %s",
                  (lottery_id, user_tg_id))
        if c.fetchone():
            return False, "❌ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید."
        
        # کسر الماس و ثبت شرکت
        c_upd = conn.cursor()
        c_upd.execute("UPDATE tokens SET balance = balance - %s WHERE owner_id = %s", (entry_fee, owner_id))
        c_upd.execute("""INSERT INTO lottery_participants (lottery_id, user_tg_id, owner_id, bet_amount)
                         VALUES (%s, %s, %s, %s)""",
                      (lottery_id, user_tg_id, owner_id, entry_fee))
        
        conn.commit()
        token_cache.invalidate(f"token_balance:{owner_id}")
        return True, f"✅ با {entry_fee} الماس در قرعه‌کشی شرکت کردید."
    except Exception as e:
        return False, f"❌ خطا: {str(e)}"
    finally:
        release_conn(conn)


def get_lottery_participants(lottery_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM lottery_participants WHERE lottery_id = %s", (lottery_id,))
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def finish_lottery(lottery_id: int, winner_tg_id: int, winner_owner_id: int):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM lottery_participants WHERE lottery_id = %s", (lottery_id,))
        participants = c.fetchall()
        
        total_prize = sum(p["bet_amount"] for p in participants)
        
        c_upd = conn.cursor()
        c_upd.execute("UPDATE tokens SET balance = balance + %s WHERE owner_id = %s",
                      (total_prize, winner_owner_id))
        c_upd.execute("UPDATE lotteries SET winner_tg_id = %s, status = 'finished' WHERE id = %s",
                      (winner_tg_id, lottery_id))
        
        conn.commit()
        token_cache.invalidate(f"token_balance:{winner_owner_id}")
        return True, total_prize
    except Exception as e:
        return False, str(e)
    finally:
        release_conn(conn)


def get_expired_lotteries():
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM lotteries WHERE status = 'active' AND end_time <= CURRENT_TIMESTAMP")
        return [dict(r) for r in c.fetchall()]
    finally:
        release_conn(conn)


def update_lottery_entry_fee(lottery_id: int, new_fee: int) -> bool:
    """به‌روزرسانی هزینه ثبت‌نام قرعه‌کشی"""
    if new_fee < 0:
        return False
    
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("UPDATE lotteries SET entry_fee = %s WHERE id = %s AND status = 'active'", 
                  (new_fee, lottery_id))
        affected = c.rowcount
        conn.commit()
        return affected > 0
    finally:
        release_conn(conn)
