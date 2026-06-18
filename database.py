# database.py - Bridge بین دیتابیس‌ها
import hashlib
import datetime
from typing import Optional, Dict, List, Any

# ─── ایمپورت از دیتابیس اصلی (Supabase) ──────────────────────────────────────
from database_supabase import (
    # حساب‌ها
    create_account as supa_create_account,
    verify_account as supa_verify_account,
    get_account as supa_get_account,
    get_account_by_username as supa_get_account_by_username,
    get_account_by_tg_id as supa_get_account_by_tg_id,
    get_all_accounts as supa_get_all_accounts,
    account_exists as supa_account_exists,
    save_telegram_user_id as supa_save_telegram_user_id,
    get_telegram_id_by_owner as supa_get_telegram_id_by_owner,
    # تنظیمات
    get_setting as supa_get_setting,
    set_setting as supa_set_setting,
    toggle_setting as supa_toggle_setting,
    get_all_logged_in_users as supa_get_all_logged_in_users,
    init_user_settings as supa_init_user_settings,
    # توکن
    get_token_balance as supa_get_token_balance,
    add_tokens as supa_add_tokens,
    deduct_tokens as supa_deduct_tokens,
    claim_daily_token as supa_claim_daily_token,
    get_token_stats as supa_get_token_stats,
    # رفرال
    process_referral as supa_process_referral,
    get_referral_count as supa_get_referral_count,
    # پیام
    save_message_slot as supa_save_message_slot,
    get_message_slot as supa_get_message_slot,
    add_scheduled_message as supa_add_scheduled_message,
    get_pending_scheduled as supa_get_pending_scheduled,
    mark_scheduled_sent as supa_mark_scheduled_sent,
    log_deleted_message as supa_log_deleted_message,
    get_deleted_messages as supa_get_deleted_messages,
    # چالش‌ها
    create_math_challenge as supa_create_math_challenge,
    get_math_challenge as supa_get_math_challenge,
    solve_math_challenge as supa_solve_math_challenge,
    create_worldcup_bet as supa_create_worldcup_bet,
    update_challenge_message as supa_update_challenge_message,
    get_active_worldcup_bet as supa_get_active_worldcup_bet,
    get_all_active_worldcup_bets as supa_get_all_active_worldcup_bets,
    get_worldcup_bet_by_message as supa_get_worldcup_bet_by_message,
    place_bet as supa_place_bet,
    get_bet_users as supa_get_bet_users,
    finish_worldcup_bet as supa_finish_worldcup_bet,
    get_challenge_settings as supa_get_challenge_settings,
    update_challenge_settings as supa_update_challenge_settings,
    # شرط‌بندی دو نفره
    create_bet_game as supa_create_bet_game,
    join_bet_game as supa_join_bet_game,
    get_active_bet_game as supa_get_active_bet_game,
    get_all_active_bet_games as supa_get_all_active_bet_games,
    get_bet_game_by_message as supa_get_bet_game_by_message,
    finish_bet_game as supa_finish_bet_game,
    expire_bet_game as supa_expire_bet_game,
    get_bet_game as supa_get_bet_game,
    get_expired_bet_games as supa_get_expired_bet_games,
    transfer_tokens as supa_transfer_tokens,
    # 🆕 توابع جدید برای قرعه‌کشی و چنل اجباری
    SETTING_DEFAULTS,
    _hash_pw,
)

# 🆕 import توابع جدید از database_supabase
try:
    from database_supabase import (
        create_lottery as supa_create_lottery,
        update_lottery_message as supa_update_lottery_message,
        get_lottery as supa_get_lottery,
        join_lottery as supa_join_lottery,
        get_lottery_participants as supa_get_lottery_participants,
        finish_lottery as supa_finish_lottery,
        get_active_challenges as supa_get_active_challenges,
        get_challenge as supa_get_challenge,
        get_challenge_bets as supa_get_challenge_bets,
        set_challenge_winner as supa_set_challenge_winner,
        settle_challenge_bets as supa_settle_challenge_bets,
        create_world_cup_challenge as supa_create_world_cup_challenge,
        place_bet_v2 as supa_place_bet_v2,
        transfer_diamonds as supa_transfer_diamonds,
    )
