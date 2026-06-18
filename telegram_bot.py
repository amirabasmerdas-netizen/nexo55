# ══════════════════════════════════════════════════════════════════════════════
# telegram_bot.py - نسخه کامل با همه قابلیت‌ها
# ══════════════════════════════════════════════════════════════════════════════

import threading
import time
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)
import database as db
import db_cache as cache
import config
import datetime
import random
import asyncio
import logging
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = config.OWNER_TG_ID

# ─── کش کاربران ──────────────────────────────────────────────────────────────
_user_cache = {}
_user_cache_time = {}
_CACHE_TTL = 60


def get_user_account(tg_id: int):
    now = time.time()
    if tg_id in _user_cache:
        if now - _user_cache_time.get(tg_id, 0) < _CACHE_TTL:
            return _user_cache[tg_id]
    account = db.get_account_by_tg_id(tg_id)
    _user_cache[tg_id] = account
    _user_cache_time[tg_id] = now
    return account


def clear_user_cache(tg_id: int = None):
    if tg_id:
        _user_cache.pop(tg_id, None)
        _user_cache_time.pop(tg_id, None)
    else:
        _user_cache.clear()
        _user_cache_time.clear()


# ─── State Machine برای ثبت‌نام و اتصال ─────────────────────────────────────
_signup_states = {}  # {tg_id: {"state": "...", "data": {...}}}
_telethon_loop = None
_telethon_clients = {}
_phone_hashes = {}
_phone_numbers = {}

# ─── دیکشنری برای نگهداری کد موقت کاربران ───
_temp_codes = {}  # {tg_id: {"code": "", "phone": "", "hash": "", "partial_sess": "", "username": "", "password": "", "mode": "signup"}}

# ─── State برای پنل مدیریت ──────────────────────────────────────────────────
_owner_states = {}
_lottery_players = {}
_tictac_games = {}  # {chat_id: {"board": [], "players": [], "turn": 0, "message_id": 0}}


def _get_telethon_loop():
    global _telethon_loop
    if _telethon_loop is None or _telethon_loop.is_closed():
        _telethon_loop = asyncio.new_event_loop()
        t = threading.Thread(target=_telethon_loop.run_forever, daemon=True)
        t.start()
    return _telethon_loop


def _run_telethon_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _get_telethon_loop()).result(timeout=60)


def get_bot():
    return _bot


# ─── ساخت کیبورد عددی برای وارد کردن کد ───
def get_code_keyboard(current_code=""):
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    buttons = []
    for i in range(1, 10):
        buttons.append(types.InlineKeyboardButton(str(i), callback_data=f"code_{i}"))
    markup.add(*buttons)
    
    markup.add(
        types.InlineKeyboardButton("0", callback_data="code_0"),
        types.InlineKeyboardButton("⌫", callback_data="code_backspace"),
        types.InlineKeyboardButton("🗑", callback_data="code_clear")
    )
    
    display_code = current_code if current_code else "____"
    markup.add(
        types.InlineKeyboardButton(f"📱 {display_code}", callback_data="code_display")
    )
    markup.add(
        types.InlineKeyboardButton("✅ تأیید", callback_data="code_confirm"),
        types.InlineKeyboardButton("❌ لغو", callback_data="code_cancel")
    )
    
    return markup


def send_code_with_keyboard(chat_id, tg_id, phone, partial_sess, phone_hash, username, password):
    _temp_codes[tg_id] = {
        "code": "",
        "phone": phone,
        "hash": phone_hash,
        "partial_sess": partial_sess,
        "username": username,
        "password": password,
        "mode": "signup"
    }
    
    markup = get_code_keyboard("")
    
    _bot.send_message(
        chat_id,
        "📱 <b>مرحله ۴ از ۴: ورود کد تأیید</b>\n\n"
        "🔐 کد ۵ رقمی به تلگرام شما ارسال شد.\n"
        "👇 با کلیک روی دکمه‌های زیر، کد را وارد کنید:\n\n"
        "⚠️ کد هرگز به‌صورت پیام متنی نمایش داده نمی‌شود.\n"
        "⏰ ۵ دقیقه فرصت دارید.",
        reply_markup=markup,
        parse_mode="HTML"
    )


