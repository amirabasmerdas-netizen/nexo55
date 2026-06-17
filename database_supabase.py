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
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(DATABASE_URL, sslmode='require')
            _conn.autocommit = True
            print("✅ اتصال به Supabase برقرار شد")
    except Exception as e:
        print(f"❌ خطا در اتصال به Supabase: {e}")
        raise
    return _conn

def execute_query(query: str, params: tuple = None, fetch_one: bool = False, fetch_all: bool = False):
    """اجرای کوئری با مدیریت خودکار اتصال"""
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
        global _conn
        _conn = None
        raise
    except Exception as e:
        print(f"❌ خطای دیتابیس: {e}")
        raise
    finally:
        if cur:
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
            telegram_user_id BIGINT,
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
        # جدول توکن‌ها (الماس)
        """
        CREATE TABLE IF NOT EXISTS amel_tokens (
            owner_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily DATE,
            total_earned INTEGER DEFAULT 0
        )
        """,
        # جدول رفرال‌ها
        """
        CREATE TABLE IF NOT EXISTS amel_referrals (
            id SERIAL PRIMARY KEY,
            referrer_owner_id INTEGER NOT NULL,
            referred_tg_id BIGINT NOT NULL UNIQUE,
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
            chat_id BIGINT NOT NULL,
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
            chat_id BIGINT,
            sender_id BIGINT,
            sender_name TEXT,
            message TEXT,
            media_type TEXT,
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول چالش‌های ریاضی
        """
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
        # جدول شرط‌بندی جام جهانی
        """
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
        # جدول شرط‌های کاربران
        """
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
        # جدول تنظیمات چالش
        """
        CREATE TABLE IF NOT EXISTS amel_challenge_settings (
            owner_id INTEGER PRIMARY KEY,
            math_challenge_active BOOLEAN DEFAULT FALSE,
            worldcup_challenge_active BOOLEAN DEFAULT FALSE,
            last_math_challenge TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        # جدول شرط‌بندی دو نفره
        """
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
        # ایندکس‌ها
        """
        CREATE INDEX IF NOT EXISTS idx_amel_accounts_telegram_user_id 
        ON amel_accounts(telegram_user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_amel_settings_owner_id 
        ON amel_settings(owner_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_amel_tokens_owner_id 
        ON amel_tokens(owner_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_amel_referrals_referrer 
        ON amel_referrals(referrer_owner_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_amel_scheduled_send_at 
        ON amel_scheduled_messages(send_at) WHERE sent = 0
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_amel_deleted_owner_id 
        ON amel_deleted_messages(owner_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_math_challenges_owner 
        ON amel_math_challenges(owner_id, solved)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_worldcup_bets_owner 
        ON amel_worldcup_bets(owner_id, is_finished)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_user_bets_bet 
        ON amel_user_bets(bet_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_bet_games_chat_status 
        ON amel_bet_games(chat_id, status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_bet_games_created_at 
        ON amel_bet_games(created_at)
        """,
    ]
    
    for query in queries:
        try:
            execute_query(query)
        except Exception as e:
            print(f"❌ Error creating table/index: {e}")
    
    print("✅ جداول Supabase ایجاد/تأیید شدند!")

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
        return result if result else None
    except Exception as e:
        print(f"❌ get_account error: {e}")
        return None

