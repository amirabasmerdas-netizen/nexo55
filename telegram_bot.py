# ══════════════════════════════════════════════════════════════════════════════
# telegram_bot.py - نسخه کامل با همه دکمه‌های فعال
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
_signup_states = {}
_telethon_loop = None
_telethon_clients = {}
_phone_hashes = {}
_phone_numbers = {}

# ─── دیکشنری برای نگهداری کد موقت کاربران ───
_temp_codes = {}

# ─── State برای پنل مدیریت ──────────────────────────────────────────────────
_owner_states = {}
_lottery_players = {}
_tictac_games = {}


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
        types.InlineKeyboardButton(f"📱 {display_code}", callback_data="code_display"),
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

    # ─── کیبوردها ────────────────────────────────────────────────────────────
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
    # 📌 هندلرهای دکمه‌های اصلی (همه دکمه‌ها)
    # ══════════════════════════════════════════════════════════════════════════

    # ─── تنظیمات سلف ────────────────────────────────────────────────────────
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

    # ─── سلف روشن ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🟢 سلف روشن", chat_types=['private'])
    def cmd_start_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_settings_keyboard())
            
            from bot import bot_manager
            try:
                loop = asyncio.get_event_loop()
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

    # ─── سلف خاموش ─────────────────────────────────────────────────────────
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

    # ─── حذف سلف ───────────────────────────────────────────────────────────
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

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_remove_self_"))
    def callback_confirm_remove_self(call):
        try:
            owner_id = int(call.data.split("_")[3])
            tg_id = call.from_user.id
            
            account = get_user_account(tg_id)
            if not account or account["id"] != owner_id:
                return _bot.answer_callback_query(call.id, "❌ خطا: کاربر یافت نشد", show_alert=True)
            
            try:
                from bot import bot_manager
                bot_manager.stop(owner_id)
            except:
                pass
            
            db.delete_session(owner_id)
            db.set_setting(owner_id, "logged_in", "0")
            db.set_setting(owner_id, "self_bot_active", "0")
            
            _bot.answer_callback_query(call.id, "✅ سلف با موفقیت حذف شد!", show_alert=True)
            
            try:
                _bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            
            _bot.send_message(
                call.message.chat.id,
                "🗑️ <b>سلف با موفقیت حذف شد!</b>\n\n"
                "شما از حساب تلگرام خود خارج شدید.\n"
                "برای استفاده مجدد، لطفاً دوباره وارد شوید.",
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

    # ─── بازگشت ─────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔙 بازگشت", chat_types=['private'])
    def cmd_back(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=types.ReplyKeyboardRemove())
            
            _bot.reply_to(
                message,
                "🔙 بازگشت به تنظیمات سلف.",
                reply_markup=_settings_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_back: {e}")

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

    # ─── منشی ──────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🤖 منشی", chat_types=['private'])
    def cmd_secretary_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_settings_keyboard())
            
            status = db.get_setting(account["id"], "secretary_active", "0")
            msg_text = db.get_setting(account["id"], "secretary_message", "در حال حاضر در دسترس نیستم.")
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(f"منشی {'روشن' if status == '1' else 'خاموش'}", "✏️ تغییر پیام منشی")
            markup.add("🔙 بازگشت")
            
            _bot.reply_to(
                message,
                f"🤖 <b>تنظیمات منشی</b>\n\nوضعیت: {'🟢 فعال' if status == '1' else '🔴 غیرفعال'}\nپیام: {msg_text}\n\n💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد.",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_secretary_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("منشی "), chat_types=['private'])
    def cmd_toggle_secretary(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = db.get_setting(account["id"], "secretary_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "secretary_active", new_status)
            _bot.reply_to(
                message,
                f"🤖 منشی {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=_settings_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_secretary: {e}")

    @_bot.message_handler(func=lambda m: m.text == "✏️ تغییر پیام منشی", chat_types=['private'])
    def cmd_change_secretary_msg(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(
                message,
                "✏️ <b>پیام جدید منشی را وارد کنید:</b>\n\n💡 می‌توانید از HTML نیز استفاده کنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت")
            )
            _bot.register_next_step_handler(msg, process_secretary_msg, account["id"])
        except Exception as e:
            logger.error(f"❌ خطا در cmd_change_secretary_msg: {e}")

    def process_secretary_msg(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به تنظیمات.", reply_markup=_settings_keyboard())
                return
            db.set_setting(owner_id, "secretary_message", message.text)
            _bot.reply_to(message, "✅ پیام منشی ذخیره شد!", reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_secretary_msg: {e}")

    # ─── امنیت ─────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🛡️ امنیت", chat_types=['private'])
    def cmd_security_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            settings = {
                "anti_delete_active": db.get_setting(account["id"], "anti_delete_active", "0"),
                "anti_link_active": db.get_setting(account["id"], "anti_link_active", "0"),
                "private_lock_active": db.get_setting(account["id"], "private_lock_active", "0"),
                "enemy_reply_active": db.get_setting(account["id"], "enemy_reply_active", "0"),
            }
            
            text = f"🛡️ <b>تنظیمات امنیتی</b>\n\n"
            text += f"🛡️ ضد حذف: {'✅ فعال' if settings['anti_delete_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"🔗 ضد لینک: {'✅ فعال' if settings['anti_link_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"🔒 قفل پیوی: {'✅ فعال' if settings['private_lock_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"⚔️ پاسخ دشمن: {'✅ فعال' if settings['enemy_reply_active'] == '1' else '❌ غیرفعال'}"
            
            _bot.reply_to(message, text, reply_markup=_security_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_security_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🛡️ ضد حذف"), chat_types=['private'])
    def cmd_toggle_anti_delete(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "anti_delete_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "anti_delete_active", new_val)
            _bot.reply_to(
                message,
                f"🛡️ ضد حذف {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_security_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_anti_delete: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🔗 ضد لینک"), chat_types=['private'])
    def cmd_toggle_anti_link(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "anti_link_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "anti_link_active", new_val)
            _bot.reply_to(
                message,
                f"🔗 ضد لینک {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_security_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_anti_link: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🔒 قفل پیوی"), chat_types=['private'])
    def cmd_toggle_private_lock(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "private_lock_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "private_lock_active", new_val)
            _bot.reply_to(
                message,
                f"🔒 قفل پیوی {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_security_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_private_lock: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("⚔️ پاسخ دشمن"), chat_types=['private'])
    def cmd_toggle_enemy_reply(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "enemy_reply_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "enemy_reply_active", new_val)
            _bot.reply_to(
                message,
                f"⚔️ پاسخ دشمن {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_security_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_enemy_reply: {e}")

    # ─── اتوماسیون ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "⚡ اتوماسیون", chat_types=['private'])
    def cmd_automation_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            settings = {
                "auto_seen_active": db.get_setting(account["id"], "auto_seen_active", "0"),
                "auto_reaction_active": db.get_setting(account["id"], "auto_reaction_active", "0"),
                "auto_save_media": db.get_setting(account["id"], "auto_save_media", "0"),
                "clock_name_active": db.get_setting(account["id"], "clock_name_active", "0"),
                "clock_bio_active": db.get_setting(account["id"], "clock_bio_active", "0"),
            }
            
            text = f"⚡ <b>تنظیمات اتوماسیون</b>\n\n"
            text += f"👁️ سین خودکار: {'✅ فعال' if settings['auto_seen_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"❤️ ری‌اکشن: {'✅ فعال' if settings['auto_reaction_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"💾 ذخیره مدیا: {'✅ فعال' if settings['auto_save_media'] == '1' else '❌ غیرفعال'}\n"
            text += f"⏰ ساعت نام: {'✅ فعال' if settings['clock_name_active'] == '1' else '❌ غیرفعال'}\n"
            text += f"⏰ ساعت بیو: {'✅ فعال' if settings['clock_bio_active'] == '1' else '❌ غیرفعال'}"
            
            _bot.reply_to(message, text, reply_markup=_automation_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_automation_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("👁️ سین خودکار"), chat_types=['private'])
    def cmd_toggle_auto_seen(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "auto_seen_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "auto_seen_active", new_val)
            _bot.reply_to(
                message,
                f"👁️ سین خودکار {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_automation_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_auto_seen: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("❤️ ری‌اکشن"), chat_types=['private'])
    def cmd_toggle_auto_reaction(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "auto_reaction_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "auto_reaction_active", new_val)
            _bot.reply_to(
                message,
                f"❤️ ری‌اکشن خودکار {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_automation_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_auto_reaction: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("💾 ذخیره مدیا"), chat_types=['private'])
    def cmd_toggle_auto_save(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "auto_save_media", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "auto_save_media", new_val)
            _bot.reply_to(
                message,
                f"💾 ذخیره مدیا {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_automation_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_auto_save: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("⏰ ساعت نام"), chat_types=['private'])
    def cmd_toggle_clock_name(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "clock_name_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "clock_name_active", new_val)
            _bot.reply_to(
                message,
                f"⏰ ساعت نام {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_automation_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_clock_name: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("⏰ ساعت بیو"), chat_types=['private'])
    def cmd_toggle_clock_bio(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current = db.get_setting(account["id"], "clock_bio_active", "0")
            new_val = "0" if current == "1" else "1"
            db.set_setting(account["id"], "clock_bio_active", new_val)
            _bot.reply_to(
                message,
                f"⏰ ساعت بیو {'روشن' if new_val == '1' else 'خاموش'} شد!",
                reply_markup=_automation_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_toggle_clock_bio: {e}")

    # ─── فونت ──────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔤 فونت", chat_types=['private'])
    def cmd_font_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            current_font = db.get_setting(account["id"], "selected_font", "0")
            text = f"🔤 <b>انتخاب فونت</b>\n\nفونت فعلی: <b>{current_font}</b>\n\n💡 برای تغییر، روی یک دکمه کلیک کنید."
            _bot.reply_to(message, text, reply_markup=_font_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_font_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("فونت ") and len(m.text) <= 7, chat_types=['private'])
    def cmd_set_font(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            font_id = message.text.split()[-1]
            if font_id in ["0", "1", "2", "3", "4", "5", "6", "7", "8"]:
                db.set_setting(account["id"], "selected_font", font_id)
                _bot.reply_to(message, f"✅ فونت {font_id} انتخاب شد!", reply_markup=_font_keyboard())
            else:
                _bot.reply_to(message, "❌ شماره فونت باید بین ۰ تا ۸ باشد.", reply_markup=_font_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_set_font: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📝 لیست فونت", chat_types=['private'])
    def cmd_list_fonts(message):
        try:
            if not require_membership(message): return
            test_text = "امیر"
            samples = {
                "0": "متن عادی", "1": "𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀", "2": "𝘐𝘵𝘢𝘭𝘪𝘤 𝘚𝘢𝘯𝘴",
                "3": "𝙼𝚘𝚗𝚘𝚜𝚙𝚊𝚌𝚎", "4": "Ｆｕｌｌｗｉｄｔｈ", "5": "𝐒𝐞𝐫𝐟 𝐁𝐨𝐥𝐝",
                "6": "𝒮𝒸𝓇𝓅𝓉", "7": "S̶t̶r̶i̶k̶e̶t̶h̶r̶o̶u̶g̶h̶", "8": "U̲n̲d̲e̲r̲l̲i̲n̲e̲"
            }
            
            from bot import FONTS
            lines = ["📝 <b>لیست فونت‌ها با نمونه:</b>\n"]
            for k, v in samples.items():
                fn = FONTS.get(k, FONTS["0"])
                converted = fn(test_text)
                lines.append(f"<b>فونت {k}</b> — {v}: `{converted}`")
            lines.append("\n💡 استفاده: <code>فونت [شماره]</code>")
            lines.append("مثال: <code>فونت 3</code>")
            
            _bot.reply_to(message, "\n".join(lines), reply_markup=_font_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_list_fonts: {e}")

    # ─── لیست‌ها ────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "📋 لیست‌ها", chat_types=['private'])
    def cmd_lists_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            enemy_count = len(db.get_enemies(account["id"]))
            friend_count = len(db.get_friends(account["id"]))
            text = f"📋 <b>مدیریت لیست‌ها</b>\n\n👥 دشمن: <b>{enemy_count}</b> نفر\n💚 دوست: <b>{friend_count}</b> نفر"
            _bot.reply_to(message, text, reply_markup=_lists_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_lists_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "👤 مدیریت دشمن", chat_types=['private'])
    def cmd_enemy_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            enemies = db.get_enemies(account["id"])
            text = f"👤 <b>مدیریت دشمن</b>\n\n"
            if enemies:
                text += f"تعداد: <b>{len(enemies)}</b> نفر\n\n"
                for i, e in enumerate(enemies[:5], 1):
                    text += f"{i}. {e.get('name') or e.get('username') or e.get('user_id')}\n"
                if len(enemies) > 5:
                    text += f"\nو {len(enemies) - 5} نفر دیگر..."
            else:
                text += "📭 لیست دشمن خالی است."
            _bot.reply_to(message, text, reply_markup=_enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_enemy_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "💚 مدیریت دوست", chat_types=['private'])
    def cmd_friend_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            friends = db.get_friends(account["id"])
            text = f"💚 <b>مدیریت دوست</b>\n\n"
            if friends:
                text += f"تعداد: <b>{len(friends)}</b> نفر\n\n"
                for i, f in enumerate(friends[:5], 1):
                    text += f"{i}. {f.get('name') or f.get('username') or f.get('user_id')}\n"
                if len(friends) > 5:
                    text += f"\nو {len(friends) - 5} نفر دیگر..."
            else:
                text += "📭 لیست دوست خالی است."
            _bot.reply_to(message, text, reply_markup=_friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_friend_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "➕ افزودن دشمن", chat_types=['private'])
    def cmd_add_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            msg = _bot.reply_to(
                message,
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت")
            )
            _bot.register_next_step_handler(msg, process_add_enemy, account["id"])
        except Exception as e:
            logger.error(f"❌ خطا در cmd_add_enemy_prompt: {e}")

    def process_add_enemy(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=_lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                username = sender.username
                name = sender.first_name
                db.add_enemy(owner_id, user_id, username, name)
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دشمن اضافه شد!", reply_markup=_enemy_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                db.add_enemy(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دشمن اضافه شد!", reply_markup=_enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=_enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_add_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "❌ حذف دشمن", chat_types=['private'])
    def cmd_remove_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            msg = _bot.reply_to(
                message,
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت")
            )
            _bot.register_next_step_handler(msg, process_remove_enemy, account["id"])
        except Exception as e:
            logger.error(f"❌ خطا در cmd_remove_enemy_prompt: {e}")

    def process_remove_enemy(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=_lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                if db.remove_enemy(owner_id, user_id):
                    _bot.reply_to(message, "✅ کاربر از لیست دشمن حذف شد!", reply_markup=_enemy_keyboard())
                else:
                    _bot.reply_to(message, "❌ کاربر در لیست دشمن نبود!", reply_markup=_enemy_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                if db.remove_enemy(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دشمن حذف شد!", reply_markup=_enemy_keyboard())
                else:
                    _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دشمن نبود!", reply_markup=_enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=_enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_remove_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "📋 نمایش دشمن‌ها", chat_types=['private'])
    def cmd_show_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            enemies = db.get_enemies(account["id"])
            if not enemies:
                _bot.reply_to(message, "📋 لیست دشمن خالی است.", reply_markup=_enemy_keyboard())
                return
            lines = [f"🔴 <b>لیست دشمن ({len(enemies)} نفر):</b>\n"]
            for i, e in enumerate(enemies, 1):
                name = e.get('name') or e.get('username') or e.get('user_id')
                uid = e.get('user_id')
                lines.append(f"{i}. {name} — <code>{uid}</code>")
            _bot.reply_to(message, "\n".join(lines), reply_markup=_enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_show_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🗑️ پاک کردن دشمن‌ها", chat_types=['private'])
    def cmd_clear_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            db.clear_enemies(account["id"])
            _bot.reply_to(message, "🗑️ لیست دشمن پاک شد!", reply_markup=_enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_clear_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.text == "➕ افزودن دوست", chat_types=['private'])
    def cmd_add_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            msg = _bot.reply_to(
                message,
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت")
            )
            _bot.register_next_step_handler(msg, process_add_friend, account["id"])
        except Exception as e:
            logger.error(f"❌ خطا در cmd_add_friend_prompt: {e}")

    def process_add_friend(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=_lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                username = sender.username
                name = sender.first_name
                db.add_friend(owner_id, user_id, username, name)
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دوست اضافه شد!", reply_markup=_friend_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                db.add_friend(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دوست اضافه شد!", reply_markup=_friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=_friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_add_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_friend_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "❌ حذف دوست", chat_types=['private'])
    def cmd_remove_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            msg = _bot.reply_to(
                message,
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت")
            )
            _bot.register_next_step_handler(msg, process_remove_friend, account["id"])
        except Exception as e:
            logger.error(f"❌ خطا در cmd_remove_friend_prompt: {e}")

    def process_remove_friend(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=_lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                if db.remove_friend(owner_id, user_id):
                    _bot.reply_to(message, "✅ کاربر از لیست دوست حذف شد!", reply_markup=_friend_keyboard())
                else:
                    _bot.reply_to(message, "❌ کاربر در لیست دوست نبود!", reply_markup=_friend_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                if db.remove_friend(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دوست حذف شد!", reply_markup=_friend_keyboard())
                else:
                    _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دوست نبود!", reply_markup=_friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=_friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_remove_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_friend_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "📋 نمایش دوست‌ها", chat_types=['private'])
    def cmd_show_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            friends = db.get_friends(account["id"])
            if not friends:
                _bot.reply_to(message, "📋 لیست دوست خالی است.", reply_markup=_friend_keyboard())
                return
            lines = [f"💚 <b>لیست دوست ({len(friends)} نفر):</b>\n"]
            for i, f in enumerate(friends, 1):
                name = f.get('name') or f.get('username') or f.get('user_id')
                uid = f.get('user_id')
                lines.append(f"{i}. {name} — <code>{uid}</code>")
            _bot.reply_to(message, "\n".join(lines), reply_markup=_friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_show_friends: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🗑️ پاک کردن دوست‌ها", chat_types=['private'])
    def cmd_clear_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            db.clear_friends(account["id"])
            _bot.reply_to(message, "🗑️ لیست دوست پاک شد!", reply_markup=_friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_clear_friends: {e}")

    # ─── دکمه‌های اصلی ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "💰 موجودی", chat_types=['private'])
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            stats = db.get_token_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            _bot.reply_to(
                message,
                f"💎 <b>موجودی الماس</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=_owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_balance: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🎁 هدیه روزانه", chat_types=['private'])
    def cmd_daily(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            success, msg = db.claim_daily_token(account["id"])
            if success:
                stats = db.get_token_stats(account["id"])
                _bot.reply_to(
                    message,
                    f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>",
                    reply_markup=_user_keyboard()
                )
            else:
                _bot.reply_to(message, msg, reply_markup=_user_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_daily: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔗 رفرال", chat_types=['private'])
    def cmd_referral(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            link = f"https://t.me/{BOT_USERNAME}?start=ref_{account['id']}"
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            referral_value = config.REFERRAL_TOKENS * token_price
            
            _bot.reply_to(
                message,
                f"🔗 <b>لینک رفرال شما:</b>\n<code>{link}</code>\n\n"
                f"👥 تعداد: <b>{ref_count}</b>\n"
                f"🎁 پاداش: <b>{config.REFERRAL_TOKENS} الماس</b> (معادل {referral_value} تومان)",
                reply_markup=_user_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_referral: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🛒 خرید الماس", chat_types=['private'])
    def cmd_buy(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            username_txt = account["username"] if account else str(message.from_user.id)
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("📩 خرید از مالک (@Amele55)", url="https://t.me/Amele55"))
            for sp in getattr(config, 'SPONSORS', []):
                markup.add(types.InlineKeyboardButton(f"🤝 {sp['name']}: @{sp['username']}", url=f"https://t.me/{sp['username']}"))

            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.reply_to(
                message,
                f"🛒 <b>خرید الماس</b>\n\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>\n"
                f"👤 یوزرنیم پنل شما: <b>{username_txt}</b>\n\n"
                f"برای خرید، روی دکمه «خرید از مالک» کلیک کنید.",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_buy: {e}")

    @_bot.message_handler(func=lambda m: m.text == "👤 پروفایل من", chat_types=['private'])
    def cmd_profile(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            
            stats = db.get_token_stats(account["id"])
            text = f"👤 <b>پروفایل کاربری</b>\n\n"
            text += f"🆔 یوزرنیم: <b>{account['username']}</b>\n"
            text += f"💎 موجودی: <b>{stats['balance']}</b>\n"
            text += f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n"
            text += f"👥 رفرال: <b>{db.get_referral_count(account['id'])}</b>\n"
            
            _bot.reply_to(
                message,
                text,
                reply_markup=_user_keyboard() if message.from_user.id != OWNER_TG_ID else _owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_profile: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📊 وضعیت سلف", chat_types=['private'])
    def cmd_status(message):
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
            
            status_map = {
                "self_bot_active": "سلف‌بات",
                "secretary_active": "منشی",
                "anti_delete_active": "ضد حذف",
                "anti_link_active": "ضد لینک",
                "auto_seen_active": "سین خودکار",
                "auto_reaction_active": "ری‌اکشن",
                "private_lock_active": "قفل پیوی",
                "enemy_reply_active": "پاسخ دشمن",
                "auto_save_media": "ذخیره مدیا",
                "clock_name_active": "ساعت نام",
                "clock_bio_active": "ساعت بیو",
            }
            
            lines = [f"📊 <b>وضعیت سلف</b>\n"]
            for key, label in status_map.items():
                icon = "✅" if settings.get(key) == "1" else "❌"
                lines.append(f"{icon} {label}")
            lines.append(f"\n🔤 فونت: {settings.get('selected_font', '0')}")
            lines.append(f"👥 دشمن: {len(db.get_enemies(account['id']))} نفر")
            lines.append(f"💚 دوست: {len(db.get_friends(account['id']))} نفر")
            
            _bot.reply_to(
                message,
                "\n".join(lines),
                reply_markup=_user_keyboard() if message.from_user.id != OWNER_TG_ID else _owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_status: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📖 راهنما", chat_types=['private'])
    def cmd_help(message):
        try:
            if not require_membership(message): return
            help_text = """📖 <b>راهنمای Self Nexo</b>

🔹 <b>دکمه‌های اصلی:</b>
• 💰 موجودی — مشاهده موجودی الماس
• 🎁 هدیه روزانه — دریافت هدیه روزانه
• 🔗 رفرال — لینک دعوت دوستان
• 🛒 خرید الماس — خرید از مالک
• ⚙️ تنظیمات سلف — پنل مدیریت سلف
• 📊 وضعیت سلف — مشاهده وضعیت سلف
• 🔄 به‌روزرسانی منو — بروزرسانی منو

🔹 <b>پنل مدیریت سلف:</b>
• 🟢 سلف روشن — روشن کردن سلف
• 🔴 سلف خاموش — خاموش کردن سلف
• 🗑️ حذف سلف — خروج از حساب تلگرام
• 🤖 منشی — تنظیمات منشی خودکار
• 🛡️ امنیت — تنظیمات امنیتی
• ⚡ اتوماسیون — تنظیمات اتوماسیون
• 🔤 فونت — تغییر فونت
• 📋 لیست‌ها — مدیریت دشمن و دوست

💡 <b>نکات مهم:</b>
• هر ۲ الماس = ۲ ساعت سلف
• هدیه روزانه: ۱ الماس
• رفرال: ۱۲ الماس"""
            
            _bot.reply_to(
                message,
                help_text,
                reply_markup=_user_keyboard() if message.from_user.id != OWNER_TG_ID else _owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_help: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔄 به‌روزرسانی منو", chat_types=['private'])
    def cmd_refresh_menu(message):
        try:
            tg_id = message.from_user.id
            if not require_membership(message): return
            clear_user_cache(tg_id)
            account = get_user_account(tg_id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=types.ReplyKeyboardRemove())
            
            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔗 اتصال به تلگرام", callback_data="connect_telegram"))
                _bot.reply_to(
                    message,
                    f"🔗 حساب شما متصل نیست!\n\n👤 {account['username']}\n📝 لطفاً دوباره متصل شوید.",
                    reply_markup=markup
                )
                return
            
            stats = db.get_token_stats(account["id"])
            is_owner = (tg_id == OWNER_TG_ID)
            markup = _owner_keyboard() if is_owner else _user_keyboard()
            
            _bot.reply_to(
                message,
                f"🔄 <b>منو به‌روزرسانی شد</b> ✅\n\n"
                f"👋 {account['username']}\n"
                f"💎 موجودی: {stats['balance']}\n"
                f"📈 کل دریافتی: {stats['total_earned']}",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_refresh_menu: {e}")

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
