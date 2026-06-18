# database_supabase.py
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
from contextlib import contextmanager

# ─── Connection Pool ──────────────────────────────────────────────────────────
_pool = None
_pool_lock = threading.Lock()
_pool_created = 0
_POOL_MAX = 5  # کاهش تعداد اتصالات همزمان
_POOL_MIN = 1
_POOL_TIMEOUT = 60

def get_pool():
    """دریافت connection pool با مدیریت خودکار"""
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
                    connect_timeout=3,
                    keepalives=1,
                    keepalives_idle=10,
                    keepalives_interval=5,
                    keepalives_count=3,
                    application_name='amel_self55'
                )
                _pool_created = now
                print("✅ Connection pool ایجاد شد")
            except Exception as e:
                print(f"❌ خطا در ایجاد pool: {e}")
                raise
    return _pool

@contextmanager
def get_connection():
    """مدیریت خودکار اتصال با context manager"""
    pool = get_pool()
    conn = None
    try:
        conn = pool.getconn()
        conn.autocommit = True
        # تنظیم timeout برای کوئری‌ها
        try:
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '5s'")
            cur.close()
        except:
            pass
        yield conn
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise
    finally:
        if conn:
            try:
                pool.putconn(conn)
            except:
                try:
                    conn.close()
                except:
                    pass

def execute_query(query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False):
    """اجرای کوئری با مدیریت خودکار اتصال"""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch_one:
                result = cur.fetchone()
                return dict(result) if result else None
            elif fetch_all:
                result = cur.fetchall()
                return [dict(row) for row in result] if result else []
            return cur.rowcount

def execute_batch(queries: List[tuple]) -> List[Any]:
    """اجرای چند کوئری به صورت batch"""
    if not queries:
        return []
    
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            results = []
            for query, params in queries:
                cur.execute(query, params)
                if query.strip().upper().startswith("SELECT"):
                    results.append(cur.fetchall())
                else:
                    results.append(cur.rowcount)
            conn.commit()
            return results

# ─── کش حافظه با محدودیت ──────────────────────────────────────────────────────
_cache = {}
_cache_time = {}
_cache_lock = threading.Lock()
_MAX_CACHE_SIZE = 200
_CACHE_TTL = 30

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

def cached_query(key: str, query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False, ttl: int = 30):
    """کش با محدودیت اندازه و TTL"""
    now = time.time()
    cache_key = f"{key}:{str(params)}"
    
    with _cache_lock:
        # بررسی کش
        if cache_key in _cache:
            if now - _cache_time.get(cache_key, 0) < ttl:
                return _cache[cache_key]
            else:
                del _cache[cache_key]
                del _cache_time[cache_key]
    
    # اجرای کوئری
    result = execute_query(query, params, fetch_one, fetch_all)
    
    with _cache_lock:
        # اگر کش پر است، قدیمی‌ترین را حذف کن
        if len(_cache) >= _MAX_CACHE_SIZE:
            oldest = min(_cache_time, key=_cache_time.get)
            del _cache[oldest]
            del _cache_time[oldest]
        
        _cache[cache_key] = result
        _cache_time[cache_key] = now
    
    return result

# ─── کش تنظیمات با محدودیت ──────────────────────────────────────────────────
_settings_cache = {}
_settings_cache_time = {}
_settings_cache_lock = threading.Lock()
_MAX_SETTINGS_CACHE = 100
_SETTINGS_CACHE_TTL = 60

def get_setting_cached(owner_id: int, key: str, default=None) -> str:
    """دریافت تنظیمات با کش اختصاصی"""
    cache_key = f"{owner_id}:{key}"
    now = time.time()
    
    with _settings_cache_lock:
        if cache_key in _settings_cache:
            if now - _settings_cache_time.get(cache_key, 0) < _SETTINGS_CACHE_TTL:
                return _settings_cache[cache_key]
            else:
                del _settings_cache[cache_key]
                del _settings_cache_time[cache_key]
        
        # محدود کردن اندازه کش
        if len(_settings_cache) >= _MAX_SETTINGS_CACHE:
            oldest = min(_settings_cache_time, key=_settings_cache_time.get)
            del _settings_cache[oldest]
            del _settings_cache_time[oldest]
    
    # دریافت از دیتابیس
    try:
        result = cached_query(
            f"setting_{owner_id}_{key}",
            "SELECT value FROM amel_settings WHERE owner_id = %s AND key = %s",
            (owner_id, key),
            fetch_one=True,
            ttl=30
        )
        value = result['value'] if result else None
    except Exception:
        value = None
    
    if value is None:
        default_val = SETTING_DEFAULTS.get(key, default)
        value = str(default_val) if default_val is not None else ""
    
    with _settings_cache_lock:
        _settings_cache[cache_key] = value
        _settings_cache_time[cache_key] = now
    
    return value

def invalidate_settings_cache(owner_id: int = None, key: str = None):
    """پاک کردن کش تنظیمات"""
    with _settings_cache_lock:
        if owner_id and key:
            _settings_cache.pop(f"{owner_id}:{key}", None)
            _settings_cache_time.pop(f"{owner_id}:{key}", None)
        elif owner_id:
            keys_to_remove = [k for k in _settings_cache.keys() if k.startswith(f"{owner_id}:")]
            for k in keys_to_remove:
                _settings_cache.pop(k, None)
                _settings_cache_time.pop(k, None)
        else:
            _settings_cache.clear()
            _settings_cache_time.clear()

def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ─── ایجاد جداول ──────────────────────────────────────────────────────────────
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
        """
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
    ]
    
    for idx in indexes:
        try:
            execute_query(idx)
        except Exception as e:
            print(f"❌ Error creating index: {e}")
    
    print("✅ جداول Supabase ایجاد/تأیید شدند!")

# ─── تنظیمات پیش‌فرض ──────────────────────────────────────────────────────────
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

# ─── حساب‌ها ──────────────────────────────────────────────────────────────────
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
            invalidate_settings_cache()
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

# ─── تنظیمات ──────────────────────────────────────────────────────────────────
def get_setting(owner_id: int, key: str, default=None) -> str:
    """دریافت تنظیمات با کش"""
    return get_setting_cached(owner_id, key, default)

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
        
        # پاک کردن کش
        invalidate_settings_cache(owner_id, key)
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

# ─── توکن‌ها ──────────────────────────────────────────────────────────────────
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

# ─── رفرال ──────────────────────────────────────────────────────────────────
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

# ─── پیام‌های ذخیره‌شده ──────────────────────────────────────────────────
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

# ─── پیام‌های زمان‌بندی‌شده ──────────────────────────────────────────────
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

# ─── پیام‌های حذف‌شده ────────────────────────────────────────────────────
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

# ─── چالش‌های ریاضی ──────────────────────────────────────────────────────────
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

# ─── چالش جام جهانی ──────────────────────────────────────────────────────────
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

def get_worldcup_bet_by_message(message_id: int, chat_id: int):
    try:
        return cached_query(
            f"wc_bet_msg_{message_id}_{chat
