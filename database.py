# ══════════════════════════════════════════════════════════════════════════════
# database.py - Bridge بین دیتابیس‌های Supabase و SQLite
# ══════════════════════════════════════════════════════════════════════════════

import hashlib
import datetime
from typing import Optional, Dict, List, Any

# ─── ایمپورت از دیتابیس اصلی (Supabase) ──────────────────────────────────────
from database_supabase import (
    # 🆕 init_tables
    init_tables as supa_init_tables,
    
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
    
    # انتقال الماس
    transfer_tokens as supa_transfer_tokens,
    
    # قرعه‌کشی
    create_lottery as supa_create_lottery,
    update_lottery_message as supa_update_lottery_message,
    get_lottery as supa_get_lottery,
    get_active_lottery_by_chat as supa_get_active_lottery_by_chat,
    join_lottery as supa_join_lottery,
    get_lottery_participants as supa_get_lottery_participants,
    finish_lottery as supa_finish_lottery,
    get_expired_lotteries as supa_get_expired_lotteries,
    cancel_lottery as supa_cancel_lottery,
    
    # ثابت‌ها
    SETTING_DEFAULTS,
    _hash_pw,
)

# ─── ایمپورت از دیتابیس کش (SQLite) ──────────────────────────────────────────
import db_cache as cache


# ══════════════════════════════════════════════════════════════════════════════
# 🆕 init_tables - ایجاد جداول در دیتابیس اصلی
# ══════════════════════════════════════════════════════════════════════════════
def init_tables():
    """ایجاد جداول در دیتابیس Supabase"""
    return supa_init_tables()


# ══════════════════════════════════════════════════════════════════════════════
# توابع دیتابیس پایدار (Supabase)
# ══════════════════════════════════════════════════════════════════════════════
def create_account(username: str, password: str) -> Optional[int]:
    """ایجاد حساب کاربری جدید"""
    return supa_create_account(username, password)


def verify_account(username: str, password: str) -> Optional[int]:
    """تأیید اعتبار حساب کاربری"""
    return supa_verify_account(username, password)


def get_account(owner_id: int) -> Optional[Dict]:
    """دریافت اطلاعات حساب کاربری"""
    return supa_get_account(owner_id)


def get_account_by_username(username: str) -> Optional[Dict]:
    """دریافت حساب بر اساس یوزرنیم"""
    return supa_get_account_by_username(username)


def get_account_by_tg_id(tg_id: int) -> Optional[Dict]:
    """دریافت حساب بر اساس آیدی تلگرام"""
    return supa_get_account_by_tg_id(tg_id)


def get_all_accounts() -> List[Dict]:
    """دریافت لیست تمام حساب‌ها"""
    return supa_get_all_accounts()


def account_exists() -> bool:
    """بررسی وجود حساب کاربری"""
    return supa_account_exists()


def save_telegram_user_id(owner_id: int, tg_user_id: int):
    """ذخیره آیدی تلگرام کاربر"""
    supa_save_telegram_user_id(owner_id, tg_user_id)


