from typing import Optional, Dict, List, Any
from database_supabase import (
    init_tables as supa_init_tables,
    create_account as supa_create_account,
    verify_account as supa_verify_account,
    get_account as supa_get_account,
    get_account_by_username as supa_get_account_by_username,
    get_account_by_tg_id as supa_get_account_by_tg_id,
    get_all_accounts as supa_get_all_accounts,
    account_exists as supa_account_exists,
    save_telegram_user_id as supa_save_telegram_user_id,
    get_telegram_id_by_owner as supa_get_telegram_id_by_owner,
    save_session as supa_save_session,
    get_session as supa_get_session,
    delete_session as supa_delete_session,
    get_setting as supa_get_setting,
    set_setting as supa_set_setting,
    toggle_setting as supa_toggle_setting,
    get_all_logged_in_users as supa_get_all_logged_in_users,
    init_user_settings as supa_init_user_settings,
    get_token_balance as supa_get_token_balance,
    add_tokens as supa_add_tokens,
    deduct_tokens as supa_deduct_tokens,
    claim_daily_token as supa_claim_daily_token,
    get_token_stats as supa_get_token_stats,
    process_referral as supa_process_referral,
    get_referral_count as supa_get_referral_count,
    save_message_slot as supa_save_message_slot,
    get_message_slot as supa_get_message_slot,
    add_scheduled_message as supa_add_scheduled_message,
    get_pending_scheduled as supa_get_pending_scheduled,
    mark_scheduled_sent as supa_mark_scheduled_sent,
    log_deleted_message as supa_log_deleted_message,
    get_deleted_messages as supa_get_deleted_messages,
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
    transfer_diamonds as supa_transfer_diamonds,
    create_lottery as supa_create_lottery,
    update_lottery_message as supa_update_lottery_message,
    get_lottery as supa_get_lottery,
    get_active_lottery_by_chat as supa_get_active_lottery_by_chat,
    join_lottery as supa_join_lottery,
    get_lottery_participants as supa_get_lottery_participants,
    finish_lottery as supa_finish_lottery,
    get_expired_lotteries as supa_get_expired_lotteries,
    cancel_lottery as supa_cancel_lottery,
    SETTING_DEFAULTS,
    _hash_pw,
)

import db_cache as cache


def init_tables():
    return supa_init_tables()

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

def save_session(owner_id: int, session_data: str, phone: str = None) -> bool:
    return supa_save_session(owner_id, session_data, phone)

def get_session(owner_id: int) -> Optional[str]:
    return supa_get_session(owner_id)

def delete_session(owner_id: int) -> bool:
    return supa_delete_session(owner_id)

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

def transfer_diamonds(from_id: int, to_id: int, amount: int):
    return supa_transfer_diamonds(from_id, to_id, amount)

def transfer_tokens(from_owner_id: int, to_tg_id: int, amount: int) -> bool:
    return supa_transfer_tokens(from_owner_id, to_tg_id, amount)

def create_math_challenge(owner_id, challenge_text, correct_answer, chat_id, message_id=None):
    return supa_create_math_challenge(owner_id, challenge_text, correct_answer, chat_id, message_id)

def get_math_challenge(owner_id):
    return supa_get_math_challenge(owner_id)

def solve_math_challenge(challenge_id):
    return supa_solve_math_challenge(challenge_id)

def create_worldcup_bet(owner_id, team1, team2, match_time, photo_file_id=None):
    return supa_create_worldcup_bet(owner_id, team1, team2, match_time, photo_file_id)

def update_challenge_message(challenge_id, message_id, chat_id):
    return supa_update_challenge_message(challenge_id, message_id, chat_id)

def get_active_worldcup_bet(owner_id):
    return supa_get_active_worldcup_bet(owner_id)

def get_all_active_worldcup_bets(owner_id):
    return supa_get_all_active_worldcup_bets(owner_id)

