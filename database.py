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
    get_active_worldcup_bet as supa_get_active_worldcup_bet,
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
    SETTING_DEFAULTS,
    _hash_pw,
)

# ─── ایمپورت از دیتابیس کش (SQLite) ──────────────────────────────────────────
import db_cache as cache

# ─── توابع دیتابیس پایدار ──────────────────────────────────────────────────────
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

# ─── توابع تنظیمات ─────────────────────────────────────────────────────────────
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

# ─── توابع توکن ────────────────────────────────────────────────────────────────
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

# ─── 📋 توابع دشمن (ذخیره در دیتابیس کش) ──────────────────────────────────────
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

# ─── 📋 توابع دوست (ذخیره در دیتابیس کش) ──────────────────────────────────────
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

# ─── توابع پیام ────────────────────────────────────────────────────────────────
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

# ─── ✅ توابع سایلنت (دیتابیس کش) ──────────────────────────────────────────────
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

# ─── ✅ توابع چنل‌های اجباری (دیتابیس کش) ─────────────────────────────────────
def get_forced_channels():
    return cache.get_forced_channels()

def add_forced_channel(username: str) -> bool:
    return cache.add_forced_channel(username)

def remove_forced_channel(username: str) -> bool:
    return cache.remove_forced_channel(username)

def check_user_membership(bot, user_id: int) -> tuple:
    return cache.check_user_membership(bot, user_id)

# ─── ✅ توابع چالش ─────────────────────────────────────────────────────────────
def create_math_challenge(owner_id: int, challenge_text: str, correct_answer: str, chat_id: int, message_id: int = None):
    return supa_create_math_challenge(owner_id, challenge_text, correct_answer, chat_id, message_id)

def get_math_challenge(owner_id: int):
    return supa_get_math_challenge(owner_id)

def solve_math_challenge(challenge_id: int):
    return supa_solve_math_challenge(challenge_id)

def create_worldcup_bet(owner_id: int, team1: str, team2: str, match_time: str, photo_file_id: str = None):
    return supa_create_worldcup_bet(owner_id, team1, team2, match_time, photo_file_id)

def get_active_worldcup_bet(owner_id: int):
    return supa_get_active_worldcup_bet(owner_id)

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

# ─── ✅ توابع شرط‌بندی دو نفره ──────────────────────────────────────────────────
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

# ─── صادرات ────────────────────────────────────────────────────────────────────
__all__ = [
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
    'create_worldcup_bet', 'get_active_worldcup_bet', 'place_bet',
    'get_bet_users', 'finish_worldcup_bet',
    'get_challenge_settings', 'update_challenge_settings',
    
    # شرط‌بندی دو نفره
    'create_bet_game', 'join_bet_game', 'get_active_bet_game',
    'get_all_active_bet_games', 'get_bet_game_by_message',
    'finish_bet_game', 'expire_bet_game', 'get_bet_game',
    'get_expired_bet_games', 'transfer_tokens',
]