def get_telegram_id_by_owner(owner_id: int) -> Optional[int]:
    """دریافت آیدی تلگرام کاربر"""
    return supa_get_telegram_id_by_owner(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع تنظیمات
# ══════════════════════════════════════════════════════════════════════════════
def get_setting(owner_id: int, key: str, default=None) -> str:
    """دریافت مقدار تنظیمات"""
    return supa_get_setting(owner_id, key, default)


def set_setting(owner_id: int, key: str, value):
    """ذخیره مقدار تنظیمات"""
    supa_set_setting(owner_id, key, value)


def toggle_setting(owner_id: int, key: str) -> bool:
    """تغییر وضعیت تنظیمات (روشن/خاموش)"""
    return supa_toggle_setting(owner_id, key)


def get_all_logged_in_users() -> List[int]:
    """دریافت لیست کاربران لاگین شده"""
    return supa_get_all_logged_in_users()


def init_user_settings(owner_id: int):
    """مقداردهی اولیه تنظیمات کاربر"""
    supa_init_user_settings(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع توکن
# ══════════════════════════════════════════════════════════════════════════════
def get_token_balance(owner_id: int) -> int:
    """دریافت موجودی توکن"""
    return supa_get_token_balance(owner_id)


def add_tokens(owner_id: int, amount: int):
    """افزودن توکن به حساب"""
    supa_add_tokens(owner_id, amount)


def deduct_tokens(owner_id: int, amount: int) -> bool:
    """کسر توکن از حساب"""
    return supa_deduct_tokens(owner_id, amount)


def claim_daily_token(owner_id: int):
    """دریافت هدیه روزانه"""
    return supa_claim_daily_token(owner_id)


def get_token_stats(owner_id: int) -> dict:
    """دریافت آمار توکن"""
    return supa_get_token_stats(owner_id)


def process_referral(referrer_owner_id: int, referred_tg_id: int) -> bool:
    """پردازش رفرال"""
    return supa_process_referral(referrer_owner_id, referred_tg_id)


def get_referral_count(owner_id: int) -> int:
    """دریافت تعداد رفرال‌ها"""
    return supa_get_referral_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# توابع پیام
# ══════════════════════════════════════════════════════════════════════════════
def save_message_slot(owner_id: int, slot: int, content, media_path=None):
    """ذخیره پیام در اسلات"""
    supa_save_message_slot(owner_id, slot, content, media_path)


def get_message_slot(owner_id: int, slot: int):
    """دریافت پیام از اسلات"""
    return supa_get_message_slot(owner_id, slot)


def add_scheduled_message(owner_id: int, chat_id, message, send_at):
    """افزودن پیام زمان‌بندی شده"""
    return supa_add_scheduled_message(owner_id, chat_id, message, send_at)


def get_pending_scheduled(owner_id: int):
    """دریافت پیام‌های زمان‌بندی شده در انتظار"""
    return supa_get_pending_scheduled(owner_id)


def mark_scheduled_sent(msg_id: int):
    """علامت‌گذاری پیام به عنوان ارسال شده"""
    supa_mark_scheduled_sent(msg_id)


def log_deleted_message(owner_id: int, chat_id, sender_id, sender_name, message, media_type=None):
    """ثبت پیام حذف شده"""
    supa_log_deleted_message(owner_id, chat_id, sender_id, sender_name, message, media_type)


def get_deleted_messages(owner_id: int, limit=50):
    """دریافت پیام‌های حذف شده"""
    return supa_get_deleted_messages(owner_id, limit)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع سایلنت (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_silent_chat(owner_id: int, chat_id: int):
    """افزودن چت به لیست سایلنت"""
    cache.add_silent_chat(owner_id, chat_id)


def remove_silent_chat(owner_id: int, chat_id: int):
    """حذف چت از لیست سایلنت"""
    cache.remove_silent_chat(owner_id, chat_id)


def is_silent_chat(owner_id: int, chat_id: int) -> bool:
    """بررسی سایلنت بودن چت"""
    return cache.is_silent_chat(owner_id, chat_id)


def add_silent_user(owner_id: int, user_id: int):
    """افزودن کاربر به لیست سایلنت"""
    cache.add_silent_user(owner_id, user_id)


def remove_silent_user(owner_id: int, user_id: int):
    """حذف کاربر از لیست سایلنت"""
    cache.remove_silent_user(owner_id, user_id)


def is_silent_user(owner_id: int, user_id: int) -> bool:
    """بررسی سایلنت بودن کاربر"""
    return cache.is_silent_user(owner_id, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع چنل‌های اجباری (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def get_forced_channels():
    """دریافت لیست چنل‌های اجباری"""
    return cache.get_forced_channels()


def add_forced_channel(username: str) -> bool:
    """افزودن چنل اجباری"""
    return cache.add_forced_channel(username)


def remove_forced_channel(username: str) -> bool:
    """حذف چنل اجباری"""
    return cache.remove_forced_channel(username)


def check_user_membership(bot, user_id: int) -> tuple:
    """بررسی عضویت کاربر در چنل‌های اجباری"""
    return cache.check_user_membership(bot, user_id)


# ══════════════════════════════════════════════════════════════════════════════
# 📋 توابع دشمن (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    """افزودن کاربر به لیست دشمن"""
    return cache.add_enemy(owner_id, user_id, username, name)


def remove_enemy(owner_id: int, user_id: int) -> bool:
    """حذف کاربر از لیست دشمن"""
    return cache.remove_enemy(owner_id, user_id)


def get_enemies(owner_id: int) -> List[Dict]:
    """دریافت لیست دشمن‌ها"""
    return cache.get_enemies(owner_id)


def is_enemy(owner_id: int, user_id: int) -> bool:
    """بررسی دشمن بودن کاربر"""
    return cache.is_enemy(owner_id, user_id)


def clear_enemies(owner_id: int):
    """پاک کردن لیست دشمن"""
    cache.clear_enemies(owner_id)


def get_enemy_count(owner_id: int) -> int:
    """دریافت تعداد دشمن‌ها"""
    return cache.get_enemy_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# 📋 توابع دوست (دیتابیس کش)
# ══════════════════════════════════════════════════════════════════════════════
def add_friend(owner_id: int, user_id: int, username=None, name=None):
    """افزودن کاربر به لیست دوست"""
    return cache.add_friend(owner_id, user_id, username, name)


def remove_friend(owner_id: int, user_id: int) -> bool:
    """حذف کاربر از لیست دوست"""
    return cache.remove_friend(owner_id, user_id)


def get_friends(owner_id: int) -> List[Dict]:
    """دریافت لیست دوست‌ها"""
    return cache.get_friends(owner_id)


def is_friend(owner_id: int, user_id: int) -> bool:
    """بررسی دوست بودن کاربر"""
    return cache.is_friend(owner_id, user_id)


def clear_friends(owner_id: int):
    """پاک کردن لیست دوست"""
    cache.clear_friends(owner_id)


def get_friend_count(owner_id: int) -> int:
    """دریافت تعداد دوست‌ها"""
    return cache.get_friend_count(owner_id)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع چالش
# ══════════════════════════════════════════════════════════════════════════════
def create_math_challenge(owner_id: int, challenge_text: str, correct_answer: str, chat_id: int, message_id: int = None):
    """ایجاد چالش ریاضی"""
    return supa_create_math_challenge(owner_id, challenge_text, correct_answer, chat_id, message_id)


def get_math_challenge(owner_id: int):
    """دریافت چالش ریاضی فعال"""
    return supa_get_math_challenge(owner_id)


def solve_math_challenge(challenge_id: int):
    """حل چالش ریاضی"""
    return supa_solve_math_challenge(challenge_id)


def create_worldcup_bet(owner_id: int, team1: str, team2: str, match_time: str, photo_file_id: str = None):
    """ایجاد شرط‌بندی جام جهانی"""
    return supa_create_worldcup_bet(owner_id, team1, team2, match_time, photo_file_id)


def update_challenge_message(challenge_id: int, message_id: int, chat_id: int):
    """به‌روزرسانی پیام چالش"""
    return supa_update_challenge_message(challenge_id, message_id, chat_id)


def get_active_worldcup_bet(owner_id: int):
    """دریافت شرط‌بندی فعال جام جهانی"""
    return supa_get_active_worldcup_bet(owner_id)


def get_all_active_worldcup_bets(owner_id: int):
    """دریافت تمام شرط‌بندی‌های فعال"""
    return supa_get_all_active_worldcup_bets(owner_id)


def get_worldcup_bet_by_message(message_id: int, chat_id: int):
    """دریافت شرط‌بندی بر اساس پیام"""
    return supa_get_worldcup_bet_by_message(message_id, chat_id)


def place_bet(bet_id: int, user_tg_id: int, selected_team: str, bet_amount: int):
    """ثبت شرط"""
    return supa_place_bet(bet_id, user_tg_id, selected_team, bet_amount)


def get_bet_users(bet_id: int):
    """دریافت کاربران شرط‌بندی"""
    return supa_get_bet_users(bet_id)


def finish_worldcup_bet(bet_id: int, winner: str):
    """پایان شرط‌بندی"""
    return supa_finish_worldcup_bet(bet_id, winner)


def get_challenge_settings(owner_id: int):
    """دریافت تنظیمات چالش"""
    return supa_get_challenge_settings(owner_id)


def update_challenge_settings(owner_id: int, key: str, value):
    """به‌روزرسانی تنظیمات چالش"""
    return supa_update_challenge_settings(owner_id, key, value)


# ══════════════════════════════════════════════════════════════════════════════
# ✅ توابع شرط‌بندی دو نفره
# ══════════════════════════════════════════════════════════════════════════════
def create_bet_game(owner_id: int, chat_id: int, player1_id: int, bet_amount: int, message_id: int = None):
    """ایجاد بازی شرط‌بندی دو نفره"""
    return supa_create_bet_game(owner_id, chat_id, player1_id, bet_amount, message_id)


def join_bet_game(game_id: int, player2_id: int):
    """شرکت در بازی شرط‌بندی"""
    return supa_join_bet_game(game_id, player2_id)


def get_active_bet_game(chat_id: int):
    """دریافت بازی شرط‌بندی فعال"""
    return supa_get_active_bet_game(chat_id)


def get_all_active_bet_games(chat_id: int):
    """دریافت تمام بازی‌های فعال"""
    return supa_get_all_active_bet_games(chat_id)


def get_bet_game_by_message(chat_id: int, message_id: int):
    """دریافت بازی بر اساس پیام"""
    return supa_get_bet_game_by_message(chat_id, message_id)


def finish_bet_game(game_id: int, winner_id: int):
    """پایان بازی"""
    return supa_finish_bet_game(game_id, winner_id)


def expire_bet_game(game_id: int):
    """منقضی کردن بازی"""
    return supa_expire_bet_game(game_id)


def get_bet_game(game_id: int):
    """دریافت اطلاعات بازی"""
    return supa_get_bet_game(game_id)


def get_expired_bet_games():
    """دریافت بازی‌های منقضی شده"""
    return supa_get_expired_bet_games()


def transfer_tokens(from_owner_id: int, to_tg_id: int, amount: int) -> bool:
    """انتقال توکن بین کاربران"""
    return supa_transfer_tokens(from_owner_id, to_tg_id, amount)


def transfer_diamonds(from_owner_id: int, to_owner_id: int, amount: int):
    """انتقال الماس بین کاربران — برمی‌گردد (success: bool, message: str)"""
    try:
        balance = get_token_balance(from_owner_id)
        if balance < amount:
            return False, f'موجودی کافی ندارید! موجودی: {balance} الماس'
        result = supa_transfer_tokens(from_owner_id, to_owner_id, amount)
        if result:
            return True, f'✅ {amount} الماس با موفقیت انتقال یافت'
        return False, 'خطا در انتقال الماس'
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# 🎲 توابع قرعه‌کشی
# ══════════════════════════════════════════════════════════════════════════════
def create_lottery(chat_id, creator_tg_id, prize_amount, duration_minutes, entry_fee=1):
    """ایجاد قرعه‌کشی جدید"""
    return supa_create_lottery(chat_id, creator_tg_id, prize_amount, duration_minutes, entry_fee)


def update_lottery_message(lottery_id, message_id):
    """به‌روزرسانی پیام قرعه‌کشی"""
    return supa_update_lottery_message(lottery_id, message_id)


def get_lottery(lottery_id):
    """دریافت اطلاعات قرعه‌کشی"""
    return supa_get_lottery(lottery_id)


def get_active_lottery_by_chat(chat_id):
    """دریافت قرعه‌کشی فعال در چت"""
    return supa_get_active_lottery_by_chat(chat_id)


def join_lottery(lottery_id, user_tg_id, owner_id, entry_fee=None):
    """شرکت در قرعه‌کشی"""
    return supa_join_lottery(lottery_id, user_tg_id, owner_id, entry_fee)


def get_lottery_participants(lottery_id):
    """دریافت لیست شرکت‌کنندگان قرعه‌کشی"""
    return supa_get_lottery_participants(lottery_id)


def finish_lottery(lottery_id, winner_tg_id, winner_owner_id):
    """پایان قرعه‌کشی"""
    return supa_finish_lottery(lottery_id, winner_tg_id, winner_owner_id)


def get_expired_lotteries():
    """دریافت قرعه‌کشی‌های منقضی شده"""
    return supa_get_expired_lotteries()


def cancel_lottery(lottery_id):
    """لغو قرعه‌کشی"""
    return supa_cancel_lottery(lottery_id)


# ══════════════════════════════════════════════════════════════════════════════
# صادرات
# ══════════════════════════════════════════════════════════════════════════════
__all__ = [
    # 🆕 init_tables
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
    
    # پیام
    'save_message_slot', 'get_message_slot',
    'add_scheduled_message', 'get_pending_scheduled', 'mark_scheduled_sent',
    'log_deleted_message', 'get_deleted_messages',
    
    # سایلنت
    'add_silent_chat', 'remove_silent_chat', 'is_silent_chat',
    'add_silent_user', 'remove_silent_user', 'is_silent_user',
    
    # چنل‌های اجباری
    'get_forced_channels', 'add_forced_channel', 'remove_forced_channel', 'check_user_membership',
    
    # دشمن
    'add_enemy', 'remove_enemy', 'get_enemies', 'is_enemy', 'clear_enemies', 'get_enemy_count',
    
    # دوست
    'add_friend', 'remove_friend', 'get_friends', 'is_friend', 'clear_friends', 'get_friend_count',
    
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
    
    # قرعه‌کشی
    'create_lottery',
    'update_lottery_message',
    'get_lottery',
    'get_active_lottery_by_chat',
    'join_lottery',
    'get_lottery_participants',
    'finish_lottery',
    'get_expired_lotteries',
    'cancel_lottery',
]
