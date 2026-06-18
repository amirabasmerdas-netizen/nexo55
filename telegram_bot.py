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
_temp_codes = {}  # {tg_id: {"code": "", "phone": "", "hash": "", "partial_sess": "", "account_id": None, "mode": "signup|connect"}}

# ─── State برای پنل مدیریت ──────────────────────────────────────────────────
_owner_states = {}
_lottery_players = {}


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
    
    # دکمه‌های اعداد
    buttons = []
    for i in range(1, 10):
        buttons.append(types.InlineKeyboardButton(str(i), callback_data=f"code_{i}"))
    markup.add(*buttons)
    
    # ردیف دوم: 0 و دکمه‌های پاک کردن
    markup.add(
        types.InlineKeyboardButton("0", callback_data="code_0"),
        types.InlineKeyboardButton("⌫", callback_data="code_backspace"),
        types.InlineKeyboardButton("🗑", callback_data="code_clear")
    )
    
    # ردیف سوم: نمایش کد و دکمه‌های تأیید/لغو
    display_code = current_code if current_code else "____"
    markup.add(
        types.InlineKeyboardButton(f"📱 {display_code}", callback_data="code_display")
    )
    markup.add(
        types.InlineKeyboardButton("✅ تأیید", callback_data="code_confirm"),
        types.InlineKeyboardButton("❌ لغو", callback_data="code_cancel")
    )
    
    return markup


