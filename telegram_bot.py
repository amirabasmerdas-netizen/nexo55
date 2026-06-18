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
    # 🆕 /start - با اصلاح کامل
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id
            logger.info(f"📩 دستور start از کاربر {tg_id} دریافت شد")
            
            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None
            
            # ─── پردازش رفرال ──────────────────────────────────────────────────
            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    threading.Thread(target=_process_referral_async, args=(referrer_id, tg_id), daemon=True).start()
                except:
                    pass

            # ─── بررسی عضویت در کانال‌های اجباری ──────────────────────────────
            if not require_membership(message):
                return

            # ─── دریافت حساب کاربری ────────────────────────────────────────────
            account = get_user_account(tg_id)
            
            # ─── اگر کاربر ثبت‌نام نکرده است ──────────────────────────────────
            if not account:
                logger.info(f"👤 کاربر {tg_id} ثبت‌نام نکرده است")
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("🤖 ثبت‌نام با ربات", callback_data="signup_bot"))
                markup.add(types.InlineKeyboardButton("🌐 ثبت‌نام با سایت", callback_data="signup_site"))
                
                _bot.reply_to(
                    message,
                    "👋 <b>سلام!</b>\n\n"
                    "برای استفاده از ربات، ابتدا ثبت‌نام کنید:\n\n"
                    "🤖 <b>ثبت‌نام با ربات:</b> سریع و آسان\n"
                    "🌐 <b>ثبت‌نام با سایت:</b> از طریق پنل وب",
                    reply_markup=markup
                )
                return

            # ─── اگر کاربر ثبت‌نام کرده اما telegram_user_id ثبت نشده است ────
            if not account.get("telegram_user_id"):
                logger.info(f"👤 کاربر {tg_id} ثبت‌نام کرده اما telegram_user_id ندارد")
                db.save_telegram_user_id(account["id"], tg_id)
                clear_user_cache(tg_id)
                account = get_user_account(tg_id)
                logger.info(f"✅ telegram_user_id برای کاربر {account['id']} ذخیره شد")

            # ─── بررسی اتصال به تلگرام ──────────────────────────────────────────
            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                logger.info(f"🔗 کاربر {tg_id} به تلگرام متصل نیست")
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

            # ─── کاربر وارد شده است ─────────────────────────────────────────────
            stats = db.get_token_stats(account["id"])
            
            if message.chat.type == 'private':
                markup = _owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
            else:
                markup = None

            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            _bot.reply_to(
                message,
                f"👋 خوش برگشتی <b>{account['username']}</b>!\n\n"
                f"💎 موجودی: <b>{stats['balance']}</b>\n"
                f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n\n"
                f"⚡ هر <b>{config.TOKENS_PER_SESSION} الماس</b> = <b>{config.SESSION_HOURS} ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=markup
            )

            # ─── نمایش اسپانسرها ──────────────────────────────────────────────
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
            try:
                _bot.reply_to(
                    message,
                    f"⚠️ خطا رخ داد: {str(e)}\n\nلطفاً دوباره /start بزنید."
                )
            except:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 ثبت‌نام با سایت
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "signup_site")
    def callback_signup_site(call):
        try:
            tg_id = call.from_user.id
            
            account = get_user_account(tg_id)
            if account:
                _bot.answer_callback_query(call.id, "❌ شما قبلاً ثبت‌نام کرده‌اید!", show_alert=True)
                return
            
            site_url = getattr(config, 'SITE_URL', '')
            if not site_url:
                _bot.answer_callback_query(call.id, "⚠️ لینک سایت تنظیم نشده است!", show_alert=True)
                return
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🌐 رفتن به سایت", url=site_url))
            markup.add(types.InlineKeyboardButton("🔄 بررسی ثبت‌نام", callback_data="check_registration"))
            
            _bot.answer_callback_query(call.id)
            _bot.send_message(
                call.message.chat.id,
                "🌐 <b>ثبت‌نام با سایت</b>\n\n"
                "لطفاً مراحل زیر را انجام دهید:\n\n"
                "1️⃣ روی دکمه زیر کلیک کنید\n"
                "2️⃣ در سایت ثبت‌نام کنید\n"
                "3️⃣ پس از ثبت‌نام، روی «بررسی ثبت‌نام» کلیک کنید\n\n"
                "📌 <b>نکته:</b> پس از ثبت‌نام در سایت، دیگر نیازی به ثبت‌نام در ربات نیست.",
                reply_markup=markup
            )
            
        except Exception as e:
            logger.error(f"❌ خطا در callback_signup_site: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    @_bot.callback_query_handler(func=lambda call: call.data == "check_registration")
    def callback_check_registration(call):
        try:
            tg_id = call.from_user.id
            
            account = get_user_account(tg_id)
            if account:
                _bot.answer_callback_query(call.id, "✅ ثبت‌نام شما تأیید شد!", show_alert=True)
                
                if not account.get("telegram_user_id"):
                    db.save_telegram_user_id(account["id"], tg_id)
                    clear_user_cache(tg_id)
                
                try:
                    _bot.delete_message(call.message.chat.id, call.message.message_id)
                except:
                    pass
                
                stats = db.get_token_stats(account["id"])
                markup = _owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                
                _bot.send_message(
                    call.message.chat.id,
                    f"👋 خوش برگشتی <b>{account['username']}</b>!\n\n"
                    f"💎 موجودی: <b>{stats['balance']}</b>\n"
                    f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n\n"
                    f"⚡ هر <b>{config.TOKENS_PER_SESSION} الماس</b> = <b>{config.SESSION_HOURS} ساعت</b> سلف‌بات",
                    reply_markup=markup
                )
            else:
                _bot.answer_callback_query(
                    call.id, 
                    "❌ هنوز ثبت‌نام نکرده‌اید!\nلطفاً ابتدا در سایت ثبت‌نام کنید.", 
                    show_alert=True
                )
        except Exception as e:
            logger.error(f"❌ خطا در callback_check_registration: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 ثبت‌نام با ربات
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "signup_bot")
    def callback_signup_bot(call):
        try:
            tg_id = call.from_user.id
            account = get_user_account(tg_id)
            if account:
                return _bot.answer_callback_query(call.id, "❌ شما قبلاً ثبت‌نام کرده‌اید!", show_alert=True)
            
            _signup_states[tg_id] = {"state": "username", "data": {}}
            
            _bot.answer_callback_query(call.id)
            msg = _bot.send_message(
                call.message.chat.id,
                "🤖 <b>ثبت‌نام با ربات</b>\n\n"
                "📝 مرحله ۱ از ۴:\n"
                "نام کاربری دلخواه را وارد کنید:\n\n"
                "💡 حداقل ۳ کاراکتر",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
            )
            _bot.register_next_step_handler(msg, process_signup_username)
        except Exception as e:
            logger.error(f"❌ خطا در callback_signup_bot: {e}")

    def process_signup_username(message):
        try:
            tg_id = message.from_user.id
            
            if message.text == "❌ لغو":
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            username = message.text.strip()
            
            if len(username) < 3:
                _bot.reply_to(message, "❌ نام کاربری باید حداقل ۳ کاراکتر باشد.\nدوباره تلاش کنید:")
                return
            
            existing = db.get_account_by_username(username)
            if existing:
                _bot.reply_to(message, "❌ این نام کاربری قبلاً ثبت شده.\nیک نام دیگر انتخاب کنید:")
                return
            
            _signup_states[tg_id]["data"]["username"] = username
            _signup_states[tg_id]["state"] = "password"
            
            _bot.reply_to(
                message,
                f"✅ نام کاربری: <b>{username}</b>\n\n"
                "📝 مرحله ۲ از ۴:\n"
                "رمز عبور را وارد کنید:\n\n"
                "💡 حداقل ۶ کاراکتر",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
            )
            _bot.register_next_step_handler(message, process_signup_password)
        except Exception as e:
            logger.error(f"❌ خطا در process_signup_username: {e}")

    def process_signup_password(message):
        try:
            tg_id = message.from_user.id
            
            if message.text == "❌ لغو":
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            password = message.text.strip()
            
            if len(password) < 6:
                _bot.reply_to(message, "❌ رمز عبور باید حداقل ۶ کاراکتر باشد.\nدوباره تلاش کنید:")
                return
            
            _signup_states[tg_id]["data"]["password"] = password
            _signup_states[tg_id]["state"] = "phone"
            
            _bot.reply_to(
                message,
                "✅ رمز عبور ذخیره شد.\n\n"
                "📝 مرحله ۳ از ۴:\n"
                "شماره تلفن خود را وارد کنید:\n\n"
                "💡 با کد کشور (مثال: +989123456789)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
            )
            _bot.register_next_step_handler(message, process_signup_phone)
        except Exception as e:
            logger.error(f"❌ خطا در process_signup_password: {e}")

    def process_signup_phone(message):
        try:
            tg_id = message.from_user.id
            
            if message.text == "❌ لغو":
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            phone = message.text.strip()
            
            if not phone.startswith("+"):
                _bot.reply_to(message, "❌ شماره باید با + شروع شود.\nمثال: +989123456789")
                return
            
            _signup_states[tg_id]["data"]["phone"] = phone
            _signup_states[tg_id]["state"] = "sending_code"
            
            _bot.reply_to(message, "⏳ در حال ارسال کد تایید...")
            
            def send_code_async():
                try:
                    async def _send():
                        cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
                        await cl.connect()
                        result = await cl.send_code_request(phone)
                        partial_sess = cl.session.save()
                        await cl.disconnect()
                        return result, partial_sess
                    
                    result, partial_sess = _run_telethon_async(_send())
                    
                    send_code_with_keyboard(
                        chat_id=message.chat.id,
                        tg_id=tg_id,
                        phone=phone,
                        partial_sess=partial_sess,
                        phone_hash=result.phone_code_hash,
                        username=_signup_states[tg_id]["data"]["username"],
                        password=_signup_states[tg_id]["data"]["password"]
                    )
                    
                except FloodWaitError as e:
                    _bot.send_message(message.chat.id, f"⏰ محدودیت: {e.seconds} ثانیه صبر کنید.")
                    _signup_states.pop(tg_id, None)
                except Exception as e:
                    _bot.send_message(message.chat.id, f"❌ خطا در ارسال کد: {str(e)}")
                    _signup_states.pop(tg_id, None)
            
            threading.Thread(target=send_code_async, daemon=True).start()
        except Exception as e:
            logger.error(f"❌ خطا در process_signup_phone: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 Callback: مدیریت کد تأیید (دکمه‌های عددی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("code_") or call.data in ["code_confirm", "code_cancel"])
    def callback_code_handler(call):
        try:
            tg_id = call.from_user.id
            
            if tg_id not in _temp_codes:
                _bot.answer_callback_query(call.id, "❌ جلسه منقضی شده. دوباره /start بزنید.", show_alert=True)
                return
            
            data = _temp_codes[tg_id]
            action = call.data
            
            if action.startswith("code_") and action not in ["code_confirm", "code_cancel", "code_display", "code_backspace", "code_clear"]:
                digit = action.split("_")[1]
                
                if len(data["code"]) >= 5:
                    _bot.answer_callback_query(call.id, "⚠️ کد ۵ رقمی است!", show_alert=True)
                    return
                
                data["code"] += digit
                _temp_codes[tg_id] = data
                
                markup = get_code_keyboard(data["code"])
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_backspace":
                if data["code"]:
                    data["code"] = data["code"][:-1]
                    _temp_codes[tg_id] = data
                    
                    markup = get_code_keyboard(data["code"])
                    _bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=markup
                    )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_clear":
                data["code"] = ""
                _temp_codes[tg_id] = data
                
                markup = get_code_keyboard("")
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_confirm":
                code = data["code"]
                
                if len(code) != 5:
                    _bot.answer_callback_query(call.id, f"⚠️ کد باید ۵ رقم باشد (در حال حاضر {len(code)} رقم)", show_alert=True)
                    return
                
                _bot.answer_callback_query(call.id, "⏳ در حال تأیید کد...")
                
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                
                def verify_code_async():
                    try:
                        async def _verify():
                            cl = TelegramClient(
                                StringSession(data["partial_sess"]),
                                config.API_ID,
                                config.API_HASH
                            )
                            await cl.connect()
                            await cl.sign_in(
                                phone=data["phone"],
                                code=code,
                                phone_code_hash=data["hash"]
                            )
                            me = await cl.get_me()
                            sess = cl.session.save()
                            await cl.disconnect()
                            return {"tg_id": me.id, "first_name": me.first_name, "session": sess}
                        
                        result = _run_telethon_async(_verify())
                        
                        new_id = db.create_account(data["username"], data["password"])
                        if not new_id:
                            _bot.send_message(call.message.chat.id, "❌ خطا در ایجاد حساب. لطفاً دوباره /start بزنید.")
                            return
                        
                        db.init_user_settings(new_id)
                        db.save_telegram_user_id(new_id, result["tg_id"])
                        db.save_session(new_id, result["session"], data["phone"])
                        db.set_setting(new_id, "logged_in", "1")
                        
                        _temp_codes.pop(tg_id, None)
                        _signup_states.pop(tg_id, None)
                        _telethon_clients.pop(tg_id, None)
                        _phone_hashes.pop(tg_id, None)
                        _phone_numbers.pop(tg_id, None)
                        
                        _bot.send_message(
                            call.message.chat.id,
                            f"✅ <b>ثبت‌نام با موفقیت انجام شد!</b>\n\n"
                            f"👤 نام کاربری: <b>{data['username']}</b>\n"
                            f"💎 موجودی اولیه: <b>{config.WELCOME_TOKENS} الماس</b>\n\n"
                            f"🎉 حالا می‌توانید از تمام قابلیت‌ها استفاده کنید!\n\n"
                            f"💡 برای مدیریت سلف، روی دکمه «⚙️ تنظیمات سلف» کلیک کنید.",
                            reply_markup=_user_keyboard()
                        )
                        
                    except SessionPasswordNeededError:
                        _bot.send_message(
                            call.message.chat.id,
                            "🔐 حساب شما رمز دومرحله‌ای دارد.\n\n"
                            "لطفاً رمز دومرحله‌ای را وارد کنید:",
                            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
                        )
                        _bot.register_next_step_handler(call.message, process_signup_2fa, tg_id)
                    
                    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                        _bot.send_message(
                            call.message.chat.id,
                            "❌ کد اشتباه یا منقضی شده.\n\n"
                            "🔁 لطفاً دوباره /start بزنید و تلاش کنید."
                        )
                        _temp_codes.pop(tg_id, None)
                    
                    except Exception as e:
                        _bot.send_message(
                            call.message.chat.id,
                            f"❌ خطا در تأیید کد:\n<code>{str(e)}</code>\n\nلطفاً دوباره /start بزنید."
                        )
                        _temp_codes.pop(tg_id, None)
                
                threading.Thread(target=verify_code_async, daemon=True).start()
            
            elif action == "code_cancel":
                _temp_codes.pop(tg_id, None)
                _signup_states.pop(tg_id, None)
                _telethon_clients.pop(tg_id, None)
                _phone_hashes.pop(tg_id, None)
                _phone_numbers.pop(tg_id, None)
                
                _bot.edit_message_text(
                    "❌ عملیات لغو شد.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                _bot.answer_callback_query(call.id)
                
                _bot.send_message(
                    call.message.chat.id,
                    "🔙 به منوی اصلی بازگشتید.",
                    reply_markup=types.ReplyKeyboardRemove()
                )
        
        except Exception as e:
            logger.error(f"❌ خطا در callback_code_handler: {e}")
            _bot.answer_callback_query(call.id, f"⚠️ خطا: {str(e)[:100]}", show_alert=True)

    def process_signup_2fa(message, tg_id):
        try:
            if message.text == "❌ لغو":
                _temp_codes.pop(tg_id, None)
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            password_2fa = message.text.strip()
            data = _temp_codes.get(tg_id)
            
            if not data:
                _bot.reply_to(message, "❌ اطلاعات ناقص است. دوباره /start بزنید.")
                return
            
            _bot.reply_to(message, "⏳ در حال تأیید رمز دومرحله‌ای...", reply_markup=types.ReplyKeyboardRemove())
            
            def verify_2fa_async():
                try:
                    async def _verify():
                        cl = TelegramClient(
                            StringSession(data["partial_sess"]),
                            config.API_ID,
                            config.API_HASH
                        )
                        await cl.connect()
                        await cl.sign_in(password=password_2fa)
                        me = await cl.get_me()
                        sess = cl.session.save()
                        await cl.disconnect()
                        return {"tg_id": me.id, "first_name": me.first_name, "session": sess}
                    
                    result = _run_telethon_async(_verify())
                    
                    new_id = db.create_account(data["username"], data["password"])
                    if not new_id:
                        _bot.send_message(message.chat.id, "❌ خطا در ایجاد حساب.")
                        return
                    
                    db.init_user_settings(new_id)
                    db.save_telegram_user_id(new_id, result["tg_id"])
                    db.save_session(new_id, result["session"], data["phone"])
                    db.set_setting(new_id, "logged_in", "1")
                    
                    _temp_codes.pop(tg_id, None)
                    _signup_states.pop(tg_id, None)
                    
                    _bot.send_message(
                        message.chat.id,
                        f"✅ <b>ثبت‌نام با موفقیت انجام شد!</b>\n\n"
                        f"👤 نام کاربری: <b>{data['username']}</b>\n"
                        f"💎 موجودی اولیه: <b>{config.WELCOME_TOKENS} الماس</b>\n\n"
                        f"🎉 حالا می‌توانید از تمام قابلیت‌ها استفاده کنید!",
                        reply_markup=_user_keyboard()
                    )
                
                except Exception as e:
                    _bot.send_message(message.chat.id, f"❌ خطا: {str(e)}")
                    _temp_codes.pop(tg_id, None)
            
            threading.Thread(target=verify_2fa_async, daemon=True).start()
        
        except Exception as e:
            logger.error(f"❌ خطا در process_signup_2fa: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 اتصال به تلگرام
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "connect_telegram")
    def callback_connect_telegram(call):
        try:
            tg_id = call.from_user.id
            account = get_user_account(tg_id)
            
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا ثبت‌نام کنید!", show_alert=True)
            
            _signup_states[tg_id] = {"state": "connect_phone", "data": {"account_id": account["id"]}}
            
            _bot.answer_callback_query(call.id)
            msg = _bot.send_message(
                call.message.chat.id,
                "🔗 <b>اتصال به تلگرام</b>\n\n"
                "شماره تلفن خود را وارد کنید:\n\n"
                "💡 با کد کشور (مثال: +989123456789)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
            )
            _bot.register_next_step_handler(msg, process_connect_phone)
        except Exception as e:
            logger.error(f"❌ خطا در callback_connect_telegram: {e}")

    def process_connect_phone(message):
        try:
            tg_id = message.from_user.id
            
            if message.text == "❌ لغو":
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            phone = message.text.strip()
            
            if not phone.startswith("+"):
                _bot.reply_to(message, "❌ شماره باید با + شروع شود.")
                return
            
            _signup_states[tg_id]["data"]["phone"] = phone
            
            _bot.reply_to(message, "⏳ در حال ارسال کد تایید...")
            
            def send_code_async():
                try:
                    async def _send():
                        cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
                        await cl.connect()
                        result = await cl.send_code_request(phone)
                        partial_sess = cl.session.save()
                        await cl.disconnect()
                        return result, partial_sess
                    
                    result, partial_sess = _run_telethon_async(_send())
                    
                    _temp_codes[tg_id] = {
                        "code": "",
                        "phone": phone,
                        "hash": result.phone_code_hash,
                        "partial_sess": partial_sess,
                        "account_id": _signup_states[tg_id]["data"]["account_id"],
                        "mode": "connect"
                    }
                    
                    markup = get_code_keyboard("")
                    _bot.send_message(
                        message.chat.id,
                        "📱 <b>ورود کد تأیید</b>\n\n"
                        "🔐 کد ۵ رقمی به تلگرام شما ارسال شد.\n"
                        "👇 با کلیک روی دکمه‌های زیر، کد را وارد کنید:\n\n"
                        "⚠️ کد هرگز به‌صورت پیام متنی نمایش داده نمی‌شود.\n"
                        "⏰ ۵ دقیقه فرصت دارید.",
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
                    
                except FloodWaitError as e:
                    _bot.send_message(message.chat.id, f"⏰ محدودیت: {e.seconds} ثانیه صبر کنید.")
                    _signup_states.pop(tg_id, None)
                except Exception as e:
                    _bot.send_message(message.chat.id, f"❌ خطا: {str(e)}")
                    _signup_states.pop(tg_id, None)
            
            threading.Thread(target=send_code_async, daemon=True).start()
        except Exception as e:
            logger.error(f"❌ خطا در process_connect_phone: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("code_") or call.data in ["code_confirm", "code_cancel"])
    def callback_connect_code_handler(call):
        try:
            tg_id = call.from_user.id
            
            if tg_id not in _temp_codes:
                _bot.answer_callback_query(call.id, "❌ جلسه منقضی شده. دوباره /start بزنید.", show_alert=True)
                return
            
            data = _temp_codes[tg_id]
            if data.get("mode") != "connect":
                return
            
            action = call.data
            
            if action.startswith("code_") and action not in ["code_confirm", "code_cancel", "code_display", "code_backspace", "code_clear"]:
                digit = action.split("_")[1]
                
                if len(data["code"]) >= 5:
                    _bot.answer_callback_query(call.id, "⚠️ کد ۵ رقمی است!", show_alert=True)
                    return
                
                data["code"] += digit
                _temp_codes[tg_id] = data
                
                markup = get_code_keyboard(data["code"])
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_backspace":
                if data["code"]:
                    data["code"] = data["code"][:-1]
                    _temp_codes[tg_id] = data
                    
                    markup = get_code_keyboard(data["code"])
                    _bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=markup
                    )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_clear":
                data["code"] = ""
                _temp_codes[tg_id] = data
                
                markup = get_code_keyboard("")
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
            
            elif action == "code_confirm":
                code = data["code"]
                
                if len(code) != 5:
                    _bot.answer_callback_query(call.id, f"⚠️ کد باید ۵ رقم باشد (در حال حاضر {len(code)} رقم)", show_alert=True)
                    return
                
                _bot.answer_callback_query(call.id, "⏳ در حال تأیید کد...")
                
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                
                def verify_code_async():
                    try:
                        async def _verify():
                            cl = TelegramClient(
                                StringSession(data["partial_sess"]),
                                config.API_ID,
                                config.API_HASH
                            )
                            await cl.connect()
                            await cl.sign_in(
                                phone=data["phone"],
                                code=code,
                                phone_code_hash=data["hash"]
                            )
                            me = await cl.get_me()
                            sess = cl.session.save()
                            await cl.disconnect()
                            return {"tg_id": me.id, "first_name": me.first_name, "session": sess}
                        
                        result = _run_telethon_async(_verify())
                        
                        account_id = data["account_id"]
                        if not account_id:
                            _bot.send_message(call.message.chat.id, "❌ شناسه حساب یافت نشد.")
                            return
                        
                        db.save_session(account_id, result["session"], data["phone"])
                        db.set_setting(account_id, "logged_in", "1")
                        db.save_telegram_user_id(account_id, result["tg_id"])
                        
                        _temp_codes.pop(tg_id, None)
                        _signup_states.pop(tg_id, None)
                        
                        _bot.send_message(
                            call.message.chat.id,
                            "✅ <b>اتصال با موفقیت انجام شد!</b>\n\n"
                            "🎉 حالا می‌توانید سلف‌بات را فعال کنید.",
                            reply_markup=_owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                        )
                    
                    except SessionPasswordNeededError:
                        _bot.send_message(
                            call.message.chat.id,
                            "🔐 رمز دومرحله‌ای را وارد کنید:",
                            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
                        )
                        _bot.register_next_step_handler(call.message, process_connect_2fa, tg_id)
                    
                    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                        _bot.send_message(
                            call.message.chat.id,
                            "❌ کد اشتباه یا منقضی شده.\n\n"
                            "🔁 لطفاً دوباره /start بزنید و تلاش کنید."
                        )
                        _temp_codes.pop(tg_id, None)
                    
                    except Exception as e:
                        _bot.send_message(
                            call.message.chat.id,
                            f"❌ خطا در تأیید کد:\n<code>{str(e)}</code>"
                        )
                        _temp_codes.pop(tg_id, None)
                
                threading.Thread(target=verify_code_async, daemon=True).start()
            
            elif action == "code_cancel":
                _temp_codes.pop(tg_id, None)
                _signup_states.pop(tg_id, None)
                
                _bot.edit_message_text(
                    "❌ عملیات لغو شد.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                _bot.answer_callback_query(call.id)
                
                _bot.send_message(
                    call.message.chat.id,
                    "🔙 به منوی اصلی بازگشتید.",
                    reply_markup=types.ReplyKeyboardRemove()
                )
        
        except Exception as e:
            logger.error(f"❌ خطا در callback_connect_code_handler: {e}")
            _bot.answer_callback_query(call.id, f"⚠️ خطا: {str(e)[:100]}", show_alert=True)

    def process_connect_2fa(message, tg_id):
        try:
            if message.text == "❌ لغو":
                _temp_codes.pop(tg_id, None)
                _signup_states.pop(tg_id, None)
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            password_2fa = message.text.strip()
            data = _temp_codes.get(tg_id)
            
            if not data or data.get("mode") != "connect":
                _bot.reply_to(message, "❌ اطلاعات ناقص است. دوباره /start بزنید.")
                return
            
            _bot.reply_to(message, "⏳ در حال تأیید...", reply_markup=types.ReplyKeyboardRemove())
            
            def verify_2fa_async():
                try:
                    async def _verify():
                        cl = TelegramClient(
                            StringSession(data["partial_sess"]),
                            config.API_ID,
                            config.API_HASH
                        )
                        await cl.connect()
                        await cl.sign_in(password=password_2fa)
                        me = await cl.get_me()
                        sess = cl.session.save()
                        await cl.disconnect()
                        return {"tg_id": me.id, "session": sess}
                    
                    result = _run_telethon_async(_verify())
                    
                    account_id = data["account_id"]
                    if not account_id:
                        _bot.send_message(message.chat.id, "❌ شناسه حساب یافت نشد.")
                        return
                    
                    db.save_session(account_id, result["session"], data["phone"])
                    db.set_setting(account_id, "logged_in", "1")
                    db.save_telegram_user_id(account_id, result["tg_id"])
                    
                    _temp_codes.pop(tg_id, None)
                    _signup_states.pop(tg_id, None)
                    
                    _bot.send_message(
                        message.chat.id,
                        "✅ <b>اتصال با موفقیت انجام شد!</b>",
                        reply_markup=_owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                    )
                
                except Exception as e:
                    _bot.send_message(message.chat.id, f"❌ خطا: {str(e)}")
                    _temp_codes.pop(tg_id, None)
            
            threading.Thread(target=verify_2fa_async, daemon=True).start()
        
        except Exception as e:
            logger.error(f"❌ خطا در process_connect_2fa: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📌 دکمه‌های اصلی
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