def get_worldcup_bet_by_message(message_id, chat_id):
    return supa_get_worldcup_bet_by_message(message_id, chat_id)

def place_bet(bet_id, user_tg_id, selected_team, bet_amount):
    return supa_place_bet(bet_id, user_tg_id, selected_team, bet_amount)

def get_bet_users(bet_id):
    return supa_get_bet_users(bet_id)

def finish_worldcup_bet(bet_id, winner):
    return supa_finish_worldcup_bet(bet_id, winner)

def get_challenge_settings(owner_id):
    return supa_get_challenge_settings(owner_id)

def update_challenge_settings(owner_id, key, value):
    return supa_update_challenge_settings(owner_id, key, value)

def create_bet_game(owner_id, chat_id, player1_id, bet_amount, message_id=None):
    return supa_create_bet_game(owner_id, chat_id, player1_id, bet_amount, message_id)

def join_bet_game(game_id, player2_id):
    return supa_join_bet_game(game_id, player2_id)

def get_active_bet_game(chat_id, player_id):
    return supa_get_active_bet_game(chat_id, player_id)

def get_all_active_bet_games(chat_id):
    return supa_get_all_active_bet_games(chat_id)

def get_bet_game_by_message(chat_id, message_id):
    return supa_get_bet_game_by_message(chat_id, message_id)

def finish_bet_game(game_id, winner_id):
    return supa_finish_bet_game(game_id, winner_id)

def expire_bet_game(game_id):
    return supa_expire_bet_game(game_id)

def get_bet_game(game_id):
    return supa_get_bet_game(game_id)

def get_expired_bet_games():
    return supa_get_expired_bet_games()

def create_lottery(chat_id, creator_tg_id, prize_amount, duration_minutes, entry_fee=1):
    return supa_create_lottery(chat_id, creator_tg_id, prize_amount, duration_minutes, entry_fee)

def update_lottery_message(lottery_id, message_id):
    return supa_update_lottery_message(lottery_id, message_id)

def get_lottery(lottery_id):
    return supa_get_lottery(lottery_id)

def get_active_lottery_by_chat(chat_id):
    return supa_get_active_lottery_by_chat(chat_id)

def join_lottery(lottery_id, user_tg_id, owner_id, entry_fee=None):
    return supa_join_lottery(lottery_id, user_tg_id, owner_id, entry_fee)

def get_lottery_participants(lottery_id):
    return supa_get_lottery_participants(lottery_id)

def finish_lottery(lottery_id, winner_tg_id, winner_owner_id):
    return supa_finish_lottery(lottery_id, winner_tg_id, winner_owner_id)

def get_expired_lotteries():
    return supa_get_expired_lotteries()

def cancel_lottery(lottery_id):
    return supa_cancel_lottery(lottery_id)

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

def add_enemy(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_enemy(owner_id, user_id, username, name)

def remove_enemy(owner_id: int, user_id: int) -> bool:
    return cache.remove_enemy(owner_id, user_id)

def get_enemies(owner_id: int):
    return cache.get_enemies(owner_id)

def is_enemy(owner_id: int, user_id: int) -> bool:
    return cache.is_enemy(owner_id, user_id)

def clear_enemies(owner_id: int):
    cache.clear_enemies(owner_id)

def get_enemy_count(owner_id: int) -> int:
    return cache.get_enemy_count(owner_id)

def add_friend(owner_id: int, user_id: int, username=None, name=None):
    return cache.add_friend(owner_id, user_id, username, name)

def remove_friend(owner_id: int, user_id: int) -> bool:
    return cache.remove_friend(owner_id, user_id)

def get_friends(owner_id: int):
    return cache.get_friends(owner_id)

def is_friend(owner_id: int, user_id: int) -> bool:
    return cache.is_friend(owner_id, user_id)

def clear_friends(owner_id: int):
    cache.clear_friends(owner_id)

def get_friend_count(owner_id: int) -> int:
    return cache.get_friend_count(owner_id)