# ─── مرحله ارسال کد با کیبورد عددی ───
def send_code_with_keyboard(chat_id, tg_id, phone, partial_sess, phone_hash, mode="signup", account_id=None):
    """ارسال کد با کیبورد عددی"""
    _temp_codes[tg_id] = {
        "code": "",
        "phone": phone,
        "hash": phone_hash,
        "partial_sess": partial_sess,
        "account_id": account_id,
        "mode": mode
    }
    
    markup = get_code_keyboard("")
    
    _bot.send_message(
        chat_id,
        "📱 <b>ورود کد تأیید</b>\n\n"
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
        markup.add("💎 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("🔌 فعال‌سازی سلف", "🔴 غیرفعال‌سازی سلف")
        markup.add("📖 راهنما", "👤 پروفایل من")
        return markup

    def _owner_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💎 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("🔌 فعال‌سازی سلف", "🔴 غیرفعال‌سازی سلف")
        markup.add("📢 مدیریت")
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
    # 🆕 /start - با دو دکمه ثبت‌نام
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

            # ✅ اگر کاربر ثبت‌نام نکرده، دو دکمه نمایش بده
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

            # ✅ بررسی اتصال تلگرام
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
    # 🆕 Callback: ثبت‌نام با ربات
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
                    
                    # ✅ ارسال کیبورد عددی به جای دکمه ساده
                    send_code_with_keyboard(
                        chat_id=message.chat.id,
                        tg_id=tg_id,
                        phone=phone,
                        partial_sess=partial_sess,
                        phone_hash=result.phone_code_hash,
                        mode="signup"
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
    # 🆕 Callback: ثبت‌نام با سایت (غیرفعال)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "signup_site_disabled")
    def callback_signup_site_disabled(call):
        _bot.answer_callback_query(call.id, "⚠️ این قابلیت در حال حاضر غیرفعال است.\nلطفاً از «ثبت‌نام با ربات» استفاده کنید.", show_alert=True)

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 Callback: اتصال به تلگرام
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
                    
                    # ✅ ارسال کیبورد عددی برای اتصال
                    send_code_with_keyboard(
                        chat_id=message.chat.id,
                        tg_id=tg_id,
                        phone=phone,
                        partial_sess=partial_sess,
                        phone_hash=result.phone_code_hash,
                        mode="connect",
                        account_id=_signup_states[tg_id]["data"]["account_id"]
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

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 Callback: مدیریت کد تأیید (دکمه‌های عددی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("code_") or call.data in ["code_confirm", "code_cancel"])
    def callback_code_handler(call):
        try:
            tg_id = call.from_user.id
            
            # اگر کاربر در لیست نباشد
            if tg_id not in _temp_codes:
                _bot.answer_callback_query(call.id, "❌ جلسه منقضی شده. دوباره تلاش کنید.", show_alert=True)
                return
            
            data = _temp_codes[tg_id]
            action = call.data
            
            # ─── دکمه‌های عددی ───
            if action.startswith("code_") and action != "code_confirm" and action != "code_cancel" and action != "code_display":
                digit = action.split("_")[1]
                
                # اگر کد ۵ رقمی شد، اجازه نده بیشتر اضافه بشه
                if len(data["code"]) >= 5:
                    _bot.answer_callback_query(call.id, "⚠️ کد ۵ رقمی است!", show_alert=True)
                    return
                
                # اضافه کردن رقم
                data["code"] += digit
                _temp_codes[tg_id] = data
                
                # به‌روزرسانی کیبورد
                markup = get_code_keyboard(data["code"])
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
            
            # ─── دکمه پاک کردن یک کاراکتر ───
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
            
            # ─── دکمه پاک کردن کل کد ───
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
            
            # ─── دکمه تأیید ───
            elif action == "code_confirm":
                code = data["code"]
                
                # بررسی طول کد
                if len(code) != 5:
                    _bot.answer_callback_query(call.id, f"⚠️ کد باید ۵ رقم باشد (در حال حاضر {len(code)} رقم)", show_alert=True)
                    return
                
                _bot.answer_callback_query(call.id, "⏳ در حال تأیید کد...")
                
                # حذف کیبورد
                _bot.edit_message_reply_markup(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=None
                )
                
                # تأیید کد در بک‌گراند
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
                        
                        # ─── ثبت‌نام جدید ───
                        if data["mode"] == "signup":
                            # اطلاعات ثبت‌نام از _signup_states
                            signup_data = _signup_states.get(tg_id, {})
                            if not signup_data:
                                _bot.send_message(call.message.chat.id, "❌ اطلاعات ثبت‌نام یافت نشد.")
                                return
                            
                            username = signup_data["data"].get("username")
                            password = signup_data["data"].get("password")
                            
                            if not username or not password:
                                _bot.send_message(call.message.chat.id, "❌ اطلاعات کاربری ناقص است.")
                                return
                            
                            # ایجاد حساب
                            new_id = db.create_account(username, password)
                            if not new_id:
                                _bot.send_message(call.message.chat.id, "❌ خطا در ایجاد حساب.")
                                return
                            
                            db.init_user_settings(new_id)
                            db.save_telegram_user_id(new_id, result["tg_id"])
                            db.save_session(new_id, result["session"], data["phone"])
                            db.set_setting(new_id, "logged_in", "1")
                            
                            # پاک کردن داده‌های موقت
                            _temp_codes.pop(tg_id, None)
                            _signup_states.pop(tg_id, None)
                            _telethon_clients.pop(tg_id, None)
                            _phone_hashes.pop(tg_id, None)
                            _phone_numbers.pop(tg_id, None)
                            
                            _bot.send_message(
                                call.message.chat.id,
                                f"✅ <b>ثبت‌نام با موفقیت انجام شد!</b>\n\n"
                                f"👤 نام کاربری: <b>{username}</b>\n"
                                f"💎 موجودی اولیه: <b>{config.WELCOME_TOKENS} الماس</b>\n\n"
                                f"🎉 حالا می‌توانید از تمام قابلیت‌ها استفاده کنید!\n\n"
                                f"💡 برای فعال‌سازی سلف، روی دکمه «🔌 فعال‌سازی سلف» کلیک کنید.",
                                reply_markup=_user_keyboard()
                            )
                        
                        # ─── اتصال حساب موجود ───
                        elif data["mode"] == "connect":
                            account_id = data["account_id"]
                            if not account_id:
                                _bot.send_message(call.message.chat.id, "❌ شناسه حساب یافت نشد.")
                                return
                            
                            db.save_session(account_id, result["session"], data["phone"])
                            db.set_setting(account_id, "logged_in", "1")
                            db.save_telegram_user_id(account_id, result["tg_id"])
                            
                            # پاک کردن داده‌های موقت
                            _temp_codes.pop(tg_id, None)
                            _signup_states.pop(tg_id, None)
                            _telethon_clients.pop(tg_id, None)
                            _phone_hashes.pop(tg_id, None)
                            _phone_numbers.pop(tg_id, None)
                            
                            _bot.send_message(
                                call.message.chat.id,
                                "✅ <b>اتصال با موفقیت انجام شد!</b>\n\n"
                                "🎉 حالا می‌توانید سلف‌بات را فعال کنید.",
                                reply_markup=_owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                            )
                    
                    except SessionPasswordNeededError:
                        # رمز دو مرحله‌ای
                        _bot.send_message(
                            call.message.chat.id,
                            "🔐 حساب شما رمز دومرحله‌ای دارد.\n\n"
                            "📝 مرحله ۴ از ۴:\n"
                            "رمز دومرحله‌ای را وارد کنید:",
                            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو")
                        )
                        _bot.register_next_step_handler(call.message, process_2fa_password, tg_id)
                    
                    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                        _bot.send_message(
                            call.message.chat.id,
                            "❌ کد اشتباه یا منقضی شده.\n\n"
                            "🔁 لطفاً دوباره تلاش کنید و /start بزنید."
                        )
                        _temp_codes.pop(tg_id, None)
                    
                    except Exception as e:
                        _bot.send_message(
                            call.message.chat.id,
                            f"❌ خطا در تأیید کد:\n<code>{str(e)}</code>"
                        )
                        _temp_codes.pop(tg_id, None)
                
                threading.Thread(target=verify_code_async, daemon=True).start()
            
            # ─── دکمه لغو ───
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
                
                # بازگشت به منو
                _bot.send_message(
                    call.message.chat.id,
                    "🔙 به منوی اصلی بازگشتید.",
                    reply_markup=_owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                )
        
        except Exception as e:
            logger.error(f"❌ خطا در callback_code_handler: {e}")
            _bot.answer_callback_query(call.id, f"⚠️ خطا: {str(e)}", show_alert=True)

    # ─── تابع پردازش رمز دو مرحله‌ای ───
    def process_2fa_password(message, tg_id):
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
                    
                    if data["mode"] == "signup":
                        # ثبت‌نام با رمز دو مرحله‌ای
                        signup_data = _signup_states.get(tg_id, {})
                        if not signup_data:
                            _bot.send_message(message.chat.id, "❌ اطلاعات ثبت‌نام یافت نشد.")
                            return
                        
                        username = signup_data["data"].get("username")
                        password = signup_data["data"].get("password")
                        
                        if not username or not password:
                            _bot.send_message(message.chat.id, "❌ اطلاعات کاربری ناقص است.")
                            return
                        
                        new_id = db.create_account(username, password)
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
                            f"👤 نام کاربری: <b>{username}</b>\n"
                            f"💎 موجودی اولیه: <b>{config.WELCOME_TOKENS} الماس</b>\n\n"
                            f"🎉 حالا می‌توانید از تمام قابلیت‌ها استفاده کنید!",
                            reply_markup=_user_keyboard()
                        )
                    
                    elif data["mode"] == "connect":
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
                            "✅ <b>اتصال با موفقیت انجام شد!</b>\n\n"
                            "🎉 حالا می‌توانید سلف‌بات را فعال کنید.",
                            reply_markup=_owner_keyboard() if tg_id == OWNER_TG_ID else _user_keyboard()
                        )
                
                except Exception as e:
                    _bot.send_message(message.chat.id, f"❌ خطا: {str(e)}")
                    _temp_codes.pop(tg_id, None)
            
            threading.Thread(target=verify_2fa_async, daemon=True).start()
        
        except Exception as e:
            logger.error(f"❌ خطا در process_2fa_password: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 فعال‌سازی سلف از ربات
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "🔌 فعال‌سازی سلف", chat_types=['private'])
    def cmd_activate_self(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            
            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                return _bot.reply_to(message, "⚠️ ابتدا حساب تلگرام را متصل کنید.")
            
            session_data = db.get_session(account["id"])
            if not session_data:
                return _bot.reply_to(message, "⚠️ session یافت نشد. دوباره متصل شوید.")
            
            from bot import bot_manager
            loop = _get_telethon_loop()
            ok = bot_manager.start(account["id"], loop, check_tokens=True)
            
            if ok:
                db.set_setting(account["id"], "self_bot_active", "1")
                
                is_owner = (message.from_user.id == OWNER_TG_ID)
                if is_owner:
                    msg = "✅ سلف روشن شد — دسترسی رایگان مالک ♾️"
                else:
                    msg = f"✅ سلف روشن شد — {config.TOKENS_PER_SESSION} الماس کسر شد — {config.SESSION_HOURS} ساعت فعال است"
                
                _bot.reply_to(message, msg, reply_markup=_owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard())
            else:
                balance = db.get_token_balance(account["id"])
                _bot.reply_to(
                    message,
                    f"❌ الماس کافی ندارید!\n💎 موجودی: {balance}\n⚡ نیاز: {config.TOKENS_PER_SESSION} الماس",
                    reply_markup=_user_keyboard()
                )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_activate_self: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    @_bot.message_handler(func=lambda m: m.text == "🔴 غیرفعال‌سازی سلف", chat_types=['private'])
    def cmd_deactivate_self(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            
            from bot import bot_manager
            bot_manager.stop(account["id"])
            db.set_setting(account["id"], "self_bot_active", "0")
            
            _bot.reply_to(message, "❌ سلف خاموش شد.", reply_markup=_owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_deactivate_self: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 دستورات مالک: چنل اجباری و مشخصات کاربران
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["channels", "چنل_اجباری"])
    def cmd_channels(message):
        try:
            if message.from_user.id != OWNER_TG_ID:
                return _bot.reply_to(message, "⛔ فقط مالک")
            
            channels = db.get_forced_channels()
            
            if not channels:
                text = "📋 لیست چنل‌های اجباری خالی است.\n\n"
            else:
                text = "📢 <b>چنل‌های اجباری:</b>\n\n"
                for i, ch in enumerate(channels, 1):
                    text += f"{i}. <code>{ch}</code>\n"
            
            text += "\n💡 برای افزودن: <code>/addchannel @username</code>\n"
            text += "💡 برای حذف: <code>/removechannel @username</code>"
            
            _bot.reply_to(message, text)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_channels: {e}")

    @_bot.message_handler(commands=["addchannel"])
    def cmd_addchannel(message):
        try:
            if message.from_user.id != OWNER_TG_ID:
                return _bot.reply_to(message, "⛔ فقط مالک")
            
            parts = message.text.split()
            if len(parts) < 2:
                return _bot.reply_to(message, "❌ فرمت: /addchannel @username")
            
            username = parts[1]
            if not username.startswith("@"):
                username = "@" + username
            
            if db.add_forced_channel(username):
                _bot.reply_to(message, f"✅ چنل {username} اضافه شد.")
            else:
                _bot.reply_to(message, "❌ خطا یا تکراری است.")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_addchannel: {e}")

    @_bot.message_handler(commands=["removechannel"])
    def cmd_removechannel(message):
        try:
            if message.from_user.id != OWNER_TG_ID:
                return _bot.reply_to(message, "⛔ فقط مالک")
            
            parts = message.text.split()
            if len(parts) < 2:
                return _bot.reply_to(message, "❌ فرمت: /removechannel @username")
            
            username = parts[1]
            if not username.startswith("@"):
                username = "@" + username
            
            if db.remove_forced_channel(username):
                _bot.reply_to(message, f"✅ چنل {username} حذف شد.")
            else:
                _bot.reply_to(message, "❌ چنل یافت نشد.")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_removechannel: {e}")

    @_bot.message_handler(commands=["users", "مشخصات_کاربرا"])
    def cmd_users(message):
        try:
            if message.from_user.id != OWNER_TG_ID:
                return _bot.reply_to(message, "⛔ فقط مالک")
            
            accounts = db.get_all_accounts()
            
            if not accounts:
                return _bot.reply_to(message, "📋 هیچ کاربری ثبت‌نام نکرده.")
            
            text = f"👥 <b>مشخصات کاربران ({len(accounts)} نفر):</b>\n\n"
            
            for i, acc in enumerate(accounts[:50], 1):
                balance = db.get_token_balance(acc["id"])
                logged_in = db.get_setting(acc["id"], "logged_in") == "1"
                self_active = db.get_setting(acc["id"], "self_bot_active") == "1"
                
                status = "🟢" if logged_in else "🔴"
                self_status = "⚡" if self_active else ""
                
                text += f"{i}. {status}{self_status} <b>{acc['username']}</b>\n"
                text += f"   💎 {balance} الماس\n"
                
                if acc.get("telegram_user_id"):
                    text += f"   🆔 <code>{acc['telegram_user_id']}</code>\n"
                
                text += "\n"
            
            if len(accounts) > 50:
                text += f"\n... و {len(accounts) - 50} کاربر دیگر"
            
            _bot.reply_to(message, text)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_users: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Callback: بررسی عضویت
    # ══════════════════════════════════════════════════════════════════════════
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
                _bot.answer_callback_query(call.id, f"هنوز در {len(missing)} کانال عضو نشده‌اید! ❌", show_alert=True)
        except Exception as e:
            logger.error(f"❌ خطا در callback_check_join: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه‌های منوی اصلی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "💎 موجودی", chat_types=['private'])
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            stats = db.get_token_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            _bot.reply_to(message,
                f"💎 <b>موجودی الماس</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=_owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard())
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
                _bot.reply_to(message, f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>", reply_markup=_user_keyboard())
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
            
            _bot.reply_to(message,
                f"🔗 <b>لینک رفرال شما:</b>\n<code>{link}</code>\n\n"
                f"👥 تعداد: <b>{ref_count}</b>\n"
                f"🎁 پاداش: <b>{config.REFERRAL_TOKENS} الماس</b> (معادل {referral_value} تومان)",
                reply_markup=_user_keyboard())
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
            _bot.reply_to(message,
                f"🛒 <b>خرید الماس</b>\n\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>\n"
                f"👤 یوزرنیم پنل شما: <b>{username_txt}</b>\n\n"
                f"برای خرید، روی دکمه «خرید از مالک» کلیک کنید.",
                reply_markup=markup)
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
            
            _bot.reply_to(message, text, 
                         reply_markup=_user_keyboard() if message.from_user.id != OWNER_TG_ID else _owner_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_profile: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📖 راهنما", chat_types=['private'])
    def cmd_help(message):
        try:
            if not require_membership(message): return
            help_text = """📖 <b>راهنمای Self Nexo</b>

🔹 <b>دکمه‌های اصلی:</b>
• 💎 موجودی — مشاهده موجودی الماس
• 🎁 هدیه روزانه — دریافت هدیه روزانه
• 🔗 رفرال — لینک دعوت دوستان
• 🛒 خرید الماس — خرید از مالک
• 🔌 فعال‌سازی سلف — روشن کردن سلف
• 🔴 غیرفعال‌سازی سلف — خاموش کردن سلف

💡 <b>نکات مهم:</b>
• هر ۲ الماس = ۲ ساعت سلف
• هدیه روزانه: ۱ الماس
• رفرال: ۱۲ الماس"""
            
            _bot.reply_to(message, help_text,
                         reply_markup=_user_keyboard() if message.from_user.id != OWNER_TG_ID else _owner_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_help: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📢 پنل مدیریت مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "📢 مدیریت", chat_types=['private'])
    def cmd_admin_panel(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        _bot.reply_to(message, 
            "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=_admin_panel_keyboard())

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("admin_") or call.data.startswith("rmch_") or call.data.startswith("wcwin_") or call.data.startswith("wc_") or call.data == "addch_prompt")
    def callback_admin(call):
        if call.from_user.id != OWNER_TG_ID:
            return _bot.answer_callback_query(call.id, "❌ فقط مالک دسترسی دارد", show_alert=True)
        
        try:
            data = call.data
            
            if data == "admin_panel" or data == "admin_back":
                _bot.edit_message_text(
                    "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=_admin_panel_keyboard()
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_channels":
                channels = db.get_forced_channels()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if channels:
                    text = "📢 <b>چنل‌های اجباری فعلی:</b>\n\n"
                    for ch in channels:
                        text += f"🔸 <code>{ch}</code>\n"
                        ch_clean = ch.lstrip("@")
                        markup.add(types.InlineKeyboardButton(f"❌ حذف {ch}", callback_data=f"rmch_{ch_clean}"))
                else:
                    text = "📋 لیست چنل‌ها خالی است.\n\n"
                text += "\nبرای افزودن چنل جدید از دکمه زیر استفاده کنید:"
                markup.add(types.InlineKeyboardButton("➕ افزودن چنل جدید", callback_data="addch_prompt"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data.startswith("rmch_"):
                ch = data[5:]
                if not ch.startswith("@"):
                    ch = "@" + ch
                if db.remove_forced_channel(ch):
                    _bot.answer_callback_query(call.id, f"✅ چنل {ch} حذف شد")
                    call.data = "admin_channels"
                    callback_admin(call)
                else:
                    _bot.answer_callback_query(call.id, "❌ خطا در حذف")
                return
            
            elif data == "addch_prompt":
                _owner_states[call.from_user.id] = {"state": "waiting_channel"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(
                    "📝 آیدی چنل را ارسال کنید (با @ شروع شود):\n\nمثال: <code>@mychannel</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=markup
                )
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_users":
                accounts = db.get_all_accounts()
                if not accounts:
                    text = "هیچ کاربری ثبت نشده."
                else:
                    lines = [f"👥 <b>کاربران ({len(accounts)} نفر):</b>\n\n"]
                    for acc in accounts[:30]:
                        bal = db.get_token_balance(acc["id"])
                        lines.append(f"• <b>{acc['username']}</b> — 💎{bal}")
                    text = "\n".join(lines)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_wc":
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("➕ ایجاد چالش جدید", callback_data="wc_new"))
                markup.add(types.InlineKeyboardButton("📋 چالش‌های فعال", callback_data="wc_list"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text("🏆 <b>مدیریت چالش‌های جام جهانی</b>\n\nیک گزینه را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "wc_list":
                challenges = db.get_active_challenges()
                if not challenges:
                    text = "📋 هیچ چالش فعالی وجود ندارد."
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc"))
                else:
                    text = "🏆 <b>چالش‌های فعال:</b>\n\n"
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    for c in challenges:
                        text += f"<b>ID {c['id']}:</b> {c['team1']} vs {c['team2']}\n"
                        text += f"⏰ {c['match_time']} | 💎 {c['bet_amount']}\n\n"
                        markup.add(
                            types.InlineKeyboardButton(f"✅ {c['team1']}", callback_data=f"wcwin_{c['id']}_{c['team1']}"),
                            types.InlineKeyboardButton(f"✅ {c['team2']}", callback_data=f"wcwin_{c['id']}_{c['team2']}")
                        )
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data.startswith("wcwin_"):
                parts = data.split("_", 2)
                challenge_id = int(parts[1])
                winner_team = parts[2]
                db.set_challenge_winner(challenge_id, winner_team)
                success, results = db.settle_challenge_bets(challenge_id)
                if success:
                    won_count = sum(1 for r in results if r["result"] == "won")
                    lost_count = sum(1 for r in results if r["result"] == "lost")
                    _bot.answer_callback_query(call.id, f"✅ برنده: {winner_team}\n🏆 {won_count} برنده | ❌ {lost_count} بازنده", show_alert=True)
                else:
                    _bot.answer_callback_query(call.id, f"❌ خطا: {results}", show_alert=True)
                return
            
            elif data == "admin_lottery":
                _owner_states[call.from_user.id] = {"state": "lottery_amount"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(
                    "🎲 <b>ایجاد قرعه‌کشی گروهی (مالک)</b>\n\n💎 مبلغ جایزه را ارسال کنید (الماس):\n\nمثال: <code>100</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_transfer":
                _owner_states[call.from_user.id] = {"state": "transfer_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(
                    "💎 <b>انتقال الماس (از طرف سیستم)</b>\n\n📝 یوزرنیم کاربر مقصد را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            elif data == "admin_give":
                _owner_states[call.from_user.id] = {"state": "give_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(
                    "💰 <b>دادن الماس به کاربر</b>\n\n📝 یوزرنیم کاربر را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            
            else:
                _bot.answer_callback_query(call.id, "❌ گزینه نامعتبر")
        
        except Exception as e:
            logger.error(f"❌ خطا در callback_admin: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📨 State handler مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.from_user.id == OWNER_TG_ID and m.from_user.id in _owner_states, chat_types=['private'])
    def handle_owner_state(message):
        try:
            state_data = _owner_states[message.from_user.id]
            state = state_data["state"]
            text = message.text.strip()
            
            if state == "waiting_channel":
                if not text.startswith("@"):
                    text = "@" + text
                if db.add_forced_channel(text):
                    _bot.reply_to(message, f"✅ چنل <b>{text}</b> اضافه شد.", reply_markup=_owner_keyboard())
                else:
                    _bot.reply_to(message, f"⚠️ خطا یا تکراری است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "lottery_amount":
                try:
                    prize = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                group = getattr(config, 'WORLD_CUP_GROUP', '@amelselfgap')
                lottery_id = db.create_lottery(0, OWNER_TG_ID, prize, 2, prize)
                
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(f"🎲 شرکت در قرعه‌کشی ({prize} الماس)", callback_data=f"join_lottery_{lottery_id}"))
                _lottery_players[lottery_id] = []
                
                try:
                    msg = _bot.send_message(group,
                        f"🎉 <b>قرعه‌کشی ویژه (مالک)!</b>\n\n💎 مبلغ ورودی: <b>{prize} الماس</b>\n💰 مجموع جایزه: <b>{prize * 2} الماس</b>\n\nبا ورود نفر دوم، قرعه‌کشی انجام می‌شود!",
                        reply_markup=markup)
                    db.update_lottery_message(lottery_id, msg.message_id)
                    _bot.reply_to(message, f"✅ قرعه‌کشی در گروه {group} ایجاد شد!\n💎 جایزه: {prize} الماس", reply_markup=_owner_keyboard())
                    threading.Timer(120, _auto_finish_lottery, args=[lottery_id, group]).start()
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا: {e}", reply_markup=_owner_keyboard())
                
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "transfer_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "transfer_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            
            elif state == "transfer_amount":
                try:
                    amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                username = state_data["data"]["username"]
                to_account = db.get_account_by_username(username)
                if not to_account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                
                db.add_tokens(to_account["id"], amount)
                new_balance = db.get_token_balance(to_account["id"])
                _bot.reply_to(message, f"✅ <b>{amount} الماس</b> به <b>{to_account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b>", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "give_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "give_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            
            elif state == "give_amount":
                try:
                    amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                username = state_data["data"]["username"]
                account = db.get_account_by_username(username)
                if not account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                
                db.add_tokens(account["id"], amount)
                new_balance = db.get_token_balance(account["id"])
                _bot.reply_to(message, f"✅ <b>{amount}</b> الماس به <b>{account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b>", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
        
        except Exception as e:
            logger.error(f"❌ خطا در handle_owner_state: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}", reply_markup=_owner_keyboard())
            _owner_states.pop(message.from_user.id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # 🎲 قرعه‌کشی در گروه و پیوی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("قرعه "), chat_types=['private', 'group', 'supergroup'])
    def cmd_lottery(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            parts = message.text.split()
            if len(parts) < 2:
                return _bot.reply_to(message, "❗ فرمت: قرعه [تعداد الماس]\nمثال: قرعه 100")
            
            try:
                prize = int(parts[1])
                if prize < 1:
                    return _bot.reply_to(message, "❌ مبلغ باید بیشتر از 0 باشد.")
            except:
                return _bot.reply_to(message, "❌ مبلغ باید عدد باشد.")
            
            balance = db.get_token_balance(account["id"])
            if balance < prize:
                return _bot.reply_to(message, f"❌ موجودی کافی ندارید! نیاز به {prize} الماس دارید.\nموجودی فعلی: {balance} الماس")
            
            if not db.deduct_tokens(account["id"], prize):
                return _bot.reply_to(message, "❌ خطا در کسر الماس!")
            
            lottery_id = db.create_lottery(
                chat_id=message.chat.id,
                creator_tg_id=message.from_user.id,
                prize_amount=prize,
                duration_minutes=2,
                entry_fee=prize
            )
            
            _lottery_players[lottery_id] = [message.from_user.id]
            
            markup = types.InlineKeyboardMarkup(row_width=1)
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
            threading.Timer(120, _auto_finish_lottery, args=[lottery_id, message.chat.id]).start()
            
        except Exception as e:
            logger.error(f"❌ خطا در cmd_lottery: {e}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("join_lottery_"))
    def callback_join_lottery(call):
        try:
            lottery_id = int(call.data.split("_")[2])
            lottery = db.get_lottery(lottery_id)
            
            if not lottery or lottery["status"] != "active":
                return _bot.answer_callback_query(call.id, "❌ این قرعه‌کشی فعال نیست یا به پایان رسیده.", show_alert=True)
            
            if lottery_id in _lottery_players and call.from_user.id in _lottery_players[lottery_id]:
                return _bot.answer_callback_query(call.id, "❌ شما قبلاً در این قرعه‌کشی ثبت‌نام کرده‌اید.", show_alert=True)
            
            if lottery["creator_tg_id"] == call.from_user.id:
                return _bot.answer_callback_query(call.id, "❌ شما سازنده قرعه‌کشی هستید! منتظر نفر دوم باشید.", show_alert=True)
            
            account = get_user_account(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            
            entry_fee = lottery["prize_amount"]
            balance = db.get_token_balance(account["id"])
            
            if balance < entry_fee:
                return _bot.answer_callback_query(call.id, f"❌ موجودی کافی ندارید! نیاز به {entry_fee} الماس دارید.", show_alert=True)
            
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
                            f"🎉 <b>قرعه‌کشی!</b>\n\n💎 مبلغ ورودی: <b>{entry_fee} الماس</b>\n💰 مجموع جایزه: <b>{entry_fee * 2} الماس</b>\n👥 شرکت‌کنندگان: {len(_lottery_players[lottery_id])} نفر\n\n⏳ منتظر نفر دوم...",
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id
                        )
                    except:
                        pass
            else:
                _bot.answer_callback_query(call.id, msg, show_alert=True)
                
        except Exception as e:
            logger.error(f"❌ خطا در callback_join_lottery: {e}")

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
            logger.error(f"❌ خطا در _finish_lottery_immediately: {e}")

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
                    _bot.send_message(chat_id,
                        f"⏰ قرعه‌کشی لغو شد!\n\n❌ تعداد شرکت‌کنندگان کافی نبود.\n💎 {lottery['prize_amount']} الماس به سازنده برگشت داده شد.")
            
            _lottery_players.pop(lottery_id, None)
            
        except Exception as e:
            logger.error(f"❌ خطا در _auto_finish_lottery: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 💰 موجودی در گروه
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text and m.text == "موجودی", chat_types=['group', 'supergroup'])
    def cmd_balance_group(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            stats = db.get_token_stats(account["id"])
            _bot.reply_to(message, f"💎 <b>موجودی شما:</b>\n💰 الماس: <b>{stats['balance']}</b>")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_balance_group: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 💎 انتقال الماس
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("انتقال "), chat_types=['private', 'group', 'supergroup'])
    def cmd_transfer(message):
        try:
            parts = message.text.split()
            if len(parts) < 3:
                return _bot.reply_to(message, "❗ فرمت: انتقال [یوزرنیم] [تعداد]\nمثال: انتقال @ali 10")
            
            username = parts[1].lstrip("@")
            try:
                amount = int(parts[2])
                if amount < 1:
                    return _bot.reply_to(message, "❌ مقدار باید بیشتر از 0 باشد.")
            except:
                return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
            
            from_account = get_user_account(message.from_user.id)
            if not from_account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            to_account = db.get_account_by_username(username)
            if not to_account:
                return _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.")
            
            if to_account["id"] == from_account["id"]:
                return _bot.reply_to(message, "❌ نمی‌توانید به خودتان الماس انتقال دهید.")
            
            success, msg = db.transfer_diamonds(from_account["id"], to_account["id"], amount)
            
            if success:
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try:
                        _bot.send_message(to_tg_id, f"💎 <b>{amount} الماس</b> از @{message.from_user.username or 'کاربر'} دریافت کردید!")
                    except:
                        pass
            
            _bot.reply_to(message, msg)
            
        except Exception as e:
            logger.error(f"❌ خطا در cmd_transfer: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # پیام‌های ناشناخته
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: True, chat_types=['private'])
    def cmd_unknown(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.\n/start را بزنید.")
            
            kb = _owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard()
            _bot.reply_to(message, "⚠️ دستور نامعتبر. از دکمه‌های زیر استفاده کنید:", reply_markup=kb)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_unknown: {e}")

    def _process_referral_async(referrer_id, tg_id):
        try:
            if db.process_referral(referrer_id, tg_id):
                referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                if referrer_tg and _bot:
                    _bot.send_message(referrer_tg, 
                        f"🎉 یک نفر با لینک شما عضو شد!\n<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
        except Exception as e:
            logger.error(f"❌ خطا در رفرال: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Polling
    # ══════════════════════════════════════════════════════════════════════════
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
