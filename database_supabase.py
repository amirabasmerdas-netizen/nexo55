import os
import json
import hashlib
import datetime
import psycopg2
import psycopg2.extras
import psycopg2.pool
from typing import Optional, Dict, List, Any
from config import DATABASE_URL
import time
import threading


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 Connection Pool - بهینه‌شده
# ══════════════════════════════════════════════════════════════════════════════
_pool = None
_pool_lock = threading.Lock()
_pool_created = 0
_POOL_MAX = 20          # ✅ از 10 به 20
_POOL_MIN = 5           # ✅ از 2 به 5
_POOL_TIMEOUT = 3600    # ✅ از 300 (5 دقیقه) به 3600 (1 ساعت)


def get_pool():
    global _pool, _pool_created
    with _pool_lock:
        now = time.time()
        if _pool is None or (now - _pool_created > _POOL_TIMEOUT):
            if _pool:
                try:
                    _pool.closeall()
                except:
                    pass
            try:
                _pool = psycopg2.pool.SimpleConnectionPool(
                    _POOL_MIN, _POOL_MAX,
                    DATABASE_URL,
                    sslmode='require',
                    connect_timeout=5,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                    application_name='amel_self55'
                )
                _pool_created = now
                print("✅ Connection pool ایجاد شد")
            except Exception as e:
                print(f"❌ خطا در ایجاد pool: {e}")
                raise
    return _pool


def get_conn():
    pool = get_pool()
    try:
        conn = pool.getconn()
        conn.autocommit = True
        # ✅ حذف SET statement_timeout برای سرعت بیشتر
        return conn
    except Exception as e:
        print(f"❌ خطا در دریافت اتصال: {e}")
        raise


def return_conn(conn):
    if conn:
        try:
            pool = get_pool()
            pool.putconn(conn)
        except:
            try:
                conn.close()
            except:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 کش حافظه با TTL بهینه‌شده
# ══════════════════════════════════════════════════════════════════════════════
_cache = {}
_cache_time = {}
_CACHE_TTL = 180        # ✅ از 60 به 180 ثانیه
_cache_lock = threading.Lock()


def clear_cache():
    with _cache_lock:
        _cache.clear()
        _cache_time.clear()


def invalidate_cache(pattern: str = None):
    with _cache_lock:
        if pattern:
            keys_to_remove = [k for k in _cache.keys() if pattern in k]
            for k in keys_to_remove:
                _cache.pop(k, None)
                _cache_time.pop(k, None)
        else:
            _cache.clear()
            _cache_time.clear()


def cached_query(key: str, query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False, ttl: int = 180):
    now = time.time()
    cache_key = f"{key}:{str(params)}"
    with _cache_lock:
        if cache_key in _cache and (now - _cache_time.get(cache_key, 0) < ttl):
            return _cache[cache_key]

    result = execute_query(query, params, fetch_one, fetch_all)

    with _cache_lock:
        _cache[cache_key] = result
        _cache_time[cache_key] = now

    return result


