# database_supabase.py
import os
import json
import hashlib
import datetime
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, List, Any
from config import DATABASE_URL

# ─── اتصال به دیتابیس ──────────────────────────────────────────────────────────
_conn = None

def get_conn():
    """دریافت اتصال به دیتابیس با connection pooling"""
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        _conn.autocommit = True
    return _conn

def execute_query(query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False):
    """اجرای کوئری با مدیریت خودکار اتصال"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(query, params)
        if fetch_one:
            return cur.fetchone()
        elif fetch_all:
            return cur.fetchall()
        return cur.rowcount
    except Exception as e:
        print(f"❌ Database error: {e}")
        raise
    finally:
        cur.close()

def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ─── ایجاد جداول ──────────────────────────────────────────────────────────────
def init_tables():
    """ساخت جداول مورد نیاز در Supabase"""
    queries = [
        # جدول اکانت‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_accounts (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            telegram_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول تنظیمات
        """
        CREATE TABLE IF NOT EXISTS amel_settings (
            owner_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (owner_id, key)
        )
        """,
        # جدول توکن‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_tokens (
            owner_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily TEXT,
            total_earned INTEGER DEFAULT 0
        )
        """,
        # جدول رفرال‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_referrals (
            id SERIAL PRIMARY KEY,
            referrer_owner_id INTEGER NOT NULL,
            referred_tg_id INTEGER NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول پیام‌های ذخیره‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_saved_messages (
            owner_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            content TEXT,
            media_path TEXT,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, slot)
        )
        """,
        # جدول پیام‌های زمان‌بندی‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_scheduled_messages (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            send_at TIMESTAMP NOT NULL,
            sent INTEGER DEFAULT 0
        )
        """,
        # جدول پیام‌های حذف‌شده
        """
        CREATE TABLE IF NOT EXISTS amel_deleted_messages (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            chat_id INTEGER,
            sender_id INTEGER,
            sender_name TEXT,
            message TEXT,
            media_type TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]
    
    for query in queries:
        try:
            execute_query(query)
        except Exception as e:
            print(f"❌ Error creating table: {e}")
    
    print("✅ جداول Supabase ایجاد/تأیید شدند!")

# ─── حساب‌ها ──────────────────────────────────────────────────────────────────
def create_account(username: str, password: str) -> Optional[int]:
    try:
        query = """
            INSERT INTO amel_accounts (username, password_hash, created_at)
            VALUES (%s, %s, %s)
            RETURNING id
        """
        result = execute_query(query, (username.strip(), _hash_pw(password), datetime.datetime.now().isoformat()), fetch_one=True)
        if result:
            print(f"✅ حساب کاربری {username} با ID {result['id']} ایجاد شد")
            return result['id']
        return None
    except psycopg2.IntegrityError:
        print(f"❌ خطا: کاربر با یوزرنیم {username} قبلاً ثبت شده است")
        return None
    except Exception as e:
        print(f"❌ create_account error: {e}")
        return None

def verify_account(username: str, password: str) -> Optional[int]:
    try:
        query = "SELECT id, password_hash FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        if result and result['password_hash'] == _hash_pw(password):
            print(f"✅ ورود موفق برای {username}")
            return result['id']
        print(f"❌ ورود ناموفق برای {username}")
        return None
    except Exception as e:
        print(f"❌ verify_account error: {e}")
        return None

def get_account(owner_id: int) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account error: {e}")
        return None

def get_account_by_username(username: str) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account_by_username error: {e}")
        return None

def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE telegram_user_id = %s"
        result = execute_query(query, (tg_id,), fetch_one=True)
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ get_account_by_tg_id error: {e}")
        return None

def get_all_accounts() -> List[Dict]:
    try:
        query = "SELECT id, username, created_at FROM amel_accounts ORDER BY created_at"
        result = execute_query(query, fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_all_accounts error: {e}")
        return []

def account_exists() -> bool:
    try:
        query = "SELECT COUNT(*) as cnt FROM amel_accounts"
        result = execute_query(query, fetch_one=True)
        return result['cnt'] > 0 if result else False
    except Exception as e:
        print(f"❌ account_exists error: {e}")
        return False

def save_telegram_user_id(owner_id: int, tg_user_id: int):
    try:
        query = "UPDATE amel_accounts SET telegram_user_id = %s WHERE id = %s"
        execute_query(query, (tg_user_id, owner_id))
        print(f"✅ آیدی تلگرام {tg_user_id} برای کاربر {owner_id} ذخیره شد")
    except Exception as e:
        print(f"❌ save_telegram_user_id error: {e}")

def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    try:
        query = "SELECT telegram_user_id FROM amel_accounts WHERE id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result['telegram_user_id'] if result else None
    except Exception as e:
        print(f"❌ get_telegram_id_by_owner error: {e}")
        return None

# ─── تنظیمات ──────────────────────────────────────────────────────────────────
SETTING_DEFAULTS = {
    "self_bot_active": "0",
    "secretary_active": "0",
    "anti_delete_active": "0",
    "anti_link_active": "0",
    "auto_seen_active": "0",
    "auto_reaction_active": "0",
    "private_lock_active": "0",
    "enemy_reply_active": "0",
    "auto_save_media": "0",
    "clock_name_active": "0",
    "clock_bio_active": "0",
    "selected_font": "0",
    "secretary_message": "در حال حاضر در دسترس نیستم.",
    "auto_reaction_emoji": "❤️",
    "spam_active": "0",
    "channel_save_active": "0",
    "spam_delay": "2",
    "session_data": "",
    "logged_in": "0",
}

# کش تنظیمات
_settings_cache = {}

def get_setting(owner_id: int, key: str, default=None) -> str:
    cache_key = f"{owner_id}:{key}"
    if cache_key in _settings_cache:
        return _settings_cache[cache_key]
    
    try:
        query = "SELECT value FROM amel_settings WHERE owner_id = %s AND key = %s"
        result = execute_query(query, (owner_id, key), fetch_one=True)
        if result:
            _settings_cache[cache_key] = result['value']
            return result['value']
    except Exception:
        pass
    
    default_val = SETTING_DEFAULTS.get(key, default)
    _settings_cache[cache_key] = str(default_val) if default_val is not None else ""
    return _settings_cache[cache_key]

def set_setting(owner_id: int, key: str, value):
    try:
        check_query = "SELECT 1 FROM amel_settings WHERE owner_id = %s AND key = %s"
        exists = execute_query(check_query, (owner_id, key), fetch_one=True)
        
        if exists:
            query = "UPDATE amel_settings SET value = %s WHERE owner_id = %s AND key = %s"
            execute_query(query, (str(value), owner_id, key))
        else:
            query = "INSERT INTO amel_settings (owner_id, key, value) VALUES (%s, %s, %s)"
            execute_query(query, (owner_id, key, str(value)))
        
        _settings_cache[f"{owner_id}:{key}"] = str(value)
    except Exception as e:
        print(f"❌ set_setting error: {e}")

def toggle_setting(owner_id: int, key: str) -> bool:
    current = get_setting(owner_id, key, "0")
    new_val = "0" if current == "1" else "1"
    set_setting(owner_id, key, new_val)
    return new_val == "1"

def get_all_logged_in_users() -> List[int]:
    try:
        query = "SELECT owner_id FROM amel_settings WHERE key = 'logged_in' AND value = '1'"
        result = execute_query(query, fetch_all=True)
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
        query = "INSERT INTO amel_tokens (owner_id, balance, total_earned) VALUES (%s, 0, 0) ON CONFLICT (owner_id) DO NOTHING"
        execute_query(query, (owner_id,))
    except Exception as e:
        print(f"❌ _init_tokens error: {e}")

def get_token_balance(owner_id: int) -> int:
    try:
        query = "SELECT balance FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if result:
            return result['balance']
        _init_tokens(owner_id)
        return 0
    except Exception as e:
        print(f"❌ get_token_balance error: {e}")
        return 0

def add_tokens(owner_id: int, amount: int):
    try:
        _init_tokens(owner_id)
        query = "UPDATE amel_tokens SET balance = balance + %s, total_earned = total_earned + %s WHERE owner_id = %s"
        execute_query(query, (amount, amount, owner_id))
    except Exception as e:
        print(f"❌ add_tokens error: {e}")

def deduct_tokens(owner_id: int, amount: int) -> bool:
    try:
        _init_tokens(owner_id)
        query = "SELECT balance FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if not result or result['balance'] < amount:
            return False
        query = "UPDATE amel_tokens SET balance = balance - %s WHERE owner_id = %s"
        execute_query(query, (amount, owner_id))
        return True
    except Exception as e:
        print(f"❌ deduct_tokens error: {e}")
        return False

def claim_daily_token(owner_id: int):
    from config import DAILY_TOKEN_GIFT
    try:
        _init_tokens(owner_id)
        today = datetime.date.today().isoformat()
        
        query = "SELECT last_daily FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        if result and result['last_daily'] == today:
            return False, "⏰ امروز قبلاً هدیه روزانه دریافت کردید."
        
        query = "UPDATE amel_tokens SET balance = balance + %s, total_earned = total_earned + %s, last_daily = %s WHERE owner_id = %s"
        execute_query(query, (DAILY_TOKEN_GIFT, DAILY_TOKEN_GIFT, today, owner_id))
        return True, f"🎁 {DAILY_TOKEN_GIFT} توکن دریافت کردید!"
    except Exception as e:
        print(f"❌ claim_daily_token error: {e}")
        return False, "خطا در دریافت هدیه"

def get_token_stats(owner_id: int) -> dict:
    try:
        _init_tokens(owner_id)
        query = "SELECT balance, last_daily, total_earned FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
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
        query = "SELECT 1 FROM amel_referrals WHERE referred_tg_id = %s"
        if execute_query(query, (referred_tg_id,), fetch_one=True):
            return False
        
        if not get_account(referrer_owner_id):
            return False
        
        query = "INSERT INTO amel_referrals (referrer_owner_id, referred_tg_id, created_at) VALUES (%s, %s, %s)"
        execute_query(query, (referrer_owner_id, referred_tg_id, datetime.datetime.now().isoformat()))
        add_tokens(referrer_owner_id, REFERRAL_TOKENS)
        return True
    except Exception as e:
        print(f"❌ process_referral error: {e}")
        return False

def get_referral_count(owner_id: int) -> int:
    try:
        query = "SELECT COUNT(*) as cnt FROM amel_referrals WHERE referrer_owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result['cnt'] if result else 0
    except Exception as e:
        print(f"❌ get_referral_count error: {e}")
        return 0

# ─── ⚠️ توابع دشمن و دوست به دیتابیس کش منتقل شدند ⚠️ ──────────────────────
# این توابع دیگر در Supabase ذخیره نمی‌شوند و به db_cache منتقل شده‌اند
# برای استفاده از آنها، از فایل database.py استفاده کنید که به db_cache متصل است

# ─── پیام‌های ذخیره‌شده ──────────────────────────────────────────────────
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    try:
        query = """
            INSERT INTO amel_saved_messages (owner_id, slot, content, media_path, saved_at) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_id, slot) DO UPDATE SET content = EXCLUDED.content, media_path = EXCLUDED.media_path, saved_at = EXCLUDED.saved_at
        """
        execute_query(query, (owner_id, slot, content, media_path, datetime.datetime.now().isoformat()))
    except Exception as e:
        print(f"❌ save_message_slot error: {e}")

def get_message_slot(owner_id: int, slot: int):
    try:
        query = "SELECT * FROM amel_saved_messages WHERE owner_id = %s AND slot = %s"
        result = execute_query(query, (owner_id, slot), fetch_one=True)
        return dict(result) if result else None
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
        result = execute_query(query, (owner_id, chat_id, message, send_at), fetch_one=True)
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
        result = execute_query(query, (owner_id, now), fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_pending_scheduled error: {e}")
        return []

def mark_scheduled_sent(msg_id: int):
    try:
        query = "UPDATE amel_scheduled_messages SET sent = 1 WHERE id = %s"
        execute_query(query, (msg_id,))
    except Exception as e:
        print(f"❌ mark_scheduled_sent error: {e}")

# ─── پیام‌های حذف‌شده ────────────────────────────────────────────────────
def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    try:
        query = """
            INSERT INTO amel_deleted_messages (owner_id, chat_id, sender_id, sender_name, message, media_type, deleted_at) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        execute_query(query, (owner_id, chat_id, sender_id, sender_name, message, media_type, datetime.datetime.now().isoformat()))
    except Exception as e:
        print(f"❌ log_deleted_message error: {e}")

def get_deleted_messages(owner_id: int, limit=50):
    try:
        query = """
            SELECT * FROM amel_deleted_messages 
            WHERE owner_id = %s 
            ORDER BY deleted_at DESC 
            LIMIT %s
        """
        result = execute_query(query, (owner_id, limit), fetch_all=True)
        return [dict(r) for r in result] if result else []
    except Exception as e:
        print(f"❌ get_deleted_messages error: {e}")
        return []

# ─── مقداردهی اولیه ──────────────────────────────────────────────────────────
try:
    init_tables()
except Exception as e:
    print(f"❌ خطا در ایجاد جداول: {e}")

print("✅ database_supabase.py بارگذاری شد!")