except ImportError:
    # اگر توابع جدید وجود نداشتند، None قرار بده
    supa_create_lottery = None
    supa_update_lottery_message = None
    supa_get_lottery = None
    supa_join_lottery = None
    supa_get_lottery_participants = None
    supa_finish_lottery = None
    supa_get_active_challenges = None
    supa_get_challenge = None
    supa_get_challenge_bets = None
    supa_set_challenge_winner = None
    supa_settle_challenge_bets = None
    supa_create_world_cup_challenge = None
    supa_place_bet_v2 = None
    supa_transfer_diamonds = None

# ─── ایمپورت از دیتابیس کش (SQLite) ──────────────────────────────────────────
import db_cache as cache

# ─── ایمپورت مستقیم از database_supabase برای init_tables ────────────────────
from database_supabase import init_tables as supa_init_tables


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 تابع init_tables - اصلاح خطای app.py
# ══════════════════════════════════════════════════════════════════════════════
def init_tables():
    """ایجاد جداول در هر دو دیتابیس"""
    try:
        supa_init_tables()
        print("✅ جداول Supabase ایجاد شدند")
    except Exception as e:
        print(f"❌ خطا در ایجاد جداول Supabase: {e}")
    
    try:
        # db_cache خودش در get_conn جداول را ایجاد می‌کند
        cache.get_conn()
        print("✅ جداول SQLite ایجاد شدند")
    except Exception as e:
        print(f"❌ خطا در ایجاد جداول SQLite: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# توابع دیتابیس پایدار
# ══════════════════════════════════════════════════════════════════════════════
def create_account(username: str, password: str) -> Optional[int]:
    return supa_create_account(username, password)

def verify_account(username: str, password: str) -> Optional[int]:
    return supa_verify_account(username, password)

def get_account(owner_id: int) -> Optional[Dict]:
    return supa_get_account(owner_id)

def get_account_by_username(username: str) -> Optional[Dict]:
    return supa_get_account_by_username(username)

def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    return supa_get_account_by_tg_id(tg_id)

def get_all_accounts() -> List[Dict]:
    return supa_get_all_accounts()

def account_exists() -> bool:
    return supa_account_exists()

def save_telegram_user_id(owner_id: int, tg_user_id: int):
    supa_save_telegram_user_id(owner_id, tg_user_id)

def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    return supa_get_telegram_id_by_owner(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع تنظیمات
# ══════════════════════════════════════════════════════════════════════════════
def get_setting(owner_id: int, key: str, default=None) -> str:
    return supa_get_setting(owner_id, key, default)

def set_setting(owner_id: int, key: str, value):
    supa_set_setting(owner_id, key, value)

def toggle_setting(owner_id: int, key: str) -> bool:
    return supa_toggle_setting(owner_id, key)

def get_all_logged_in_users() -> List[int]:
    return supa_get_all_logged_in_users()

def init_user_settings(owner_id: int):
    supa_init_user_settings(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع توکن
# ══════════════════════════════════════════════════════════════════════════════
def get_token_balance(owner_id: int) -> int:
    return supa_get_token_balance(owner_id)

def add_tokens(owner_id: int, amount: int):
    supa_add_tokens(owner_id, amount)

def deduct_tokens(owner_id: int, amount: int) -> bool:
    return supa_deduct_tokens(owner_id, amount)

def claim_daily_token(owner_id: int):
    return supa_claim_daily_token(owner_id)

def get_token_stats(owner_id: int) -> dict:
    return supa_get_token_stats(owner_id)

def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    return supa_process_referral(referrer_owner_id, referred_tg_id)

def get_referral_count(owner_id: int) -> int:
    return supa_get_referral_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# 📋 توابع دشمن (ذخیره در دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_enemy(owner_id, user_id, username, name)

def remove_enemy(owner_id: int, user_id: int) -> bool:
    return cache.remove_enemy(owner_id, user_id)

def get_enemies(owner_id: int) -> List[Dict]:
    return cache.get_enemies(owner_id)

def is_enemy(owner_id: int, user_id: int) -> bool:
    return cache.is_enemy(owner_id, user_id)

def clear_enemies(owner_id: int):
    cache.clear_enemies(owner_id)

def get_enemy_count(owner_id: int) -> int:
    return cache.get_enemy_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# 📋 توابع دوست (ذخیره در دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_friend(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_friend(owner_id, user_id, username, name)

def remove_friend(owner_id: int, user_id: int) -> bool:
    return cache.remove_friend(owner_id, user_id)

def get_friends(owner_id: int) -> List[Dict]:
    return cache.get_friends(owner_id)

def is_friend(owner_id: int, user_id: int) -> bool:
    return cache.is_friend(owner_id, user_id)

def clear_friends(owner_id: int):
    cache.clear_friends(owner_id)

def get_friend_count(owner_id: int) -> int:
    return cache.get_friend_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع پیام
# ══════════════════════════════════════════════════════════════════════════════
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    supa_save_message_slot(owner_id, slot, content, media_path)

def get_message_slot(owner_id: int, slot: int):
    return supa_get_message_slot(owner_id, slot)

def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    return supa_add_scheduled_message(owner_id, chat_id, message, send_at)

def get_pending_scheduled(owner_id: int):
    return supa_get_pending_scheduled(owner_id)

def mark_scheduled_sent(msg_id: int):
    supa_mark_scheduled_sent(msg_id)

def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    supa_log_deleted_message(owner_id, chat_id, sender_id, sender_name, message, media_type)

def get_deleted_messages(owner_id: int, limit=50):
    return supa_get_deleted_messages(owner_id, limit)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع سایلنت (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_silent_chat(owner_id: int, chat_id: int):
    cache.add_silent_chat(owner_id, chat_id)

def remove_silent_chat(owner_id: int, chat_id: int):
    cache.remove_silent_chat(owner_id, chat_id)

def is_silent_chat(owner_id: int, chat_id: int) -> bool:
    return cache.is_silent_chat(owner_id, chat_id)

def add_silent_user(owner_id: int, user_id: int):
    cache.add_silent_user(owner_id, user_id)

def remove_silent_user(owner_id: int, user_id: int):
    cache.remove_silent_user(owner_id, user_id)

def is_silent_user(owner_id: int, user_id: int) -> bool:
    return cache.is_silent_user(owner_id, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع چنل‌های اجباری (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def get_forced_channels():
    return cache.get_forced_channels()

def add_forced_channel(username: str) -> bool:
    return cache.add_forced_channel(username)

def remove_forced_channel(username: str) -> bool:
    return cache.remove_forced_channel(username)

def check_user_membership(bot, user_id: int) -> tuple:
    return cache.check_user_membership(bot, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع چالش
# ══════════════════════════════════════════════════════════════════════════════
def create_math_challenge(owner_id: int, challenge_text: str, correct_answer: str, chat_id: int, message_id: int = None):
    return supa_create_math_challenge(owner_id, challenge_text, correct_answer, chat_id, message_id)

def get_math_challenge(owner_id: int):
    return supa_get_math_challenge(owner_id)

def solve_math_challenge(challenge_id: int):
    return supa_solve_math_challenge(challenge_id)

def create_worldcup_bet(owner_id: int, team1: str, team2: str, match_time: str, photo_file_id: str = None):
    return supa_create_worldcup_bet(owner_id, team1, team2, match_time, photo_file_id)

def update_challenge_message(challenge_id: int, message_id: int, chat_id: int):
    return supa_update_challenge_message(challenge_id, message_id, chat_id)

def get_active_worldcup_bet(owner_id: int):
    return supa_get_active_worldcup_bet(owner_id)

def get_all_active_worldcup_bets(owner_id: int):
    return supa_get_all_active_worldcup_bets(owner_id)

def get_worldcup_bet_by_message(message_id: int, chat_id: int):
    return supa_get_worldcup_bet_by_message(message_id, chat_id)

def place_bet(bet_id: int, user_tg_id: int, selected_team: str, bet_amount: int):
    return supa_place_bet(bet_id, user_tg_id, selected_team, bet_amount)

def get_bet_users(bet_id: int):
    return supa_get_bet_users(bet_id)

def finish_worldcup_bet(bet_id: int, winner: str):
    return supa_finish_worldcup_bet(bet_id, winner)

def get_challenge_settings(owner_id: int):
    return supa_get_challenge_settings(owner_id)

def update_challenge_settings(owner_id: int, key: str, value):
    return supa_update_challenge_settings(owner_id, key, value)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع شرط‌بندی دو نفره
# ══════════════════════════════════════════════════════════════════════════════
def create_bet_game(owner_id: int, chat_id: int, player1_id: int, bet_amount: int, message_id: int = None):
    return supa_create_bet_game(owner_id, chat_id, player1_id, bet_amount, message_id)

def join_bet_game(game_id: int, player2_id: int):
    return supa_join_bet_game(game_id, player2_id)

def get_active_bet_game(chat_id: int):
    return supa_get_active_bet_game(chat_id)

def get_all_active_bet_games(chat_id: int):
    return supa_get_all_active_bet_games(chat_id)

def get_bet_game_by_message(chat_id: int, message_id: int):
    return supa_get_bet_game_by_message(chat_id, message_id)

def finish_bet_game(game_id: int, winner_id: int):
    return supa_finish_bet_game(game_id, winner_id)

def expire_bet_game(game_id: int):
    return supa_expire_bet_game(game_id)

def get_bet_game(game_id: int):
    return supa_get_bet_game(game_id)

def get_expired_bet_games():
    return supa_get_expired_bet_games()

def transfer_tokens(from_owner_id: int, to_tg_id: int, amount: int) -> bool:
    return supa_transfer_tokens(from_owner_id, to_tg_id, amount)


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 توابع قرعه‌کشی (جدید)
# ══════════════════════════════════════════════════════════════════════════════
def create_lottery(chat_id: int, creator_tg_id: int, prize_amount: int, duration_minutes: int, entry_fee: int = 1):
    if supa_create_lottery:
        return supa_create_lottery(chat_id, creator_tg_id, prize_amount, duration_minutes, entry_fee)
    return None

def update_lottery_message(lottery_id: int, message_id: int):
    if supa_update_lottery_message:
        return supa_update_lottery_message(lottery_id, message_id)
    return False

def get_lottery(lottery_id: int):
    if supa_get_lottery:
        return supa_get_lottery(lottery_id)
    return None

def join_lottery(lottery_id: int, user_tg_id: int, owner_id: int, entry_fee: int = None) -> tuple:
    if supa_join_lottery:
        return supa_join_lottery(lottery_id, user_tg_id, owner_id, entry_fee)
    return False, "❌ تابع قرعه‌کشی فعال نیست"

def get_lottery_participants(lottery_id: int):
    if supa_get_lottery_participants:
        return supa_get_lottery_participants(lottery_id)
    return []

def finish_lottery(lottery_id: int, winner_tg_id: int, winner_owner_id: int):
    if supa_finish_lottery:
        return supa_finish_lottery(lottery_id, winner_tg_id, winner_owner_id)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 توابع چالش جام جهانی (جدید)
# ══════════════════════════════════════════════════════════════════════════════
def create_world_cup_challenge(team1: str, team2: str, match_time: str, bet_amount: int):
    if supa_create_world_cup_challenge:
        return supa_create_world_cup_challenge(team1, team2, match_time, bet_amount)
    return None

def get_active_challenges():
    if supa_get_active_challenges:
        return supa_get_active_challenges()
    return []

def get_challenge(challenge_id: int):
    if supa_get_challenge:
        return supa_get_challenge(challenge_id)
    return None

def get_challenge_bets(challenge_id: int):
    if supa_get_challenge_bets:
        return supa_get_challenge_bets(challenge_id)
    return []

def set_challenge_winner(challenge_id: int, winner_team: str):
    if supa_set_challenge_winner:
        return supa_set_challenge_winner(challenge_id, winner_team)
    return False

def settle_challenge_bets(challenge_id: int):
    if supa_settle_challenge_bets:
        return supa_settle_challenge_bets(challenge_id)
    return False, "❌ تابع تسویه فعال نیست"

def place_bet_v2(challenge_id: int, user_tg_id: int, owner_id: int, team_choice: str, bet_amount: int) -> tuple:
    if supa_place_bet_v2:
        return supa_place_bet_v2(challenge_id, user_tg_id, owner_id, team_choice, bet_amount)
    return False, "❌ تابع شرط‌بندی فعال نیست"


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 توابع انتقال الماس (جدید)
# ══════════════════════════════════════════════════════════════════════════════
def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int) -> tuple:
    if supa_transfer_diamonds:
        return supa_transfer_diamonds(from_owner_id, to_owner_id, amount)
    return False, "❌ تابع انتقال فعال نیست"


# ══════════════════════════════════════════════════════════════════════════════
# صادرات
# ══════════════════════════════════════════════════════════════════════════════
__all__ = [
    # 🆕 init_tables - برای app.py
    'init_tables',
    # حساب‌ها
    'create_account', 'verify_account', 'get_account',
    'get_account_by_username', 'get_account_by_tg_id',
    'get_all_accounts', 'account_exists', 'save_telegram_user_id',
    'get_telegram_id_by_owner',
    # تنظیمات
    'get_setting', 'set_setting', 'toggle_setting',
    'get_all_logged_in_users', 'init_user_settings',
    # توکن
    'get_token_balance', 'add_tokens', 'deduct_tokens',
    'claim_daily_token', 'get_token_stats',
    'process_referral', 'get_referral_count',
    # دشمن
    'add_enemy', 'remove_enemy', 'get_enemies', 'is_enemy', 'clear_enemies', 'get_enemy_count',
    # دوست
    'add_friend', 'remove_friend', 'get_friends', 'is_friend', 'clear_friends', 'get_friend_count',
    # پیام
    'save_message_slot', 'get_message_slot',
    'add_scheduled_message', 'get_pending_scheduled', 'mark_scheduled_sent',
    'log_deleted_message', 'get_deleted_messages',
    # سایلنت
    'add_silent_chat', 'remove_silent_chat', 'is_silent_chat',
    'add_silent_user', 'remove_silent_user', 'is_silent_user',
    # چنل‌های اجباری
    'get_forced_channels', 'add_forced_channel', 'remove_forced_channel', 'check_user_membership',
    # چالش‌ها
    'create_math_challenge', 'get_math_challenge', 'solve_math_challenge',
    'create_worldcup_bet', 'update_challenge_message',
    'get_active_worldcup_bet', 'get_all_active_worldcup_bets',
    'get_worldcup_bet_by_message', 'place_bet',
    'get_bet_users', 'finish_worldcup_bet',
    'get_challenge_settings', 'update_challenge_settings',
    # شرط‌بندی دو نفره
    'create_bet_game', 'join_bet_game', 'get_active_bet_game',
    'get_all_active_bet_games', 'get_bet_game_by_message',
    'finish_bet_game', 'expire_bet_game', 'get_bet_game',
    'get_expired_bet_games', 'transfer_tokens',
    # 🆕 قرعه‌کشی
    'create_lottery', 'update_lottery_message', 'get_lottery',
    'join_lottery', 'get_lottery_participants', 'finish_lottery',
    # 🆕 چالش جام جهانی جدید
    'create_world_cup_challenge', 'get_active_challenges', 'get_challenge',
    'get_challenge_bets', 'set_challenge_winner', 'settle_challenge_bets',
    'place_bet_v2',
    # 🆕 انتقال الماس
    'transfer_diamonds',
]