def execute_query(query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False):
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        if fetch_one:
            result = cur.fetchone()
            return dict(result) if result else None
        elif fetch_all:
            result = cur.fetchall()
            return [dict(row) for row in result] if result else []
        return cur.rowcount
    except psycopg2.OperationalError as e:
        print(f"❌ خطای اتصال به دیتابیس: {e}")
        clear_cache()
        raise
    except Exception as e:
        print(f"❌ خطای دیتابیس: {e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            return_conn(conn)


def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# 📋 ایجاد جداول
# ══════════════════════════════════════════════════════════════════════════════
def init_tables():
    try:
        result = execute_query(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'amel_%'",
            fetch_all=True
        )
        existing = [r['tablename'] for r in result] if result else []
    except:
        existing = []

    tables = {
        "amel_accounts": """
            CREATE TABLE IF NOT EXISTS amel_accounts (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                telegram_user_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_settings": """
            CREATE TABLE IF NOT EXISTS amel_settings (
                owner_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (owner_id, key)
            )
        """,
        "amel_tokens": """
            CREATE TABLE IF NOT EXISTS amel_tokens (
                owner_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                last_daily DATE,
                total_earned INTEGER DEFAULT 0
            )
        """,
        "amel_referrals": """
            CREATE TABLE IF NOT EXISTS amel_referrals (
                id SERIAL PRIMARY KEY,
                referrer_owner_id INTEGER NOT NULL,
                referred_tg_id BIGINT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_saved_messages": """
            CREATE TABLE IF NOT EXISTS amel_saved_messages (
                owner_id INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                content TEXT,
                media_path TEXT,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (owner_id, slot)
            )
        """,
        "amel_scheduled_messages": """
            CREATE TABLE IF NOT EXISTS amel_scheduled_messages (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                chat_id BIGINT NOT NULL,
                message TEXT NOT NULL,
                send_at TIMESTAMP NOT NULL,
                sent INTEGER DEFAULT 0
            )
        """,
        "amel_deleted_messages": """
            CREATE TABLE IF NOT EXISTS amel_deleted_messages (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                chat_id BIGINT,
                sender_id BIGINT,
                sender_name TEXT,
                message TEXT,
                media_type TEXT,
                deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_math_challenges": """
            CREATE TABLE IF NOT EXISTS amel_math_challenges (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                challenge_text TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                message_id BIGINT,
                chat_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                solved BOOLEAN DEFAULT FALSE
            )
        """,
        "amel_worldcup_bets": """
            CREATE TABLE IF NOT EXISTS amel_worldcup_bets (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                team1 TEXT NOT NULL,
                team2 TEXT NOT NULL,
                match_time TIMESTAMP NOT NULL,
                photo_file_id TEXT,
                message_id BIGINT,
                chat_id BIGINT,
                winner TEXT,
                is_finished BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_user_bets": """
            CREATE TABLE IF NOT EXISTS amel_user_bets (
                id SERIAL PRIMARY KEY,
                bet_id INTEGER NOT NULL,
                user_tg_id BIGINT NOT NULL,
                selected_team TEXT NOT NULL,
                bet_amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bet_id, user_tg_id)
            )
        """,
        "amel_challenge_settings": """
            CREATE TABLE IF NOT EXISTS amel_challenge_settings (
                owner_id INTEGER PRIMARY KEY,
                math_challenge_active BOOLEAN DEFAULT FALSE,
                worldcup_challenge_active BOOLEAN DEFAULT FALSE,
                last_math_challenge TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_bet_games": """
            CREATE TABLE IF NOT EXISTS amel_bet_games (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                chat_id BIGINT NOT NULL,
                message_id BIGINT,
                player1_id BIGINT NOT NULL,
                player2_id BIGINT,
                bet_amount INTEGER NOT NULL,
                winner_id BIGINT,
                status TEXT DEFAULT 'waiting',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        # 🆕 جداول جدید
        "amel_forced_channels": """
            CREATE TABLE IF NOT EXISTS amel_forced_channels (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_lotteries": """
            CREATE TABLE IF NOT EXISTS amel_lotteries (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                creator_tg_id BIGINT NOT NULL,
                prize_amount INTEGER NOT NULL,
                entry_fee INTEGER DEFAULT 1,
                end_time TIMESTAMP NOT NULL,
                winner_tg_id BIGINT DEFAULT NULL,
                status TEXT DEFAULT 'active',
                message_id BIGINT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "amel_lottery_participants": """
            CREATE TABLE IF NOT EXISTS amel_lottery_participants (
                id SERIAL PRIMARY KEY,
                lottery_id BIGINT NOT NULL,
                user_tg_id BIGINT NOT NULL,
                owner_id BIGINT NOT NULL,
                bet_amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (lottery_id, user_tg_id)
            )
        """,
        "amel_diamond_transactions": """
            CREATE TABLE IF NOT EXISTS amel_diamond_transactions (
                id SERIAL PRIMARY KEY,
                from_owner_id BIGINT NOT NULL,
                to_owner_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
    }

    queries = []
    for table_name, create_query in tables.items():
        if table_name not in existing:
            queries.append((create_query, None))

    if queries:
        for query, _ in queries:
            try:
                execute_query(query)
            except Exception as e:
                print(f"❌ Error creating table: {e}")

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_amel_accounts_telegram_user_id ON amel_accounts(telegram_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_amel_settings_owner_id ON amel_settings(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_amel_tokens_owner_id ON amel_tokens(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_amel_referrals_referrer ON amel_referrals(referrer_owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_amel_scheduled_send_at ON amel_scheduled_messages(send_at) WHERE sent = 0",
        "CREATE INDEX IF NOT EXISTS idx_amel_deleted_owner_id ON amel_deleted_messages(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_math_challenges_owner ON amel_math_challenges(owner_id, solved)",
        "CREATE INDEX IF NOT EXISTS idx_worldcup_bets_owner ON amel_worldcup_bets(owner_id, is_finished)",
        "CREATE INDEX IF NOT EXISTS idx_user_bets_bet ON amel_user_bets(bet_id)",
        "CREATE INDEX IF NOT EXISTS idx_bet_games_chat_status ON amel_bet_games(chat_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_bet_games_created_at ON amel_bet_games(created_at)",
        # 🆕 ایندکس‌های جدید
        "CREATE INDEX IF NOT EXISTS idx_forced_channels_username ON amel_forced_channels(username)",
        "CREATE INDEX IF NOT EXISTS idx_lotteries_status ON amel_lotteries(status)",
        "CREATE INDEX IF NOT EXISTS idx_lottery_part_lottery ON amel_lottery_participants(lottery_id)",
        "CREATE INDEX IF NOT EXISTS idx_diamond_trans_from ON amel_diamond_transactions(from_owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_diamond_trans_to ON amel_diamond_transactions(to_owner_id)",
    ]

    for idx in indexes:
        try:
            execute_query(idx)
        except Exception as e:
            print(f"❌ Error creating index: {e}")

    print("✅ جداول Supabase ایجاد/تأیید شدند!")


# ══════════════════════════════════════════════════════════════════════════════
# 👤 حساب‌ها
# ══════════════════════════════════════════════════════════════════════════════
def create_account(username: str, password: str) -> Optional[int]:
    try:
        query = """
            INSERT INTO amel_accounts (username, password_hash, created_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """
        result = execute_query(
            query,
            (username.strip(), _hash_pw(password), datetime.datetime.now().isoformat()),
            fetch_one=True
        )
        if result:
            clear_cache()
            return result['id']
        return None
    except Exception as e:
        print(f"❌ create_account error: {e}")
        return None


def verify_account(username: str, password: str) -> Optional[int]:
    try:
        query = "SELECT id, password_hash FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        if result and result['password_hash'] == _hash_pw(password):
            return result['id']
        return None
    except Exception as e:
        print(f"❌ verify_account error: {e}")
        return None


def get_account(owner_id: int) -> Optional[Dict]:
    try:
        return cached_query(
            f"account_{owner_id}",
            "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=60
        )
    except Exception as e:
        print(f"❌ get_account error: {e}")
        return None


def get_account_by_username(username: str) -> Optional[Dict]:
    try:
        return cached_query(
            f"account_username_{username}",
            "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE username = %s",
            (username.strip(),),
            fetch_one=True,
            ttl=60
        )
    except Exception as e:
        print(f"❌ get_account_by_username error: {e}")
        return None


def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    try:
        if not tg_id:
            return None
        return cached_query(
            f"account_tg_{tg_id}",
            "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE telegram_user_id = %s",
            (int(tg_id),),
            fetch_one=True,
            ttl=30
        )
    except Exception as e:
        print(f"❌ get_account_by_tg_id error: {e}")
        return None


def get_all_accounts() -> List[Dict]:
    try:
        return cached_query(
            "all_accounts",
            "SELECT id, username, telegram_user_id, created_at FROM amel_accounts ORDER BY created_at",
            fetch_all=True,
            ttl=30
        ) or []
    except Exception as e:
        print(f"❌ get_all_accounts error: {e}")
        return []


def account_exists() -> bool:
    try:
        result = cached_query(
            "account_exists",
            "SELECT COUNT(*) as cnt FROM amel_accounts",
            fetch_one=True,
            ttl=60
        )
        return result['cnt'] > 0 if result else False
    except Exception as e:
        print(f"❌ account_exists error: {e}")
        return False


def save_telegram_user_id(owner_id: int, tg_user_id: int):
    try:
        query = "UPDATE amel_accounts SET telegram_user_id = %s WHERE id = %s"
        execute_query(query, (int(tg_user_id), owner_id))
        invalidate_cache(f"account_{owner_id}")
        invalidate_cache(f"account_tg_{tg_user_id}")
    except Exception as e:
        print(f"❌ save_telegram_user_id error: {e}")


def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    try:
        result = cached_query(
            f"tg_id_{owner_id}",
            "SELECT telegram_user_id FROM amel_accounts WHERE id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=60
        )
        return result['telegram_user_id'] if result else None
    except Exception as e:
        print(f"❌ get_telegram_id_by_owner error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ⚙️ تنظیمات
# ══════════════════════════════════════════════════════════════════════════════
SETTING_DEFAULTS = {
    "self_bot_active": "0", "secretary_active": "0", "anti_delete_active": "0",
    "anti_link_active": "0", "auto_seen_active": "0", "auto_reaction_active": "0",
    "private_lock_active": "0", "enemy_reply_active": "0", "auto_save_media": "0",
    "clock_name_active": "0", "clock_bio_active": "0", "selected_font": "0",
    "secretary_message": "در حال حاضر در دسترس نیستم.", "auto_reaction_emoji": "❤️",
    "spam_active": "0", "channel_save_active": "0", "spam_delay": "2",
    "session_data": "", "logged_in": "0",
    "_login_phone": "", "_login_phone_hash": "", "_login_partial_session": "",
}
_settings_cache = {}
_settings_cache_time = {}
_SETTINGS_CACHE_TTL = 180  # ✅ از 60 به 180


def get_setting(owner_id: int, key: str, default=None) -> str:
    cache_key = f"{owner_id}:{key}"
    now = time.time()
    if cache_key in _settings_cache and (now - _settings_cache_time.get(cache_key, 0) < _SETTINGS_CACHE_TTL):
        return _settings_cache[cache_key]

    try:
        result = cached_query(
            f"setting_{owner_id}_{key}",
            "SELECT value FROM amel_settings WHERE owner_id = %s AND key = %s",
            (owner_id, key),
            fetch_one=True,
            ttl=_SETTINGS_CACHE_TTL
        )
        if result:
            _settings_cache[cache_key] = result['value']
            _settings_cache_time[cache_key] = now
            return result['value']
    except Exception as e:
        print(f"❌ get_setting error for {key}: {e}")

    default_val = SETTING_DEFAULTS.get(key, default)
    _settings_cache[cache_key] = str(default_val) if default_val is not None else ""
    _settings_cache_time[cache_key] = now
    return _settings_cache[cache_key]


def set_setting(owner_id: int, key: str, value):
    try:
        value_str = str(value) if value is not None else ""
        query = """
            INSERT INTO amel_settings (owner_id, key, value) 
            VALUES (%s, %s, %s)
            ON CONFLICT (owner_id, key) 
            DO UPDATE SET value = EXCLUDED.value
        """
        execute_query(query, (owner_id, key, value_str))
        
        _settings_cache[f"{owner_id}:{key}"] = value_str
        _settings_cache_time[f"{owner_id}:{key}"] = time.time()
        invalidate_cache(f"setting_{owner_id}_{key}")
    except Exception as e:
        print(f"❌ set_setting error for {key}: {e}")


def toggle_setting(owner_id: int, key: str) -> bool:
    current = get_setting(owner_id, key, "0")
    new_val = "0" if current == "1" else "1"
    set_setting(owner_id, key, new_val)
    return new_val == "1"


def get_all_logged_in_users() -> List[int]:
    try:
        result = cached_query(
            "logged_in_users",
            "SELECT owner_id FROM amel_settings WHERE key = 'logged_in' AND value = '1'",
            fetch_all=True,
            ttl=30
        )
        return [r['owner_id'] for r in result] if result else []
    except Exception as e:
        print(f"❌ get_all_logged_in_users error: {e}")
        return []


def init_user_settings(owner_id: int):
    for key, value in SETTING_DEFAULTS.items():
        set_setting(owner_id, key, value)
    print(f"✅ تنظیمات کاربر {owner_id} مقداردهی شد")


# ══════════════════════════════════════════════════════════════════════════════
# 💎 توکن‌ها
# ══════════════════════════════════════════════════════════════════════════════
def _init_tokens(owner_id: int):
    try:
        query = """
            INSERT INTO amel_tokens (owner_id, balance, total_earned)
            VALUES (%s, 0, 0)
            ON CONFLICT (owner_id) DO NOTHING
        """
        execute_query(query, (owner_id,))
    except Exception as e:
        print(f"❌ _init_tokens error: {e}")


def get_token_balance(owner_id: int) -> int:
    try:
        _init_tokens(owner_id)
        result = cached_query(
            f"token_balance_{owner_id}",
            "SELECT balance FROM amel_tokens WHERE owner_id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=10
        )
        return result['balance'] if result else 0
    except Exception as e:
        print(f"❌ get_token_balance error: {e}")
        return 0


def add_tokens(owner_id: int, amount: int):
    try:
        _init_tokens(owner_id)
        query = """
            UPDATE amel_tokens
            SET balance = balance + %s, total_earned = total_earned + %s
            WHERE owner_id = %s
        """
        execute_query(query, (amount, amount, owner_id))
        invalidate_cache(f"token_balance_{owner_id}")
        invalidate_cache(f"token_stats_{owner_id}")
    except Exception as e:
        print(f"❌ add_tokens error: {e}")


def deduct_tokens(owner_id: int, amount: int) -> bool:
    try:
        _init_tokens(owner_id)
        balance = get_token_balance(owner_id)
        if balance < amount:
            return False
        query = "UPDATE amel_tokens SET balance = balance - %s WHERE owner_id = %s"
        execute_query(query, (amount, owner_id))
        invalidate_cache(f"token_balance_{owner_id}")
        invalidate_cache(f"token_stats_{owner_id}")
        return True
    except Exception as e:
        print(f"❌ deduct_tokens error: {e}")
        return False


def claim_daily_token(owner_id: int):
    from config import DAILY_TOKEN_GIFT
    try:
        _init_tokens(owner_id)
        today = datetime.date.today().isoformat()
        result = cached_query(
            f"token_daily_{owner_id}",
            "SELECT last_daily FROM amel_tokens WHERE owner_id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=5
        )
        
        if result and result.get('last_daily') == today:
            return False, "⏰ امروز قبلاً هدیه روزانه دریافت کردید."
        
        query = """
            UPDATE amel_tokens 
            SET balance = balance + %s, total_earned = total_earned + %s, last_daily = %s 
            WHERE owner_id = %s
        """
        execute_query(query, (DAILY_TOKEN_GIFT, DAILY_TOKEN_GIFT, today, owner_id))
        invalidate_cache(f"token_balance_{owner_id}")
        invalidate_cache(f"token_stats_{owner_id}")
        invalidate_cache(f"token_daily_{owner_id}")
        return True, f"🎁 {DAILY_TOKEN_GIFT} الماس دریافت کردید!"
    except Exception as e:
        print(f"❌ claim_daily_token error: {e}")
        return False, "خطا در دریافت هدیه"


def get_token_stats(owner_id: int) -> dict:
    try:
        _init_tokens(owner_id)
        result = cached_query(
            f"token_stats_{owner_id}",
            "SELECT balance, last_daily, total_earned FROM amel_tokens WHERE owner_id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=10
        )
        if result:
            today = datetime.date.today().isoformat()
            return {
                "balance": result['balance'],
                "last_daily": result['last_daily'],
                "total_earned": result['total_earned'],
                "can_claim_daily": result['last_daily'] != today,
            }
    except Exception as e:
        print(f"❌ get_token_stats error: {e}")
    return {"balance": 0, "last_daily": None, "total_earned": 0, "can_claim_daily": True}


# ══════════════════════════════════════════════════════════════════════════════
# 🔗 رفرال
# ══════════════════════════════════════════════════════════════════════════════
def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    from config import REFERRAL_TOKENS
    try:
        result = cached_query(
            f"referral_check_{referred_tg_id}",
            "SELECT 1 FROM amel_referrals WHERE referred_tg_id = %s",
            (int(referred_tg_id),),
            fetch_one=True,
            ttl=60
        )
        if result:
            return False
        
        if not get_account(referrer_owner_id):
            return False
        
        query = """
            INSERT INTO amel_referrals (referrer_owner_id, referred_tg_id, created_at) 
            VALUES (%s, %s, %s)
        """
        execute_query(query, (referrer_owner_id, int(referred_tg_id), datetime.datetime.now().isoformat()))
        
        add_tokens(referrer_owner_id, REFERRAL_TOKENS)
        invalidate_cache(f"referral_check_{referred_tg_id}")
        invalidate_cache(f"referral_count_{referrer_owner_id}")
        return True
    except Exception as e:
        print(f"❌ process_referral error: {e}")
        return False


def get_referral_count(owner_id: int) -> int:
    try:
        result = cached_query(
            f"referral_count_{owner_id}",
            "SELECT COUNT(*) as cnt FROM amel_referrals WHERE referrer_owner_id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=30
        )
        return result['cnt'] if result else 0
    except Exception as e:
        print(f"❌ get_referral_count error: {e}")
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# 💬 پیام‌های ذخیره‌شده
# ══════════════════════════════════════════════════════════════════════════════
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    try:
        query = """
            INSERT INTO amel_saved_messages (owner_id, slot, content, media_path, saved_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_id, slot)
            DO UPDATE SET content = EXCLUDED.content, media_path = EXCLUDED.media_path, saved_at = EXCLUDED.saved_at
        """
        execute_query(query, (owner_id, slot, content, media_path, datetime.datetime.now().isoformat()))
        invalidate_cache(f"msg_slot_{owner_id}_{slot}")
    except Exception as e:
        print(f"❌ save_message_slot error: {e}")


def get_message_slot(owner_id: int, slot: int):
    try:
        return cached_query(
            f"msg_slot_{owner_id}_{slot}",
            "SELECT * FROM amel_saved_messages WHERE owner_id = %s AND slot = %s",
            (owner_id, slot),
            fetch_one=True,
            ttl=60
        )
    except Exception as e:
        print(f"❌ get_message_slot error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ⏰ پیام‌های زمان‌بندی‌شده
# ══════════════════════════════════════════════════════════════════════════════
def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    try:
        query = """
            INSERT INTO amel_scheduled_messages (owner_id, chat_id, message, send_at, sent)
            VALUES (%s, %s, %s, %s, 0)
            RETURNING id
        """
        result = execute_query(query, (owner_id, int(chat_id), message, send_at), fetch_one=True)
        return result['id'] if result else None
    except Exception as e:
        print(f"❌ add_scheduled_message error: {e}")
        return None


def get_pending_scheduled(owner_id: int):
    try:
        query = """
            SELECT * FROM amel_scheduled_messages
            WHERE owner_id = %s AND sent = 0 AND send_at <= %s
            ORDER BY send_at
        """
        now = datetime.datetime.now().isoformat()
        return cached_query(
            f"pending_scheduled_{owner_id}",
            query,
            (owner_id, now),
            fetch_all=True,
            ttl=10
        ) or []
    except Exception as e:
        print(f"❌ get_pending_scheduled error: {e}")
        return []


def mark_scheduled_sent(msg_id: int):
    try:
        query = "UPDATE amel_scheduled_messages SET sent = 1 WHERE id = %s"
        execute_query(query, (msg_id,))
        invalidate_cache("pending_scheduled_")
    except Exception as e:
        print(f"❌ mark_scheduled_sent error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 🗑️ پیام‌های حذف‌شده
# ══════════════════════════════════════════════════════════════════════════════
def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    try:
        query = """
            INSERT INTO amel_deleted_messages
            (owner_id, chat_id, sender_id, sender_name, message, media_type, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        execute_query(query, (
            owner_id,
            int(chat_id) if chat_id else None,
            int(sender_id) if sender_id else None,
            sender_name,
            message,
            media_type,
            datetime.datetime.now().isoformat()
        ))
        invalidate_cache(f"deleted_msgs_{owner_id}")
    except Exception as e:
        print(f"❌ log_deleted_message error: {e}")


def get_deleted_messages(owner_id: int, limit=50):
    try:
        return cached_query(
            f"deleted_msgs_{owner_id}",
            "SELECT * FROM amel_deleted_messages WHERE owner_id = %s ORDER BY deleted_at DESC LIMIT %s",
            (owner_id, limit),
            fetch_all=True,
            ttl=30
        ) or []
    except Exception as e:
        print(f"❌ get_deleted_messages error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 🧮 چالش‌های ریاضی
# ══════════════════════════════════════════════════════════════════════════════
def create_math_challenge(owner_id: int, challenge_text: str, correct_answer: str, chat_id: int, message_id: int = None):
    try:
        query = """
            INSERT INTO amel_math_challenges (owner_id, challenge_text, correct_answer, chat_id, message_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        result = execute_query(query, (owner_id, challenge_text, correct_answer, chat_id, message_id, datetime.datetime.now().isoformat()), fetch_one=True)
        if result:
            invalidate_cache(f"math_challenge_{owner_id}")
            return result['id']
        return None
    except Exception as e:
        print(f"❌ create_math_challenge error: {e}")
        return None


def get_math_challenge(owner_id: int):
    try:
        return cached_query(
            f"math_challenge_{owner_id}",
            "SELECT * FROM amel_math_challenges WHERE owner_id = %s AND solved = FALSE ORDER BY created_at DESC LIMIT 1",
            (owner_id,),
            fetch_one=True,
            ttl=5
        )
    except Exception as e:
        print(f"❌ get_math_challenge error: {e}")
        return None


def solve_math_challenge(challenge_id: int):
    try:
        query = "UPDATE amel_math_challenges SET solved = TRUE WHERE id = %s"
        execute_query(query, (challenge_id,))
        invalidate_cache("math_challenge_")
        return True
    except Exception as e:
        print(f"❌ solve_math_challenge error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# ⚽ چالش جام جهانی
# ══════════════════════════════════════════════════════════════════════════════
def create_worldcup_bet(owner_id: int, team1: str, team2: str, match_time: str, photo_file_id: str = None):
    try:
        query = """
            INSERT INTO amel_worldcup_bets (owner_id, team1, team2, match_time, photo_file_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        result = execute_query(query, (owner_id, team1, team2, match_time, photo_file_id, datetime.datetime.now().isoformat()), fetch_one=True)
        if result:
            invalidate_cache(f"wc_bets_{owner_id}")
            return result['id']
        return None
    except Exception as e:
        print(f"❌ create_worldcup_bet error: {e}")
        return None


# 🆕 تابع جدید برای telegram_bot (3).py
def create_world_cup_challenge(team1: str, team2: str, match_time: str, bet_amount: int):
    """ایجاد چالش جام جهانی - سازگار با telegram_bot (3).py"""
    return create_worldcup_bet(1, team1, team2, match_time, None)


def update_challenge_message(challenge_id: int, message_id: int, chat_id: int):
    try:
        query = "UPDATE amel_worldcup_bets SET message_id = %s, chat_id = %s WHERE id = %s"
        execute_query(query, (message_id, chat_id, challenge_id))
        invalidate_cache("wc_bet_")
        return True
    except Exception as e:
        print(f"❌ update_challenge_message error: {e}")
        return False


def get_active_worldcup_bet(owner_id: int):
    try:
        return cached_query(
            f"wc_bet_active_{owner_id}",
            "SELECT * FROM amel_worldcup_bets WHERE owner_id = %s AND is_finished = FALSE ORDER BY created_at DESC LIMIT 1",
            (owner_id,),
            fetch_one=True,
            ttl=5
        )
    except Exception as e:
        print(f"❌ get_active_worldcup_bet error: {e}")
        return None


def get_all_active_worldcup_bets(owner_id: int):
    try:
        return cached_query(
            f"wc_bets_all_{owner_id}",
            "SELECT * FROM amel_worldcup_bets WHERE owner_id = %s AND is_finished = FALSE ORDER BY created_at DESC",
            (owner_id,),
            fetch_all=True,
            ttl=5
        ) or []
    except Exception as e:
        print(f"❌ get_all_active_worldcup_bets error: {e}")
        return []


# 🆕 تابع جدید برای telegram_bot (3).py
def get_active_challenges():
    """دریافت چالش‌های فعال - برای telegram_bot (3).py"""
    try:
        result = cached_query(
            "wc_bets_all_active",
            "SELECT * FROM amel_worldcup_bets WHERE is_finished = FALSE ORDER BY created_at DESC",
            fetch_all=True,
            ttl=5
        )
        if not result:
            return []
        
        challenges = []
        for r in result:
            challenges.append({
                "id": r['id'],
                "team1": r['team1'],
                "team2": r['team2'],
                "match_time": r['match_time'].strftime("%H:%M") if isinstance(r['match_time'], datetime.datetime) else str(r['match_time']),
                "bet_amount": 10,
                "status": "active",
                "message_id": r.get('message_id'),
                "chat_id": r.get('chat_id'),
            })
        return challenges
    except Exception as e:
        print(f"❌ get_active_challenges error: {e}")
        return []


def get_worldcup_bet_by_message(message_id: int, chat_id: int):
    try:
        return cached_query(
            f"wc_bet_msg_{message_id}_{chat_id}",
            "SELECT * FROM amel_worldcup_bets WHERE message_id = %s AND chat_id = %s AND is_finished = FALSE",
            (message_id, chat_id),
            fetch_one=True,
            ttl=5
        )
    except Exception as e:
        print(f"❌ get_worldcup_bet_by_message error: {e}")
        return None


# 🆕 تابع جدید برای telegram_bot (3).py
def get_challenge(challenge_id: int):
    """دریافت یک چالش - برای telegram_bot (3).py"""
    try:
        result = cached_query(
            f"wc_bet_{challenge_id}",
            "SELECT * FROM amel_worldcup_bets WHERE id = %s",
            (challenge_id,),
            fetch_one=True,
            ttl=5
        )
        if not result:
            return None
        
        return {
            "id": result['id'],
            "team1": result['team1'],
            "team2": result['team2'],
            "match_time": result['match_time'].strftime("%H:%M") if isinstance(result['match_time'], datetime.datetime) else str(result['match_time']),
            "bet_amount": 10,
            "status": "active" if not result.get('is_finished') else "finished",
            "winner_team": result.get('winner'),
            "message_id": result.get('message_id'),
            "chat_id": result.get('chat_id'),
        }
    except Exception as e:
        print(f"❌ get_challenge error: {e}")
        return None


def place_bet(bet_id: int, user_tg_id: int, selected_team: str, bet_amount: int):
    try:
        query = """
            INSERT INTO amel_user_bets (bet_id, user_tg_id, selected_team, bet_amount, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (bet_id, user_tg_id) DO UPDATE
            SET selected_team = EXCLUDED.selected_team, bet_amount = EXCLUDED.bet_amount
        """
        execute_query(query, (bet_id, user_tg_id, selected_team, bet_amount, datetime.datetime.now().isoformat()))
        invalidate_cache(f"user_bets_{bet_id}")
        return True
    except Exception as e:
        print(f"❌ place_bet error: {e}")
        return False


# 🆕 نسخه سازگار با telegram_bot (3).py
def place_bet_v2(challenge_id: int, user_tg_id: int, owner_id: int, team_choice: str, bet_amount: int) -> tuple:
    """ثبت شرط - برای telegram_bot (3).py"""
    try:
        _init_tokens(owner_id)
        
        balance = get_token_balance(owner_id)
        if balance < bet_amount:
            return False, f"❌ موجودی کافی ندارید. موجودی: {balance} الماس"
        
        existing = cached_query(
            f"user_bet_{challenge_id}_{user_tg_id}",
            "SELECT 1 FROM amel_user_bets WHERE bet_id = %s AND user_tg_id = %s",
            (challenge_id, user_tg_id),
            fetch_one=True,
            ttl=5
        )
        if existing:
            return False, "❌ شما قبلاً در این چالش شرکت کرده‌اید."
        
        query = """
            INSERT INTO amel_user_bets (bet_id, user_tg_id, selected_team, bet_amount, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """
        execute_query(query, (challenge_id, user_tg_id, team_choice, bet_amount, datetime.datetime.now().isoformat()))
        
        deduct_tokens(owner_id, bet_amount)
        
        invalidate_cache(f"user_bets_{challenge_id}")
        return True, f"✅ شرط {bet_amount} الماس روی {team_choice} ثبت شد."
    except Exception as e:
        print(f"❌ place_bet_v2 error: {e}")
        return False, f"❌ خطا: {str(e)}"


def get_bet_users(bet_id: int):
    try:
        return cached_query(
            f"user_bets_{bet_id}",
            "SELECT * FROM amel_user_bets WHERE bet_id = %s",
            (bet_id,),
            fetch_all=True,
            ttl=10
        ) or []
    except Exception as e:
        print(f"❌ get_bet_users error: {e}")
        return []


# 🆕 برای telegram_bot (3).py
def get_challenge_bets(challenge_id: int):
    """دریافت شرط‌های یک چالش"""
    bets = get_bet_users(challenge_id)
    result = []
    for b in bets:
        result.append({
            "id": b['id'],
            "challenge_id": b['bet_id'],
            "user_tg_id": b['user_tg_id'],
            "owner_id": b['user_tg_id'],
            "team_choice": b['selected_team'],
            "bet_amount": b['bet_amount'],
            "result": "pending",
        })
    return result


def finish_worldcup_bet(bet_id: int, winner: str):
    try:
        query = "UPDATE amel_worldcup_bets SET winner = %s, is_finished = TRUE WHERE id = %s"
        execute_query(query, (winner, bet_id))
        invalidate_cache("wc_bet_")
        invalidate_cache(f"user_bets_{bet_id}")
        return True
    except Exception as e:
        print(f"❌ finish_worldcup_bet error: {e}")
        return False


# 🆕 برای telegram_bot (3).py
def set_challenge_winner(challenge_id: int, winner_team: str):
    return finish_worldcup_bet(challenge_id, winner_team)


def settle_challenge_bets(challenge_id: int):
    """تسویه شرط‌ها - برای telegram_bot (3).py"""
    challenge = get_challenge(challenge_id)
    if not challenge or not challenge.get("winner_team"):
        return False, "❌ چالش یافت نشد یا برنده مشخص نشده."
    
    bets = get_challenge_bets(challenge_id)
    results = []
    
    try:
        for bet in bets:
            if bet["team_choice"] == challenge["winner_team"]:
                winnings = bet["bet_amount"] * 2
                add_tokens(bet["owner_id"], winnings)
                results.append({
                    "user_tg_id": bet["user_tg_id"],
                    "owner_id": bet["owner_id"],
                    "result": "won",
                    "amount": winnings
                })
            else:
                results.append({
                    "user_tg_id": bet["user_tg_id"],
                    "owner_id": bet["owner_id"],
                    "result": "lost",
                    "amount": bet["bet_amount"]
                })
        
        return True, results
    except Exception as e:
        print(f"❌ settle_challenge_bets error: {e}")
        return False, str(e)


def get_challenge_settings(owner_id: int):
    try:
        result = cached_query(
            f"challenge_settings_{owner_id}",
            "SELECT * FROM amel_challenge_settings WHERE owner_id = %s",
            (owner_id,),
            fetch_one=True,
            ttl=30
        )
        if not result:
            query = """
                INSERT INTO amel_challenge_settings (owner_id, math_challenge_active, worldcup_challenge_active, updated_at)
                VALUES (%s, FALSE, FALSE, %s)
                RETURNING *
            """
            result = execute_query(query, (owner_id, datetime.datetime.now().isoformat()), fetch_one=True)
        return result if result else {"math_challenge_active": False, "worldcup_challenge_active": False}
    except Exception as e:
        print(f"❌ get_challenge_settings error: {e}")
        return {"math_challenge_active": False, "worldcup_challenge_active": False}


def update_challenge_settings(owner_id: int, key: str, value):
    try:
        check_query = "SELECT 1 FROM amel_challenge_settings WHERE owner_id = %s"
        exists = execute_query(check_query, (owner_id,), fetch_one=True)
        if exists:
            query = f"UPDATE amel_challenge_settings SET {key} = %s, updated_at = %s WHERE owner_id = %s"
            execute_query(query, (value, datetime.datetime.now().isoformat(), owner_id))
        else:
            query = f"INSERT INTO amel_challenge_settings (owner_id, {key}, updated_at) VALUES (%s, %s, %s)"
            execute_query(query, (owner_id, value, datetime.datetime.now().isoformat()))
        
        invalidate_cache(f"challenge_settings_{owner_id}")
        return True
    except Exception as e:
        print(f"❌ update_challenge_settings error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 🎲 شرط‌بندی دو نفره
# ══════════════════════════════════════════════════════════════════════════════
def create_bet_game(owner_id: int, chat_id: int, player1_id: int, bet_amount: int, message_id: int = None):
    try:
        query = """
            INSERT INTO amel_bet_games (owner_id, chat_id, player1_id, bet_amount, message_id, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'waiting', %s)
            RETURNING id
        """
        result = execute_query(query, (owner_id, chat_id, player1_id, bet_amount, message_id, datetime.datetime.now().isoformat()), fetch_one=True)
        if result:
            invalidate_cache(f"bet_games_{chat_id}")
            return result['id']
        return None
    except Exception as e:
        print(f"❌ create_bet_game error: {e}")
        return None


def join_bet_game(game_id: int, player2_id: int):
    try:
        query = "UPDATE amel_bet_games SET player2_id = %s, status = 'active' WHERE id = %s AND status = 'waiting'"
        execute_query(query, (player2_id, game_id))
        invalidate_cache("bet_games_")
        return True
    except Exception as e:
        print(f"❌ join_bet_game error: {e}")
        return False


def get_all_active_bet_games(chat_id: int):
    try:
        return cached_query(
            f"bet_games_{chat_id}",
            "SELECT * FROM amel_bet_games WHERE chat_id = %s AND status IN ('waiting', 'active') ORDER BY created_at DESC",
            (chat_id,),
            fetch_all=True,
            ttl=5
        ) or []
    except Exception as e:
        print(f"❌ get_all_active_bet_games error: {e}")
        return []


def get_active_bet_game(chat_id: int):
    games = get_all_active_bet_games(chat_id)
    return games[0] if games else None


def get_bet_game_by_message(chat_id: int, message_id: int):
    try:
        return cached_query(
            f"bet_game_msg_{chat_id}_{message_id}",
            "SELECT * FROM amel_bet_games WHERE chat_id = %s AND message_id = %s AND status IN ('waiting', 'active')",
            (chat_id, message_id),
            fetch_one=True,
            ttl=5
        )
    except Exception as e:
        print(f"❌ get_bet_game_by_message error: {e}")
        return None


def finish_bet_game(game_id: int, winner_id: int):
    try:
        query = "UPDATE amel_bet_games SET winner_id = %s, status = 'finished' WHERE id = %s"
        execute_query(query, (winner_id, game_id))
        invalidate_cache("bet_games_")
        return True
    except Exception as e:
        print(f"❌ finish_bet_game error: {e}")
        return False


def expire_bet_game(game_id: int):
    try:
        query = "UPDATE amel_bet_games SET status = 'expired' WHERE id = %s AND status = 'waiting'"
        execute_query(query, (game_id,))
        invalidate_cache("bet_games_")
        return True
    except Exception as e:
        print(f"❌ expire_bet_game error: {e}")
        return False


def get_bet_game(game_id: int):
    try:
        return cached_query(
            f"bet_game_{game_id}",
            "SELECT * FROM amel_bet_games WHERE id = %s",
            (game_id,),
            fetch_one=True,
            ttl=30
        )
    except Exception as e:
        print(f"❌ get_bet_game error: {e}")
        return None


def get_expired_bet_games():
    try:
        expire_time = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat()
        return cached_query(
            "expired_bet_games",
            "SELECT * FROM amel_bet_games WHERE status = 'waiting' AND created_at < %s",
            (expire_time,),
            fetch_all=True,
            ttl=60
        ) or []
    except Exception as e:
        print(f"❌ get_expired_bet_games error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 🎲 قرعه‌کشی (جدید - برای telegram_bot (3).py)
# ══════════════════════════════════════════════════════════════════════════════
def create_lottery(chat_id: int, creator_tg_id: int, prize_amount: int, duration_minutes: int, entry_fee: int = 1):
    """ایجاد قرعه‌کشی"""
    try:
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=duration_minutes)
        query = """
            INSERT INTO amel_lotteries (chat_id, creator_tg_id, prize_amount, entry_fee, end_time, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            RETURNING id
        """
        result = execute_query(query, (chat_id, creator_tg_id, prize_amount, entry_fee, end_time), fetch_one=True)
        if result:
            invalidate_cache("lottery_")
            return result['id']
        return None
    except Exception as e:
        print(f"❌ create_lottery error: {e}")
        return None


def update_lottery_message(lottery_id: int, message_id: int):
    """به‌روزرسانی message_id قرعه‌کشی"""
    try:
        query = "UPDATE amel_lotteries SET message_id = %s WHERE id = %s"
        execute_query(query, (message_id, lottery_id))
        invalidate_cache(f"lottery_{lottery_id}")
        return True
    except Exception as e:
        print(f"❌ update_lottery_message error: {e}")
        return False


def get_lottery(lottery_id: int):
    """دریافت اطلاعات قرعه‌کشی"""
    try:
        result = cached_query(
            f"lottery_{lottery_id}",
            "SELECT * FROM amel_lotteries WHERE id = %s",
            (lottery_id,),
            fetch_one=True,
            ttl=10
        )
        if not result:
            return None
        
        return {
            "id": result['id'],
            "chat_id": result['chat_id'],
            "creator_tg_id": result['creator_tg_id'],
            "prize_amount": result['prize_amount'],
            "entry_fee": result['entry_fee'],
            "end_time": result['end_time'],
            "winner_tg_id": result.get('winner_tg_id'),
            "status": result['status'],
            "message_id": result.get('message_id'),
        }
    except Exception as e:
        print(f"❌ get_lottery error: {e}")
        return None


def join_lottery(lottery_id: int, user_tg_id: int, owner_id: int, entry_fee: int = None) -> tuple:
    """شرکت در قرعه‌کشی"""
    try:
        _init_tokens(owner_id)
        
        lottery = get_lottery(lottery_id)
        if not lottery or lottery['status'] != 'active':
            return False, "❌ قرعه‌کشی فعال نیست یا یافت نشد."
        
        if entry_fee is None:
            entry_fee = lottery['entry_fee']
        
        balance = get_token_balance(owner_id)
        if balance < entry_fee:
            return False, f"❌ موجودی کافی ندارید. موجودی: {balance} الماس | هزینه: {entry_fee} الماس"
        
        existing = cached_query(
            f"lottery_part_{lottery_id}_{user_tg_id}",
            "SELECT 1 FROM amel_lottery_participants WHERE lottery_id = %s AND user_tg_id = %s",
            (lottery_id, user_tg_id),
            fetch_one=True,
            ttl=5
        )
        if existing:
            return False, "❌ شما قبلاً در این قرعه‌کشی شرکت کرده‌اید."
        
        query = """
            INSERT INTO amel_lottery_participants (lottery_id, user_tg_id, owner_id, bet_amount)
            VALUES (%s, %s, %s, %s)
        """
        execute_query(query, (lottery_id, user_tg_id, owner_id, entry_fee))
        
        deduct_tokens(owner_id, entry_fee)
        
        invalidate_cache(f"lottery_part_{lottery_id}_{user_tg_id}")
        invalidate_cache(f"lottery_parts_{lottery_id}")
        return True, f"✅ با {entry_fee} الماس در قرعه‌کشی شرکت کردید."
    except Exception as e:
        print(f"❌ join_lottery error: {e}")
        return False, f"❌ خطا: {str(e)}"


def get_lottery_participants(lottery_id: int):
    """دریافت شرکت‌کنندگان قرعه‌کشی"""
    try:
        result = cached_query(
            f"lottery_parts_{lottery_id}",
            "SELECT * FROM amel_lottery_participants WHERE lottery_id = %s",
            (lottery_id,),
            fetch_all=True,
            ttl=10
        )
        if not result:
            return []
        
        participants = []
        for r in result:
            participants.append({
                "id": r['id'],
                "lottery_id": r['lottery_id'],
                "user_tg_id": r['user_tg_id'],
                "owner_id": r['owner_id'],
                "bet_amount": r['bet_amount'],
            })
        return participants
    except Exception as e:
        print(f"❌ get_lottery_participants error: {e}")
        return []


def finish_lottery(lottery_id: int, winner_tg_id: int, winner_owner_id: int):
    """پایان قرعه‌کشی"""
    try:
        query = "UPDATE amel_lotteries SET winner_tg_id = %s, status = 'finished' WHERE id = %s"
        execute_query(query, (winner_tg_id, lottery_id))
        invalidate_cache(f"lottery_{lottery_id}")
        return True
    except Exception as e:
        print(f"❌ finish_lottery error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 📢 چنل‌های اجباری
# ══════════════════════════════════════════════════════════════════════════════
def get_forced_channels():
    """دریافت لیست چنل‌های اجباری"""
    try:
        result = cached_query(
            "forced_channels",
            "SELECT username FROM amel_forced_channels ORDER BY added_at DESC",
            fetch_all=True,
            ttl=60
        )
        return [r['username'] for r in result] if result else []
    except Exception as e:
        print(f"❌ get_forced_channels error: {e}")
        return []


def add_forced_channel(username: str) -> bool:
    """افزودن چنل اجباری"""
    try:
        if not username.startswith("@"):
            username = "@" + username
        query = "INSERT INTO amel_forced_channels (username) VALUES (%s)"
        execute_query(query, (username,))
        invalidate_cache("forced_channels")
        return True
    except Exception as e:
        print(f"❌ add_forced_channel error: {e}")
        return False


def remove_forced_channel(username: str) -> bool:
    """حذف چنل اجباری"""
    try:
        if not username.startswith("@"):
            username = "@" + username
        query = "DELETE FROM amel_forced_channels WHERE username = %s"
        result = execute_query(query, (username,))
        if result and result > 0:
            invalidate_cache("forced_channels")
            return True
        return False
    except Exception as e:
        print(f"❌ remove_forced_channel error: {e}")
        return False


def check_user_membership(bot, user_id: int) -> tuple:
    """بررسی عضویت کاربر در چنل‌های اجباری"""
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
# 💎 انتقال الماس
# ══════════════════════════════════════════════════════════════════════════════
def transfer_tokens(from_owner_id: int, to_tg_id: int, amount: int) -> bool:
    try:
        balance = get_token_balance(from_owner_id)
        if balance < amount:
            return False
        
        to_account = get_account_by_tg_id(to_tg_id)
        if not to_account:
            return False
        
        deduct_tokens(from_owner_id, amount)
        add_tokens(to_account['id'], amount)
        
        return True
    except Exception as e:
        print(f"❌ transfer_tokens error: {e}")
        return False


# 🆕 برای telegram_bot (3).py
def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int) -> tuple:
    """انتقال الماس بین کاربران - برای telegram_bot (3).py"""
    if amount <= 0:
        return False, "❌ مقدار باید بزرگ‌تر از صفر باشد."
    
    if from_owner_id == to_owner_id:
        return False, "❌ نمی‌توانید به خودتان الماس انتقال دهید."
    
    try:
        _init_tokens(from_owner_id)
        _init_tokens(to_owner_id)
        
        balance = get_token_balance(from_owner_id)
        if balance < amount:
            return False, f"❌ موجودی کافی ندارید. موجودی: {balance} الماس"
        
        deduct_tokens(from_owner_id, amount)
        add_tokens(to_owner_id, amount)
        
        query = """
            INSERT INTO amel_diamond_transactions (from_owner_id, to_owner_id, amount, type, description)
            VALUES (%s, %s, %s, 'transfer', 'انتقال الماس')
        """
        execute_query(query, (from_owner_id, to_owner_id, amount))
        
        return True, f"✅ {amount} الماس با موفقیت انتقال یافت."
    except Exception as e:
        print(f"❌ transfer_diamonds error: {e}")
        return False, f"❌ خطا در انتقال: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# 🚀 مقداردهی اولیه
# ══════════════════════════════════════════════════════════════════════════════
try:
    init_tables()
except Exception as e:
    print(f"❌ خطا در ایجاد جداول: {e}")

print("✅ database_supabase.py بارگذاری شد!")