def get_account_by_username(username: str) -> Optional[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE username = %s"
        result = execute_query(query, (username.strip(),), fetch_one=True)
        return result if result else None
    except Exception as e:
        print(f"❌ get_account_by_username error: {e}")
        return None

def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    try:
        if not tg_id:
            return None
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts WHERE telegram_user_id = %s"
        result = execute_query(query, (int(tg_id),), fetch_one=True)
        if result:
            print(f"✅ کاربر با tg_id {tg_id} پیدا شد: {result}")
        else:
            print(f"❌ کاربر با tg_id {tg_id} پیدا نشد")
        return result if result else None
    except Exception as e:
        print(f"❌ get_account_by_tg_id error: {e}")
        return None

def get_all_accounts() -> List[Dict]:
    try:
        query = "SELECT id, username, telegram_user_id, created_at FROM amel_accounts ORDER BY created_at"
        result = execute_query(query, fetch_all=True)
        return result if result else []
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
        execute_query(query, (int(tg_user_id), owner_id))
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
    "_login_phone": "",
    "_login_phone_hash": "",
    "_login_partial_session": "",
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
    except Exception as e:
        print(f"❌ get_setting error for {key}: {e}")
    
    default_val = SETTING_DEFAULTS.get(key, default)
    _settings_cache[cache_key] = str(default_val) if default_val is not None else ""
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
        print(f"✅ تنظیم {key} برای کاربر {owner_id} = {value_str} ذخیره شد")
    except Exception as e:
        print(f"❌ set_setting error for {key}: {e}")

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

# ─── توکن‌ها (الماس) ──────────────────────────────────────────────────────────
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
        query = "SELECT balance FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
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
        print(f"✅ {amount} الماس به کاربر {owner_id} اضافه شد")
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
        print(f"✅ {amount} الماس از کاربر {owner_id} کسر شد")
        return True
    except Exception as e:
        print(f"❌ deduct_tokens error: {e}")
        return False

def claim_daily_token(owner_id: int):
    from config import DAILY_TOKEN_GIFT
    try:
        _init_tokens(owner_id)
        today = datetime.date.today()
        today_str = today.isoformat()
        
        query = "SELECT last_daily FROM amel_tokens WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
        
        if result and result.get('last_daily'):
            last_daily = result['last_daily']
            if hasattr(last_daily, 'isoformat'):
                last_daily = last_daily.isoformat()
            if last_daily == today_str:
                return False, "⏰ امروز قبلاً هدیه روزانه دریافت کردید."
        
        query = """
            UPDATE amel_tokens 
            SET balance = balance + %s, total_earned = total_earned + %s, last_daily = %s 
            WHERE owner_id = %s
        """
        execute_query(query, (DAILY_TOKEN_GIFT, DAILY_TOKEN_GIFT, today_str, owner_id))
        return True, f"🎁 {DAILY_TOKEN_GIFT} الماس دریافت کردید!"
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
            last_daily = result['last_daily']
            if hasattr(last_daily, 'isoformat'):
                last_daily = last_daily.isoformat()
            return {
                "balance": result['balance'],
                "last_daily": last_daily,
                "total_earned": result['total_earned'],
                "can_claim_daily": last_daily != today,
            }
    except Exception as e:
        print(f"❌ get_token_stats error: {e}")
    return {"balance": 0, "last_daily": None, "total_earned": 0, "can_claim_daily": True}

# ─── رفرال ──────────────────────────────────────────────────────────────────
def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    from config import REFERRAL_TOKENS
    try:
        query = "SELECT 1 FROM amel_referrals WHERE referred_tg_id = %s"
        if execute_query(query, (int(referred_tg_id),), fetch_one=True):
            print(f"❌ کاربر {referred_tg_id} قبلاً معرفی شده است")
            return False
        
        if not get_account(referrer_owner_id):
            print(f"❌ معرف با ID {referrer_owner_id} وجود ندارد")
            return False
        
        query = """
            INSERT INTO amel_referrals (referrer_owner_id, referred_tg_id, created_at) 
            VALUES (%s, %s, %s)
        """
        execute_query(query, (referrer_owner_id, int(referred_tg_id), datetime.datetime.now().isoformat()))
        
        add_tokens(referrer_owner_id, REFERRAL_TOKENS)
        print(f"✅ رفرال ثبت شد: {referrer_owner_id} -> {referred_tg_id}")
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
        print(f"✅ پیام در اسلات {slot} برای کاربر {owner_id} ذخیره شد")
    except Exception as e:
        print(f"❌ save_message_slot error: {e}")

def get_message_slot(owner_id: int, slot: int):
    try:
        query = "SELECT * FROM amel_saved_messages WHERE owner_id = %s AND slot = %s"
        result = execute_query(query, (owner_id, slot), fetch_one=True)
        return result if result else None
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
        result = execute_query(query, (owner_id, now), fetch_all=True)
        return result if result else []
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
        return result if result else []
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
        return result['id'] if result else None
    except Exception as e:
        print(f"❌ create_math_challenge error: {e}")
        return None

def get_math_challenge(owner_id: int):
    try:
        query = """
            SELECT * FROM amel_math_challenges 
            WHERE owner_id = %s AND solved = FALSE 
            ORDER BY created_at DESC LIMIT 1
        """
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result if result else None
    except Exception as e:
        print(f"❌ get_math_challenge error: {e}")
        return None

def solve_math_challenge(challenge_id: int):
    try:
        query = "UPDATE amel_math_challenges SET solved = TRUE WHERE id = %s"
        execute_query(query, (challenge_id,))
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
        return result['id'] if result else None
    except Exception as e:
        print(f"❌ create_worldcup_bet error: {e}")
        return None

def get_active_worldcup_bet(owner_id: int):
    try:
        query = """
            SELECT * FROM amel_worldcup_bets 
            WHERE owner_id = %s AND is_finished = FALSE 
            ORDER BY created_at DESC LIMIT 1
        """
        result = execute_query(query, (owner_id,), fetch_one=True)
        return result if result else None
    except Exception as e:
        print(f"❌ get_active_worldcup_bet error: {e}")
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
        return True
    except Exception as e:
        print(f"❌ place_bet error: {e}")
        return False

def get_bet_users(bet_id: int):
    try:
        query = "SELECT * FROM amel_user_bets WHERE bet_id = %s"
        result = execute_query(query, (bet_id,), fetch_all=True)
        return result if result else []
    except Exception as e:
        print(f"❌ get_bet_users error: {e}")
        return []

def finish_worldcup_bet(bet_id: int, winner: str):
    try:
        query = "UPDATE amel_worldcup_bets SET winner = %s, is_finished = TRUE WHERE id = %s"
        execute_query(query, (winner, bet_id))
        return True
    except Exception as e:
        print(f"❌ finish_worldcup_bet error: {e}")
        return False

def get_challenge_settings(owner_id: int):
    try:
        query = "SELECT * FROM amel_challenge_settings WHERE owner_id = %s"
        result = execute_query(query, (owner_id,), fetch_one=True)
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
        return True
    except Exception as e:
        print(f"❌ update_challenge_settings error: {e}")
        return False

# ─── شرط‌بندی دو نفره ──────────────────────────────────────────────────────────
def create_bet_game(owner_id: int, chat_id: int, player1_id: int, bet_amount: int, message_id: int = None):
    try:
        query = """
            INSERT INTO amel_bet_games (owner_id, chat_id, player1_id, bet_amount, message_id, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'waiting', %s)
            RETURNING id
        """
        result = execute_query(query, (owner_id, chat_id, player1_id, bet_amount, message_id, datetime.datetime.now().isoformat()), fetch_one=True)
        return result['id'] if result else None
    except Exception as e:
        print(f"❌ create_bet_game error: {e}")
        return None

def join_bet_game(game_id: int, player2_id: int):
    try:
        query = "UPDATE amel_bet_games SET player2_id = %s, status = 'active' WHERE id = %s AND status = 'waiting'"
        execute_query(query, (player2_id, game_id))
        return True
    except Exception as e:
        print(f"❌ join_bet_game error: {e}")
        return False

def get_all_active_bet_games(chat_id: int):
    try:
        query = "SELECT * FROM amel_bet_games WHERE chat_id = %s AND status IN ('waiting', 'active') ORDER BY created_at DESC"
        result = execute_query(query, (chat_id,), fetch_all=True)
        return result if result else []
    except Exception as e:
        print(f"❌ get_all_active_bet_games error: {e}")
        return []

def get_active_bet_game(chat_id: int):
    games = get_all_active_bet_games(chat_id)
    return games[0] if games else None

def get_bet_game_by_message(chat_id: int, message_id: int):
    try:
        query = "SELECT * FROM amel_bet_games WHERE chat_id = %s AND message_id = %s AND status IN ('waiting', 'active')"
        result = execute_query(query, (chat_id, message_id), fetch_one=True)
        return result if result else None
    except Exception as e:
        print(f"❌ get_bet_game_by_message error: {e}")
        return None

def finish_bet_game(game_id: int, winner_id: int):
    try:
        query = "UPDATE amel_bet_games SET winner_id = %s, status = 'finished' WHERE id = %s"
        execute_query(query, (winner_id, game_id))
        return True
    except Exception as e:
        print(f"❌ finish_bet_game error: {e}")
        return False

def expire_bet_game(game_id: int):
    try:
        query = "UPDATE amel_bet_games SET status = 'expired' WHERE id = %s AND status = 'waiting'"
        execute_query(query, (game_id,))
        return True
    except Exception as e:
        print(f"❌ expire_bet_game error: {e}")
        return False

def get_bet_game(game_id: int):
    try:
        query = "SELECT * FROM amel_bet_games WHERE id = %s"
        result = execute_query(query, (game_id,), fetch_one=True)
        return result if result else None
    except Exception as e:
        print(f"❌ get_bet_game error: {e}")
        return None

def get_expired_bet_games():
    try:
        expire_time = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat()
        query = "SELECT * FROM amel_bet_games WHERE status = 'waiting' AND created_at < %s"
        result = execute_query(query, (expire_time,), fetch_all=True)
        return result if result else []
    except Exception as e:
        print(f"❌ get_expired_bet_games error: {e}")
        return []

# ─── انتقال الماس ──────────────────────────────────────────────────────────────
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

# ─── مقداردهی اولیه ──────────────────────────────────────────────────────────
try:
    init_tables()
except Exception as e:
    print(f"❌ خطا در ایجاد جداول: {e}")

print("✅ database_supabase.py بارگذاری شد!")