def start_token_bot():
    global _bot, BOT_USERNAME

    if not config.BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN تنظیم نشده — ربات غیرفعال است")
        return

    _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4)

    try:
        me = _bot.get_me()
        BOT_USERNAME = me.username
        logger.info(f"🤖 ربات مدیریت: @{BOT_USERNAME}")
    except Exception as e:
        logger.error(f"❌ خطا در اتصال ربات: {e}")
        _bot = None
        return

    for _ in range(3):
        try:
            _bot.delete_webhook(drop_pending_updates=True)
            time.sleep(2)
            break
        except:
            time.sleep(2)

    # ─── توابع کمکی ───────────────────────────────────────────────────────────
    def send_forced_channels_menu(message, missing_channels):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for ch in missing_channels:
            ch_clean = ch.lstrip("@")
            markup.add(types.InlineKeyboardButton(f"📢 عضویت در {ch}", url=f"https://t.me/{ch_clean}"))
        markup.add(types.InlineKeyboardButton("✅ بررسی عضویت من", callback_data="check_join"))
        
        channels_list = "\n".join([f"🔸 {ch}" for ch in missing_channels])
        _bot.reply_to(
            message,
            "⛔️ <b>ورود به ربات منوط به عضویت در کانال‌های زیر است:</b>\n\n"
            f"{channels_list}\n\n"
            "👇 روی هر کانال کلیک کنید و Join بزنید، سپس دکمه «بررسی عضویت من» را بزنید:",
            reply_markup=markup
        )

    def require_membership(message):
        if message.chat.type != 'private':
            return True
        is_member, missing = cache.check_user_membership(_bot, message.from_user.id)
        if not is_member:
            send_forced_channels_menu(message, missing)
            return False
        return True

    def _user_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("📖 راهنما", "👤 پروفایل من")
        markup.add("🔄 به‌روزرسانی منو")
        return markup

    def _owner_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("🎯 چالش‌ها", "📢 پیام عمومی")
        markup.add("🏆 اعلام برنده", "👤 پروفایل من")
        markup.add("📖 راهنما", "🔄 به‌روزرسانی منو")
        return markup

    def _self_settings_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
        markup.add("🗑️ حذف سلف", "📊 وضعیت سلف")
        markup.add("🔙 بازگشت به منو")
        return markup

    def _settings_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
        markup.add("🤖 منشی", "🛡️ امنیت")
        markup.add("⚡ اتوماسیون", "🔤 فونت")
        markup.add("📋 لیست‌ها", "🗑️ حذف سلف")
        markup.add("🔙 بازگشت")
        return markup

    def _security_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🛡️ ضد حذف روشن", "🛡️ ضد حذف خاموش")
        markup.add("🔗 ضد لینک روشن", "🔗 ضد لینک خاموش")
        markup.add("🔒 قفل پیوی روشن", "🔒 قفل پیوی خاموش")
        markup.add("⚔️ پاسخ دشمن روشن", "⚔️ پاسخ دشمن خاموش")
        markup.add("🔙 بازگشت")
        return markup

    def _automation_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👁️ سین خودکار روشن", "👁️ سین خودکار خاموش")
        markup.add("❤️ ری‌اکشن روشن", "❤️ ری‌اکشن خاموش")
        markup.add("💾 ذخیره مدیا روشن", "💾 ذخیره مدیا خاموش")
        markup.add("⏰ ساعت نام روشن", "⏰ ساعت نام خاموش")
        markup.add("⏰ ساعت بیو روشن", "⏰ ساعت بیو خاموش")
        markup.add("🔙 بازگشت")
        return markup

    def _lists_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👤 مدیریت دشمن", "💚 مدیریت دوست")
        markup.add("🔙 بازگشت")
        return markup

    def _enemy_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دشمن", "❌ حذف دشمن")
        markup.add("📋 نمایش دشمن‌ها", "🗑️ پاک کردن دشمن‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def _friend_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دوست", "❌ حذف دوست")
        markup.add("📋 نمایش دوست‌ها", "🗑️ پاک کردن دوست‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def _font_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=4)
        markup.add("فونت 0", "فونت 1", "فونت 2", "فونت 3")
        markup.add("فونت 4", "فونت 5", "فونت 6", "فونت 7")
        markup.add("فونت 8", "📝 لیست فونت", "🔙 بازگشت")
        return markup

    def _challenges_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🧮 چالش ریاضی", "⚽ پیش‌بینی جام جهانی")
        markup.add("🎮 بازی دوز", "🎲 قرعه‌کشی")
        markup.add("🔙 بازگشت")
        return markup

    def _admin_panel_keyboard():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("📢 چنل‌های اجباری", callback_data="admin_channels"),
            types.InlineKeyboardButton("👥 کاربران", callback_data="admin_users")
        )
        markup.add(
            types.InlineKeyboardButton("🏆 جام جهانی", callback_data="admin_wc"),
            types.InlineKeyboardButton("🎲 قرعه‌کشی (مالک)", callback_data="admin_lottery")
        )
        markup.add(
            types.InlineKeyboardButton("💎 انتقال الماس", callback_data="admin_transfer"),
            types.InlineKeyboardButton("💰 دادن الماس", callback_data="admin_give")
        )
        markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
        return markup

    # ══════════════════════════════════════════════════════════════════════════
    # /start
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id
            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None
            
            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    threading.Thread(target=_process_referral_async, args=(referrer_id, tg_id), daemon=True).start()
                except: 
                    pass

            if not require_membership(message):
                return

            account = get_user_account(tg_id)

            if not account:
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("🤖 ثبت‌نام با ربات", callback_data="signup_bot"))
                markup.add(types.InlineKeyboardButton("🌐 ثبت‌نام با سایت (غیرفعال)", callback_data="signup_site_disabled"))
                
                _bot.reply_to(
                    message,
                    "👋 <b>سلام!</b>\n\n"
                    "برای استفاده از ربات، ابتدا ثبت‌نام کنید:\n\n"
                    "🤖 <b>ثبت‌نام با ربات:</b> سریع و آسان\n"
                    "🌐 <b>ثبت‌نام با سایت:</b> در حال حاضر غیرفعال",
                    reply_markup=markup
                )
                return

            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔗 اتصال به تلگرام", callback_data="connect_telegram"))
                _bot.reply_to(
                    message,
                    f"👋 سلام <b>{account['username']}</b>!\n\n"
                    "⚠️ حساب تلگرام شما متصل نیست!\n\n"
                    "برای ادامه، روی دکمه زیر کلیک کنید:",
                    reply_markup=markup
                )
                return

            stats = db.get_token_stats(account["id"])
            
            if message.chat.type == 'private':
                markup = _owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
            else:
                markup = None

            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            _bot.reply_to(
                message,
                f"👋 سلام <b>{account['username']}</b>!\n\n"
                f"💎 موجودی: <b>{stats['balance']}</b>\n"
                f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n\n"
                f"⚡ هر <b>{config.TOKENS_PER_SESSION} الماس</b> = <b>{config.SESSION_HOURS} ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=markup
            )

            if message.chat.type == 'private':
                sponsors = getattr(config, 'SPONSORS', [])
                if sponsors:
                    sponsors_text = "🤝 <b>اسپانسرهای رسمی پروژه:</b>\n"
                    for sp in sponsors:
                        sponsors_text += f"🔸 @{sp['username']}\n"
                    sponsors_text += f"\n👑 <b>مالک و پشتیبانی:</b> @{config.OWNER_USERNAME}"
                    _bot.send_message(message.chat.id, sponsors_text)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_start: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 پنل مدیریت سلف
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "⚙️ تنظیمات سلف", chat_types=['private'])
    def cmd_self_settings(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            settings = {
                "self_bot_active": db.get_setting(account["id"], "self_bot_active", "0"),
                "secretary_active": db.get_setting(account["id"], "secretary_active", "0"),
                "anti_delete_active": db.get_setting(account["id"], "anti_delete_active", "0"),
                "anti_link_active": db.get_setting(account["id"], "anti_link_active", "0"),
                "auto_seen_active": db.get_setting(account["id"], "auto_seen_active", "0"),
                "auto_reaction_active": db.get_setting(account["id"], "auto_reaction_active", "0"),
                "private_lock_active": db.get_setting(account["id"], "private_lock_active", "0"),
                "enemy_reply_active": db.get_setting(account["id"], "enemy_reply_active", "0"),
                "auto_save_media": db.get_setting(account["id"], "auto_save_media", "0"),
                "clock_name_active": db.get_setting(account["id"], "clock_name_active", "0"),
                "clock_bio_active": db.get_setting(account["id"], "clock_bio_active", "0"),
                "selected_font": db.get_setting(account["id"], "selected_font", "0"),
            }
            
            status_text = "⚙️ <b>پنل مدیریت سلف</b>\n\n"
            status_text += f"🟢 سلف‌بات: {'✅ فعال' if settings['self_bot_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"🤖 منشی: {'✅ فعال' if settings['secretary_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"🛡️ ضد حذف: {'✅ فعال' if settings['anti_delete_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"🔗 ضد لینک: {'✅ فعال' if settings['anti_link_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"🔒 قفل پیوی: {'✅ فعال' if settings['private_lock_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"⚔️ پاسخ دشمن: {'✅ فعال' if settings['enemy_reply_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"👁️ سین خودکار: {'✅ فعال' if settings['auto_seen_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"❤️ ری‌اکشن: {'✅ فعال' if settings['auto_reaction_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"💾 ذخیره مدیا: {'✅ فعال' if settings['auto_save_media'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"⏰ ساعت نام: {'✅ فعال' if settings['clock_name_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"⏰ ساعت بیو: {'✅ فعال' if settings['clock_bio_active'] == '1' else '❌ غیرفعال'}\n"
            status_text += f"\n🔤 فونت: {settings['selected_font']}"
            
            _bot.reply_to(message, status_text, reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_self_settings: {e}")

    # ─── دکمه‌های تنظیمات سلف ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🟢 سلف روشن", chat_types=['private'])
    def cmd_start_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_settings_keyboard())
            
            from bot import bot_manager
            try: loop = asyncio.get_event_loop()
            except:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            ok = bot_manager.start(account["id"], loop, check_tokens=True)
            if ok:
                db.set_setting(account["id"], "self_bot_active", "1")
                _bot.reply_to(message, "✅ سلف‌بات روشن شد!", reply_markup=_settings_keyboard())
            else:
                balance = db.get_token_balance(account["id"])
                price = getattr(config, 'TOKENS_PER_SESSION', 2)
                _bot.reply_to(
                    message,
                    f"❌ الماس کافی ندارید!\n💎 موجودی: {balance}\n⚡ نیاز: {price} الماس",
                    reply_markup=_settings_keyboard()
                )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_start_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_settings_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "🔴 سلف خاموش", chat_types=['private'])
    def cmd_stop_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_settings_keyboard())
            
            from bot import bot_manager
            bot_manager.stop(account["id"])
            db.set_setting(account["id"], "self_bot_active", "0")
            _bot.reply_to(message, "❌ سلف‌بات خاموش شد.", reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_stop_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_settings_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "🗑️ حذف سلف", chat_types=['private'])
    def cmd_remove_self(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_settings_keyboard())
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_remove_self_{account['id']}"),
                types.InlineKeyboardButton("❌ لغو", callback_data="cancel_remove_self")
            )
            
            _bot.reply_to(
                message,
                "⚠️ <b>هشدار! حذف سلف</b>\n\n"
                "با حذف سلف:\n"
                "• از حساب تلگرام خود خارج می‌شوید\n"
                "• تمام جلسات فعال پایان می‌یابد\n"
                "• باید دوباره وارد حساب تلگرام شوید\n\n"
                "آیا مطمئن هستید؟",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_remove_self: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_settings_keyboard())

    # ─── Callback برای تأیید حذف سلف ──────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_remove_self_"))
    def callback_confirm_remove_self(call):
        try:
            owner_id = int(call.data.split("_")[3])
            tg_id = call.from_user.id
            
            account = get_user_account(tg_id)
            if not account or account["id"] != owner_id:
                return _bot.answer_callback_query(call.id, "❌ خطا: کاربر یافت نشد", show_alert=True)
            
            # 1. توقف سلف‌بات
            try:
                from bot import bot_manager
                bot_manager.stop(owner_id)
            except:
                pass
            
            # 2. حذف سشن از دیتابیس
            db.delete_session(owner_id)
            
            # 3. غیرفعال کردن وضعیت لاگین
            db.set_setting(owner_id, "logged_in", "0")
            
            # 4. غیرفعال کردن سلف
            db.set_setting(owner_id, "self_bot_active", "0")
            
            _bot.answer_callback_query(call.id, "✅ سلف با موفقیت حذف شد!", show_alert=True)
            
            try:
                _bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            
            # ارسال پیام موفقیت
            _bot.send_message(
                call.message.chat.id,
                "🗑️ <b>سلف با موفقیت حذف شد!</b>\n\n"
                "شما از حساب تلگرام خود خارج شدید.\n"
                "برای استفاده مجدد، لطفاً دوباره وارد شوید.\n\n"
                "🔗 روی دکمه «اتصال به تلگرام» کلیک کنید.",
                reply_markup=_user_keyboard() if tg_id != OWNER_TG_ID else _owner_keyboard()
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔗 اتصال به تلگرام", callback_data="connect_telegram"))
            _bot.send_message(
                call.message.chat.id,
                "🔗 برای اتصال مجدد به تلگرام، روی دکمه زیر کلیک کنید:",
                reply_markup=markup
            )
            
        except Exception as e:
            logger.error(f"❌ خطا در callback_confirm_remove_self: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    @_bot.callback_query_handler(func=lambda call: call.data == "cancel_remove_self")
    def callback_cancel_remove_self(call):
        try:
            _bot.answer_callback_query(call.id, "❌ عملیات لغو شد.")
            try:
                _bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            _bot.send_message(
                call.message.chat.id,
                "🔙 عملیات حذف سلف لغو شد.",
                reply_markup=_settings_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در callback_cancel_remove_self: {e}")

    # ─── دکمه بازگشت ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔙 بازگشت به منو", chat_types=['private'])
    def cmd_back_to_menu(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=types.ReplyKeyboardRemove())
            
            is_owner = (message.from_user.id == OWNER_TG_ID)
            _bot.reply_to(
                message,
                "🔙 بازگشت به منوی اصلی.",
                reply_markup=_owner_keyboard() if is_owner else _user_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_back_to_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔙 بازگشت", chat_types=['private'])
    def cmd_back(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=types.ReplyKeyboardRemove())
            
            is_owner = (message.from_user.id == OWNER_TG_ID)
            _bot.reply_to(
                message,
                "🔙 بازگشت به تنظیمات سلف.",
                reply_markup=_settings_keyboard() if not is_owner else _settings_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_back: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 چالش‌ها و سرگرمی‌ها
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "🎯 چالش‌ها", chat_types=['private'])
    def cmd_challenges(message):
        try:
            if message.from_user.id != OWNER_TG_ID:
                _bot.reply_to(message, "⛔ این بخش فقط برای مالک است.")
                return
            _bot.reply_to(
                message,
                "🎯 <b>پنل مدیریت چالش‌ها و سرگرمی‌ها</b>\n\n"
                "🧮 <b>چالش ریاضی:</b> هر ۲ ساعت یکبار در گروه\n"
                "⚽ <b>پیش‌بینی جام جهانی:</b> شرط‌بندی روی مسابقات\n"
                "🎮 <b>بازی دوز:</b> بازی دو نفره در گروه\n"
                "🎲 <b>قرعه‌کشی:</b> قرعه‌کشی با الماس",
                reply_markup=_challenges_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ cmd_challenges error: {e}")

    # ─── چالش ریاضی ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🧮 چالش ریاضی", chat_types=['private'])
    def cmd_math_challenge(message):
        try:
            if message.from_user.id != OWNER_TG_ID: return
            settings = db.get_challenge_settings(1)
            current = settings.get('math_challenge_active', False)
            db.update_challenge_settings(1, 'math_challenge_active', not current)
            status = "🟢 فعال" if not current else "🔴 غیرفعال"
            _bot.reply_to(
                message,
                f"🧮 <b>چالش ریاضی</b>\n\nوضعیت: {status}\n📅 هر ۲ ساعت یکبار در گروه ارسال می‌شود.\n💰 جایزه: ۱ الماس به پاسخ‌دهنده صحیح",
                reply_markup=_challenges_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ cmd_math_challenge error: {e}")

    # ─── جام جهانی ───────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "⚽ پیش‌بینی جام جهانی", chat_types=['private'])
    def cmd_worldcup(message):
        try:
            if message.from_user.id != OWNER_TG_ID: return
            _owner_states[message.chat.id] = {'step': 'team1'}
            msg = _bot.reply_to(
                message,
                "⚽ <b>ایجاد چالش جدید جام جهانی</b>\n\n📝 نام تیم اول را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_team1)
        except Exception as e:
            logger.error(f"❌ cmd_worldcup error: {e}")

    def process_worldcup_team1(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_owner_keyboard())
                return
            team1 = message.text.strip()
            _owner_states[message.chat.id] = {'step': 'team2', 'team1': team1}
            msg = _bot.reply_to(
                message,
                f"⚽ تیم اول: <b>{team1}</b>\n\n📝 نام تیم دوم را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_team2, team1)
        except Exception as e:
            logger.error(f"❌ process_worldcup_team1 error: {e}")

    def process_worldcup_team2(message, team1):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_owner_keyboard())
                return
            team2 = message.text.strip()
            _owner_states[message.chat.id] = {'step': 'time', 'team1': team1, 'team2': team2}
            msg = _bot.reply_to(
                message,
                f"⚽ تیم اول: <b>{team1}</b>\n⚽ تیم دوم: <b>{team2}</b>\n\n🕐 ساعت بازی را وارد کنید (مثال: 21:30):",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_time, team1, team2)
        except Exception as e:
            logger.error(f"❌ process_worldcup_team2 error: {e}")

    def process_worldcup_time(message, team1, team2):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_owner_keyboard())
                return
            match_time = message.text.strip()
            if not re.match(r'^\d{2}:\d{2}$', match_time):
                _bot.reply_to(
                    message,
                    "❌ فرمت ساعت اشتباه است!\nلطفاً ساعت را به فرمت <code>HH:MM</code> وارد کنید.\nمثال: <code>21:30</code>",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
                )
                return
            iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
            today = datetime.datetime.now(iran_tz).date()
            full_datetime = f"{today.isoformat()} {match_time}:00"
            _owner_states[message.chat.id] = {'step': 'bet', 'team1': team1, 'team2': team2, 'time': full_datetime}
            msg = _bot.reply_to(
                message,
                f"⚽ <b>اطلاعات مسابقه</b>\n\nتیم اول: <b>{team1}</b>\nتیم دوم: <b>{team2}</b>\nزمان: <b>{match_time}</b> (به وقت ایران)\n\n💎 مبلغ شرط (الماس) را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_bet, team1, team2, full_datetime)
        except Exception as e:
            logger.error(f"❌ process_worldcup_time error: {e}")

    def process_worldcup_bet(message, team1, team2, match_time):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_owner_keyboard())
                return
            try:
                bet_amount = int(message.text.strip())
                if bet_amount <= 0:
                    raise ValueError
            except:
                _bot.reply_to(
                    message,
                    "❌ مبلغ باید عدد مثبت باشد.\nدوباره تلاش کنید:",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
                )
                return
            
            bet_id = db.create_worldcup_bet(1, team1, team2, match_time, None)
            if not bet_id:
                _bot.reply_to(message, "❌ خطا در ایجاد چالش.", reply_markup=_owner_keyboard())
                return
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(f"⚽ {team1}", callback_data=f"bet_wc_{bet_id}_{team1}"),
                types.InlineKeyboardButton(f"⚽ {team2}", callback_data=f"bet_wc_{bet_id}_{team2}")
            )
            
            caption = (f"⚽ <b>مسابقه جام جهانی</b>\n\n🆚 <b>{team1}</b> vs <b>{team2}</b>\n"
                      f"🕐 زمان: {match_time}\n💎 مبلغ شرط: <b>{bet_amount} الماس</b>\n\n"
                      f"کدام تیم برنده می‌شود؟ شرط ببندید!")
            
            target_chat = "@Gp_SelfNexo"
            try:
                sent = _bot.send_message(target_chat, caption, reply_markup=markup)
                db.update_challenge_message(bet_id, sent.message_id, sent.chat.id)
                _bot.reply_to(message, f"✅ چالش ایجاد شد!\n\n🆚 {team1} vs {team2}\n🕐 {match_time}\n💎 {bet_amount} الماس", reply_markup=_owner_keyboard())
            except Exception as e:
                _bot.reply_to(message, f"❌ خطا در ارسال به گروه: {e}", reply_markup=_owner_keyboard())
        except Exception as e:
            logger.error(f"❌ process_worldcup_bet error: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith('bet_wc_'))
    def callback_wc_bet(call):
        try:
            parts = call.data.split('_')
            if len(parts) < 4:
                _bot.answer_callback_query(call.id, "❌ لینک نامعتبر.", show_alert=True)
                return
            bet_id = int(parts[2])
            selected_team = parts[3]
            
            bet = db.get_worldcup_bet_by_message(call.message.message_id, call.message.chat.id)
            if not bet:
                bet = db.get_bet_game(bet_id)
            if not bet:
                _bot.answer_callback_query(call.id, "❌ این چالش منقضی شده است.", show_alert=True)
                return
            if bet.get('is_finished', False):
                _bot.answer_callback_query(call.id, "❌ این مسابقه به پایان رسیده است.", show_alert=True)
                return
            
            tg_id = call.from_user.id
            account = db.get_account_by_tg_id(tg_id)
            if not account:
                _bot.answer_callback_query(call.id, "❌ ابتدا در پنل ثبت‌نام کنید.", show_alert=True)
                return
            
            msg = _bot.send_message(
                call.message.chat.id,
                f"⚽ <b>شرط‌بندی</b>\n\nتیم انتخاب شده: <b>{selected_team}</b>\n💰 میزان الماس خود را وارد کنید:\n\n"
                f"📊 موجودی شما: {db.get_token_balance(account['id'])} الماس\n💡 عدد 0 برای لغو",
                reply_to_message_id=call.message.message_id
            )
            _bot.register_next_step_handler(msg, process_wc_bet_amount, bet['id'], tg_id, selected_team)
            _bot.answer_callback_query(call.id, "✅ انتخاب ثبت شد!")
        except Exception as e:
            logger.error(f"❌ callback_wc_bet error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    def process_wc_bet_amount(message, bet_id, user_tg_id, selected_team):
        try:
            try:
                amount = int(message.text.strip())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک عدد معتبر وارد کنید.")
                return
            if amount == 0:
                _bot.reply_to(message, "❌ شرط‌بندی لغو شد.")
                return
            if amount < 0:
                _bot.reply_to(message, "❌ مقدار باید مثبت باشد.")
                return
            
            account = db.get_account_by_tg_id(user_tg_id)
            if not account:
                _bot.reply_to(message, "❌ ابتدا در پنل ثبت‌نام کنید.")
                return
            
            balance = db.get_token_balance(account['id'])
            if balance < amount:
                _bot.reply_to(message, f"❌ موجودی ناکافی!\n💎 موجودی شما: {balance} الماس\n📊 نیاز: {amount} الماس")
                return
            
            db.deduct_tokens(account['id'], amount)
            db.place_bet(bet_id, user_tg_id, selected_team, amount)
            _bot.reply_to(message, f"✅ <b>شرط شما ثبت شد!</b>\n\n⚽ تیم: <b>{selected_team}</b>\n💎 میزان: <b>{amount}</b> الماس")
        except Exception as e:
            logger.error(f"❌ process_wc_bet_amount error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}")

    # ─── بازی دوز ────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🎮 بازی دوز", chat_types=['private', 'group', 'supergroup'])
    def cmd_tictac(message):
        try:
            chat_id = message.chat.id
            if chat_id in _tictac_games:
                _bot.reply_to(message, "⚠️ در این گروه یک بازی دوز در حال اجراست!")
                return
            
            markup = types.InlineKeyboardMarkup(row_width=3)
            board = ["⬜"] * 9
            buttons = []
            for i in range(9):
                buttons.append(types.InlineKeyboardButton("⬜", callback_data=f"tictac_{i}"))
            markup.add(*buttons)
            
            sent = _bot.reply_to(
                message,
                "🎮 <b>بازی دوز</b>\n\n"
                "👤 بازیکن اول: @X\n"
                "👤 بازیکن دوم: @O\n"
                "⏳ نوبت: بازیکن اول (❌)\n\n"
                "برای شروع، روی یک خانه کلیک کنید!",
                reply_markup=markup
            )
            
            _tictac_games[chat_id] = {
                "board": [" "] * 9,
                "players": [],
                "turn": 0,
                "message_id": sent.message_id,
                "player1": None,
                "player2": None
            }
        except Exception as e:
            logger.error(f"❌ cmd_tictac error: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith('tictac_'))
    def callback_tictac(call):
        try:
            chat_id = call.message.chat.id
            if chat_id not in _tictac_games:
                _bot.answer_callback_query(call.id, "❌ این بازی منقضی شده است.", show_alert=True)
                return
            
            game = _tictac_games[chat_id]
            idx = int(call.data.split('_')[1])
            
            if game["board"][idx] != " ":
                _bot.answer_callback_query(call.id, "❌ این خانه پر است!", show_alert=True)
                return
            
            if game["player1"] is None:
                game["player1"] = call.from_user.id
                game["players"].append(call.from_user.id)
                player_symbol = "❌"
                player_name = "بازیکن اول"
            elif game["player2"] is None and call.from_user.id != game["player1"]:
                game["player2"] = call.from_user.id
                game["players"].append(call.from_user.id)
                player_symbol = "⭕"
                player_name = "بازیکن دوم"
            elif call.from_user.id == game["player1"]:
                player_symbol = "❌" if game["turn"] == 0 else "⭕"
                player_name = "بازیکن اول"
            elif call.from_user.id == game["player2"]:
                player_symbol = "❌" if game["turn"] == 0 else "⭕"
                player_name = "بازیکن دوم"
            else:
                _bot.answer_callback_query(call.id, "❌ شما در این بازی شرکت ندارید!", show_alert=True)
                return
            
            if (game["turn"] == 0 and call.from_user.id != game["player1"]) or \
               (game["turn"] == 1 and call.from_user.id != game["player2"]):
                _bot.answer_callback_query(call.id, "⏳ نوبت شما نیست!", show_alert=True)
                return
            
            game["board"][idx] = "X" if game["turn"] == 0 else "O"
            game["turn"] = 1 - game["turn"]
            
            # بررسی برنده
            winner = check_tictac_winner(game["board"])
            if winner:
                winner_id = game["player1"] if winner == "X" else game["player2"]
                winner_account = db.get_account_by_tg_id(winner_id)
                winner_name = f"@{winner_account['username']}" if winner_account else str(winner_id)
                
                _bot.edit_message_text(
                    f"🎉 <b>بازی دوز به پایان رسید!</b>\n\n"
                    f"🏆 برنده: {winner_name}\n"
                    f"🎊 تبریک می‌گوییم!",
                    chat_id=chat_id,
                    message_id=game["message_id"],
                    reply_markup=None
                )
                del _tictac_games[chat_id]
                _bot.answer_callback_query(call.id, f"🏆 برنده: {winner_name}")
                return
            
            # بررسی تساوی
            if " " not in game["board"]:
                _bot.edit_message_text(
                    "🤝 <b>بازی دوز به پایان رسید!</b>\n\n"
                    "مساوی! هیچ برنده‌ای وجود ندارد.",
                    chat_id=chat_id,
                    message_id=game["message_id"],
                    reply_markup=None
                )
                del _tictac_games[chat_id]
                _bot.answer_callback_query(call.id, "🤝 بازی مساوی شد!")
                return
            
            # نمایش تخته
            markup = types.InlineKeyboardMarkup(row_width=3)
            buttons = []
            for i in range(9):
                val = game["board"][i]
                if val == "X":
                    buttons.append(types.InlineKeyboardButton("❌", callback_data=f"tictac_{i}"))
                elif val == "O":
                    buttons.append(types.InlineKeyboardButton("⭕", callback_data=f"tictac_{i}"))
                else:
                    buttons.append(types.InlineKeyboardButton("⬜", callback_data=f"tictac_{i}"))
            markup.add(*buttons)
            
            turn_name = "بازیکن اول (❌)" if game["turn"] == 0 else "بازیکن دوم (⭕)"
            _bot.edit_message_text(
                f"🎮 <b>بازی دوز</b>\n\n"
                f"👤 بازیکن اول: @{_bot.get_chat(game['player1']).username or 'کاربر'}\n"
                f"👤 بازیکن دوم: @{_bot.get_chat(game['player2']).username or 'کاربر' if game['player2'] else 'در انتظار'}\n"
                f"⏳ نوبت: {turn_name}",
                chat_id=chat_id,
                message_id=game["message_id"],
                reply_markup=markup
            )
            _bot.answer_callback_query(call.id, "✅ حرکت ثبت شد!")
            
        except Exception as e:
            logger.error(f"❌ callback_tictac error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    def check_tictac_winner(board):
        lines = [
            [0, 1, 2], [3, 4, 5], [6, 7, 8],
            [0, 3, 6], [1, 4, 7], [2, 5, 8],
            [0, 4, 8], [2, 4, 6]
        ]
        for line in lines:
            if board[line[0]] == board[line[1]] == board[line[2]] != " ":
                return board[line[0]]
        return None

    # ─── قرعه‌کشی ────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🎲 قرعه‌کشی", chat_types=['private', 'group', 'supergroup'])
    def cmd_lottery(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            msg = _bot.reply_to(
                message,
                "🎲 <b>قرعه‌کشی</b>\n\n"
                "💎 مبلغ ورودی را وارد کنید:\n"
                "📊 موجودی شما: {db.get_token_balance(account['id'])} الماس\n"
                "💡 عدد 0 برای لغو",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
            )
            _bot.register_next_step_handler(msg, process_lottery_amount, account["id"])
        except Exception as e:
            logger.error(f"❌ cmd_lottery error: {e}")

    def process_lottery_amount(message, owner_id):
        try:
            if message.text == "❌ لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_user_keyboard())
                return
            
            try:
                prize = int(message.text.strip())
                if prize <= 0:
                    raise ValueError
            except:
                _bot.reply_to(message, "❌ مبلغ باید عدد مثبت باشد.\nدوباره تلاش کنید:")
                return
            
            account = db.get_account(owner_id)
            balance = db.get_token_balance(owner_id)
            if balance < prize:
                _bot.reply_to(message, f"❌ موجودی کافی نیست!\n💎 موجودی: {balance}\n📊 نیاز: {prize}")
                return
            
            if not db.deduct_tokens(owner_id, prize):
                _bot.reply_to(message, "❌ خطا در کسر الماس!")
                return
            
            lottery_id = db.create_lottery(
                chat_id=message.chat.id,
                creator_tg_id=message.from_user.id,
                prize_amount=prize,
                duration_minutes=2,
                entry_fee=prize
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"🎲 شرکت در قرعه‌کشی ({prize} الماس)", callback_data=f"join_lottery_{lottery_id}"))
            
            creator_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            msg = _bot.reply_to(
                message,
                f"🎉 <b>قرعه‌کشی!</b>\n\n"
                f"👤 سازنده: {creator_name}\n"
                f"💎 مبلغ ورودی: <b>{prize} الماس</b>\n"
                f"💰 مجموع جایزه: <b>{prize * 2} الماس</b>\n"
                f"👥 شرکت‌کنندگان: ۱ نفر\n\n"
                f"⏳ برای شرکت، روی دکمه زیر کلیک کنید!\n"
                f"(با ورود نفر دوم، قرعه‌کشی انجام می‌شود)",
                reply_markup=markup
            )
            
            db.update_lottery_message(lottery_id, msg.message_id)
            _lottery_players[lottery_id] = [message.from_user.id]
            
            threading.Timer(120, _auto_finish_lottery, args=[lottery_id, message.chat.id]).start()
            
        except Exception as e:
            logger.error(f"❌ process_lottery_amount error: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("join_lottery_"))
    def callback_join_lottery(call):
        try:
            lottery_id = int(call.data.split("_")[2])
            lottery = db.get_lottery(lottery_id)
            
            if not lottery or lottery["status"] != "active":
                return _bot.answer_callback_query(call.id, "❌ این قرعه‌کشی فعال نیست.", show_alert=True)
            
            if lottery_id in _lottery_players and call.from_user.id in _lottery_players[lottery_id]:
                return _bot.answer_callback_query(call.id, "❌ قبلاً ثبت‌نام کرده‌اید.", show_alert=True)
            
            if lottery["creator_tg_id"] == call.from_user.id:
                return _bot.answer_callback_query(call.id, "❌ شما سازنده هستید! منتظر نفر دوم باشید.", show_alert=True)
            
            account = get_user_account(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا ثبت‌نام کنید.", show_alert=True)
            
            entry_fee = lottery["prize_amount"]
            balance = db.get_token_balance(account["id"])
            
            if balance < entry_fee:
                return _bot.answer_callback_query(call.id, f"❌ موجودی کافی نیست! نیاز: {entry_fee} الماس", show_alert=True)
            
            if not db.deduct_tokens(account["id"], entry_fee):
                return _bot.answer_callback_query(call.id, "❌ خطا در کسر الماس!", show_alert=True)
            
            success, msg = db.join_lottery(lottery_id, call.from_user.id, account["id"], entry_fee)
            
            if success:
                if lottery_id not in _lottery_players:
                    _lottery_players[lottery_id] = []
                _lottery_players[lottery_id].append(call.from_user.id)
                
                _bot.answer_callback_query(call.id, f"✅ با {entry_fee} الماس ثبت‌نام کردید!", show_alert=True)
                
                if len(_lottery_players[lottery_id]) >= 2:
                    _finish_lottery_immediately(lottery_id, call.message.chat.id)
                else:
                    try:
                        _bot.edit_message_text(
                            f"🎉 <b>قرعه‌کشی!</b>\n\n"
                            f"💎 مبلغ ورودی: <b>{entry_fee} الماس</b>\n"
                            f"💰 مجموع جایزه: <b>{entry_fee * 2} الماس</b>\n"
                            f"👥 شرکت‌کنندگان: {len(_lottery_players[lottery_id])} نفر\n\n"
                            f"⏳ منتظر نفر دوم...",
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id
                        )
                    except:
                        pass
            else:
                _bot.answer_callback_query(call.id, msg, show_alert=True)
                
        except Exception as e:
            logger.error(f"❌ callback_join_lottery error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    def _finish_lottery_immediately(lottery_id, chat_id):
        try:
            lottery = db.get_lottery(lottery_id)
            if not lottery or lottery["status"] != "active":
                return
            
            participants = db.get_lottery_participants(lottery_id)
            if len(participants) < 2:
                return
            
            total_prize = lottery["prize_amount"] * 2
            winner = random.choice(participants)
            
            db.add_tokens(winner["owner_id"], total_prize)
            db.finish_lottery(lottery_id, winner["user_tg_id"], winner["owner_id"])
            
            try:
                winner_account = db.get_account(winner["owner_id"])
                winner_name = winner_account["username"] if winner_account else str(winner["user_tg_id"])
            except:
                winner_name = str(winner["user_tg_id"])
            
            msg_text = (
                f"🎉 <b>قرعه‌کشی به پایان رسید!</b>\n\n"
                f"🏆 برنده: <b>{winner_name}</b>\n"
                f"💎 مجموع جایزه: <b>{total_prize} الماس</b>\n"
                f"👥 شرکت‌کنندگان: {len(participants)} نفر\n\n"
                f"🎊 تبریک به برنده!"
            )
            
            if _bot:
                try:
                    _bot.send_message(chat_id, msg_text)
                    _bot.send_message(winner["user_tg_id"], f"🎉 تبریک! شما برنده شدید!\n💎 <b>{total_prize} الماس</b> به حساب شما واریز شد.")
                except Exception as e:
                    logger.error(f"❌ خطا در ارسال پیام: {e}")
            
            _lottery_players.pop(lottery_id, None)
            
        except Exception as e:
            logger.error(f"❌ _finish_lottery_immediately error: {e}")

    def _auto_finish_lottery(lottery_id, chat_id):
        try:
            lottery = db.get_lottery(lottery_id)
            if not lottery or lottery["status"] != "active":
                return
            
            participants = db.get_lottery_participants(lottery_id)
            
            if len(participants) < 2:
                db.finish_lottery(lottery_id, None, None)
                
                creator_id = lottery["creator_tg_id"]
                creator_account = db.get_account_by_tg_id(creator_id)
                if creator_account:
                    db.add_tokens(creator_account["id"], lottery["prize_amount"])
                
                if _bot:
                    _bot.send_message(
                        chat_id,
                        f"⏰ قرعه‌کشی لغو شد!\n\n❌ تعداد شرکت‌کنندگان کافی نبود.\n💎 {lottery['prize_amount']} الماس به سازنده برگشت داده شد."
                    )
            
            _lottery_players.pop(lottery_id, None)
            
        except Exception as e:
            logger.error(f"❌ _auto_finish_lottery error: {e}")

    # ─── اعلام برنده ────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🏆 اعلام برنده", chat_types=['private'])
    def cmd_announce_winner(message):
        try:
            if message.from_user.id != OWNER_TG_ID: return
            bet = db.get_active_worldcup_bet(1)
            if not bet:
                _bot.reply_to(message, "❌ هیچ مسابقه فعالی وجود ندارد.")
                return
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(f"🏆 {bet['team1']}", callback_data=f"winner_{bet['team1']}"),
                types.InlineKeyboardButton(f"🏆 {bet['team2']}", callback_data=f"winner_{bet['team2']}")
            )
            _bot.reply_to(
                message,
                f"🏆 <b>اعلام برنده مسابقه</b>\n\n"
                f"⚽ {bet['team1']} vs {bet['team2']}\n"
                f"🕐 زمان: {bet['match_time']}\n\n"
                f"تیم برنده را انتخاب کنید:",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ cmd_announce_winner error: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith('winner_'))
    def callback_winner(call):
        try:
            winner = call.data.split('_')[1]
            bet = db.get_active_worldcup_bet(1)
            if not bet:
                _bot.answer_callback_query(call.id, "❌ این مسابقه وجود ندارد.", show_alert=True)
                return
            
            bets = db.get_bet_users(bet['id'])
            if not bets:
                _bot.answer_callback_query(call.id, "❌ هیچ شرطی ثبت نشده است.", show_alert=True)
                return
            
            total_tokens = sum(b['bet_amount'] for b in bets)
            winners = [b for b in bets if b['selected_team'] == winner]
            
            if not winners:
                _bot.answer_callback_query(call.id, "❌ هیچ کاربری روی این تیم شرط نبسته است.", show_alert=True)
                return
            
            winner_amount = total_tokens // len(winners)
            for w in winners:
                account = db.get_account_by_tg_id(w['user_tg_id'])
                if account:
                    db.add_tokens(account['id'], winner_amount)
            
            db.finish_worldcup_bet(bet['id'], winner)
            
            target_chat = "@Gp_SelfNexo"
            try:
                _bot.send_message(
                    target_chat,
                    f"🏆 <b>نتیجه مسابقه</b>\n\n"
                    f"⚽ برنده: <b>{winner}</b>\n"
                    f"💎 کل الماس‌ها: <b>{total_tokens}</b>\n"
                    f"👥 تعداد برندگان: <b>{len(winners)}</b> نفر\n"
                    f"🎁 هر برنده: <b>{winner_amount}</b> الماس\n\n"
                    f"🎉 به برندگان تبریک می‌گوییم!"
                )
            except Exception as e:
                logger.error(f"❌ خطا در ارسال نتیجه: {e}")
            
            _bot.answer_callback_query(call.id, f"✅ برنده {winner} اعلام شد!")
            _bot.reply_to(call.message, f"✅ برنده <b>{winner}</b> اعلام شد!", reply_markup=_owner_keyboard())
            
        except Exception as e:
            logger.error(f"❌ callback_winner error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    # ─── پیام عمومی ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "📢 پیام عمومی", chat_types=['private'])
    def cmd_broadcast(message):
        try:
            if message.from_user.id != OWNER_TG_ID: return
            msg = _bot.reply_to(
                message,
                "📢 <b>ارسال پیام عمومی</b>\n\n✏️ متن پیام خود را وارد کنید:\n(از HTML برای فرمت‌دهی استفاده کنید)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_broadcast)
        except Exception as e:
            logger.error(f"❌ cmd_broadcast error: {e}")

    def process_broadcast(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=_owner_keyboard())
                return
            broadcast_text = message.text
            users = db.get_all_accounts()
            success_count = 0
            _bot.reply_to(message, f"⏳ در حال ارسال پیام به {len(users)} کاربر...", reply_markup=_owner_keyboard())
            for user in users:
                tg_id = db.get_telegram_id_by_owner(user['id'])
                if tg_id:
                    try:
                        _bot.send_message(tg_id, broadcast_text, parse_mode="HTML")
                        success_count += 1
                        time.sleep(0.05)
                    except:
                        pass
            _bot.send_message(
                message.chat.id,
                f"✅ <b>پیام عمومی ارسال شد!</b>\n\n📨 ارسال به: {success_count} از {len(users)} کاربر",
                reply_markup=_owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ process_broadcast error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=_owner_keyboard())

    # ─── ادامه کدهای ثبت‌نام و اتصال ─────────────────────────────────────
    # (بخش ثبت‌نام و اتصال به تلگرام از قبل در کد وجود دارد و در اینجا تکرار نمی‌شود)
    # ... (کدهای ثبت‌نام، اتصال، کد تأیید و ...)

    # ─── Callback: بررسی عضویت ──────────────────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        try:
            is_member, missing = cache.check_user_membership(_bot, call.from_user.id)
            if is_member:
                _bot.answer_callback_query(call.id, "عضویت تأیید شد! ✅")
                try:
                    _bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                cmd_start(call.message)
            else:
                _bot.answer_callback_query(
                    call.id,
                    f"هنوز در {len(missing)} کانال عضو نشده‌اید! ❌",
                    show_alert=True
                )
        except Exception as e:
            logger.error(f"❌ خطا در callback_check_join: {e}")

    # ─── پیام‌های ناشناخته ──────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: True, chat_types=['private'])
    def cmd_unknown(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.\n/start را بزنید.")
            
            is_owner = (message.from_user.id == OWNER_TG_ID)
            kb = _owner_keyboard() if is_owner else _user_keyboard()
            _bot.reply_to(
                message,
                "⚠️ دستور نامعتبر. از دکمه‌های زیر استفاده کنید:",
                reply_markup=kb
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_unknown: {e}")

    # ─── تابع رفرال ──────────────────────────────────────────────────────────
    def _process_referral_async(referrer_id, tg_id):
        try:
            if db.process_referral(referrer_id, tg_id):
                referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                if referrer_tg and _bot:
                    _bot.send_message(
                        referrer_tg,
                        f"🎉 یک نفر با لینک شما عضو شد!\n<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎"
                    )
        except Exception as e:
            logger.error(f"❌ خطا در رفرال: {e}")

    # ─── Polling ──────────────────────────────────────────────────────────────
    def _polling_loop():
        while True:
            try:
                _bot.infinity_polling(
                    timeout=20,
                    long_polling_timeout=15,
                    restart_on_change=False,
                    skip_pending=True
                )
            except Exception as e:
                if "409" in str(e):
                    time.sleep(10)
                    try:
                        _bot.delete_webhook(drop_pending_updates=True)
                    except:
                        pass
                else:
                    logger.error(f"⚠️ خطای polling: {e}")
                    time.sleep(3)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    logger.info(f"✅ ربات الماس @{BOT_USERNAME} استارت شد")
