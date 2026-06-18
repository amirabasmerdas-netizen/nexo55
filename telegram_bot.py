import threading, time, telebot, asyncio, datetime, random, logging, re
from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError
import database as db
import db_cache as cache
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = config.OWNER_TG_ID

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

_signup_states = {}
_telethon_loop = None
_telethon_clients = {}
_phone_hashes = {}
_phone_numbers = {}
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

def get_bot(): return _bot

def start_token_bot():
    global _bot, BOT_USERNAME
    try:
        test = db.get_all_accounts()
        logger.info(f"✅ اتصال به دیتابیس دائمی (Supabase) برقرار است! تعداد کاربران: {len(test)}")
    except Exception as e:
        logger.error(f"❌ خطا در اتصال به دیتابیس دائمی: {e}")
        return
    try:
        channels = cache.get_forced_channels()
        logger.info(f"✅ اتصال به دیتابیس موقت (SQLite) برقرار است! تعداد چنل‌ها: {len(channels)}")
    except Exception as e:
        logger.error(f"❌ خطا در اتصال به دیتابیس موقت: {e}")
    if not config.BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN تنظیم نشده — ربات مدیریت غیرفعال است")
        return
    _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4)
    try:
        me = _bot.get_me()
        BOT_USERNAME = me.username
        logger.info(f"🤖 ربات مدیریت: @{BOT_USERNAME}")
    except Exception as e:
        logger.error(f"❌ خطا در اتصال ربات مدیریت: {e}")
        _bot = None
        return
    for _ in range(3):
        try:
            _bot.delete_webhook(drop_pending_updates=True)
            time.sleep(2)
            break
        except: time.sleep(2)

    def get_user_stats(owner_id: int): return db.get_token_stats(owner_id)
    def get_user_settings(owner_id: int):
        keys = ["self_bot_active", "secretary_active", "anti_delete_active", "anti_link_active",
            "auto_seen_active", "auto_reaction_active", "private_lock_active", "enemy_reply_active",
            "auto_save_media", "clock_name_active", "clock_bio_active", "selected_font"]
        return {k: db.get_setting(owner_id, k, "0") for k in keys}

    def require_membership(message):
        tg_id = message.from_user.id
        is_member, missing = cache.check_user_membership(_bot, tg_id)
        if not is_member:
            send_forced_channels_menu(message, missing)
            return False
        return True

    def send_forced_channels_menu(message, missing_channels):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for ch in missing_channels:
            ch_clean = ch.lstrip("@")
            markup.add(types.InlineKeyboardButton(f"📢 عضویت در {ch}", url=f"https://t.me/{ch_clean}"))
        markup.add(types.InlineKeyboardButton("✅ بررسی عضویت من", callback_data="check_join"))
        channels_list = "\n".join([f"🔸 {ch}" for ch in missing_channels])
        _bot.reply_to(message,
            "⛔️ <b>ورود به ربات منوط به عضویت در کانال‌های زیر است:</b>\n\n"
            f"{channels_list}\n\n👇 روی هر کانال کلیک کنید و Join بزنید، سپس دکمه «بررسی عضویت من» را بزنید:",
            reply_markup=markup)

    def user_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("📖 راهنما", "👤 پروفایل من")
        markup.add("🔄 به‌روزرسانی منو")
        return markup

    def owner_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("🎯 چالش‌ها", "📢 پیام عمومی")
        markup.add("🏆 اعلام برنده", "👤 پروفایل من")
        markup.add("📖 راهنما", "🔄 به‌روزرسانی منو")
        return markup

    def settings_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
        markup.add("🤖 منشی", "🛡️ امنیت")
        markup.add("⚡ اتوماسیون", "🔤 فونت")
        markup.add("📋 لیست‌ها", "🔙 بازگشت")
        return markup

    def security_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🛡️ ضد حذف", "🔗 ضد لینک")
        markup.add("🔒 قفل پیوی", "⚔️ پاسخ دشمن")
        markup.add("🔙 بازگشت")
        return markup

    def automation_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👁️ سین خودکار", "❤️ ری‌اکشن")
        markup.add("💾 ذخیره مدیا", "⏰ ساعت نام/بیو")
        markup.add("🔙 بازگشت")
        return markup

    def lists_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👤 مدیریت دشمن", "💚 مدیریت دوست")
        markup.add("🔙 بازگشت")
        return markup

    def enemy_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دشمن", "❌ حذف دشمن")
        markup.add("📋 نمایش دشمن‌ها", "🗑️ پاک کردن دشمن‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def friend_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دوست", "❌ حذف دوست")
        markup.add("📋 نمایش دوست‌ها", "🗑️ پاک کردن دوست‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def font_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=4)
        markup.add("فونت 0", "فونت 1", "فونت 2", "فونت 3")
        markup.add("فونت 4", "فونت 5", "فونت 6", "فونت 7")
        markup.add("فونت 8", "📝 لیست فونت", "🔙 بازگشت")
        return markup

    def challenges_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🧮 چالش ریاضی", "⚽ پیش‌بینی جام جهانی")
        markup.add("🔙 بازگشت")
        return markup

    def _admin_panel_keyboard():
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("📢 چنل‌های اجباری", callback_data="admin_channels"),
            types.InlineKeyboardButton("👥 کاربران", callback_data="admin_users"))
        markup.add(types.InlineKeyboardButton("🏆 جام جهانی", callback_data="admin_wc"),
            types.InlineKeyboardButton("🎲 قرعه‌کشی (مالک)", callback_data="admin_lottery"))
        markup.add(types.InlineKeyboardButton("💎 انتقال الماس", callback_data="admin_transfer"),
            types.InlineKeyboardButton("💰 دادن الماس", callback_data="admin_give"))
        markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
        return markup

    # ══════════════════════════════════════════════════════════════════════════
    # /start
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id
            logger.info(f"📩 دستور start از کاربر {tg_id} دریافت شد")
            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None
            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    if db.process_referral(referrer_id, tg_id):
                        referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                        if referrer_tg:
                            try: _bot.send_message(referrer_tg, f"🎉 یک نفر با لینک شما عضو شد!\n<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
                            except: pass
                except: pass
            if not require_membership(message): return
            account = get_user_account(tg_id)
            site_url = getattr(config, "SITE_URL", "")
            
            if not account:
                logger.warning(f"❌ کاربر {tg_id} در دیتابیس دائمی پیدا نشد")
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("🤖 ثبت‌نام با ربات", callback_data="signup_bot"))
                markup.add(types.InlineKeyboardButton("🌐 ثبت‌نام با سایت (غیرفعال)", callback_data="signup_site_disabled"))
                _bot.reply_to(message,
                    "👋 <b>سلام!</b>\n\nبرای استفاده از ربات، ابتدا ثبت‌نام کنید:\n\n"
                    "🤖 <b>ثبت‌نام با ربات:</b> سریع و آسان\n"
                    "🌐 <b>ثبت‌نام با سایت:</b> در حال حاضر غیرفعال",
                    reply_markup=markup)
                return

            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                markup = types.InlineKeyboardMarkup()
                if site_url: markup.add(types.InlineKeyboardButton("🌐 اتصال به تلگرام", url=site_url))
                _bot.reply_to(message,
                    f"👋 <b>سلام {account['username']}!</b>\n\n"
                    "🔗 شما در پنل وب ثبت‌نام کرده‌اید ولی حساب تلگرام متصل نیست!\n\n"
                    "📝 مراحل:\n1️⃣ وارد پنل وب شوید\n2️⃣ روی «اتصال به تلگرام» کلیک کنید\n3️⃣ شماره و کد را وارد کنید\n4️⃣ دوباره /start بزنید",
                    reply_markup=markup if site_url else None)
                return

            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            is_owner = (tg_id == config.OWNER_TG_ID)
            markup = owner_keyboard() if is_owner else user_keyboard()
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            bot_status = "🟢 فعال" if settings.get("self_bot_active") == "1" else "🔴 غیرفعال"
            _bot.reply_to(message,
                f"👋 خوش برگشتی <b>{account['username']}</b>!\n\n"
                f"📊 <b>وضعیت سلف:</b> {bot_status}\n"
                f"💎 <b>موجودی الماس:</b> {stats['balance']}\n"
                f"📈 <b>کل دریافتی:</b> {stats['total_earned']}\n\n"
                f"⚡ هر <b>۲ الماس</b> = <b>۲ ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=markup)
            sponsors = getattr(config, 'SPONSORS', [])
            if sponsors:
                sponsors_text = "🤝 <b>اسپانسرهای رسمی پروژه:</b>\n"
                for sp in sponsors: sponsors_text += f"🔸 @{sp['username']}\n"
                sponsors_text += f"\n👑 <b>مالک و پشتیبانی:</b> @{config.OWNER_USERNAME}"
                _bot.send_message(message.chat.id, sponsors_text)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_start: {e}")
            try: _bot.reply_to(message, f"⚠️ خطا رخ داد: {str(e)}\n\nلطفاً دوباره /start بزنید.")
            except: pass

    # ══════════════════════════════════════════════════════════════════════════
    # 🆕 ثبت‌نام با ربات
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "signup_bot")
    def callback_signup_bot(call):
        tg_id = call.from_user.id
        if get_user_account(tg_id):
            return _bot.answer_callback_query(call.id, "❌ قبلاً ثبت‌نام کرده‌اید!", show_alert=True)
        _signup_states[tg_id] = {"state": "username"}
        _bot.answer_callback_query(call.id)
        msg = _bot.send_message(call.message.chat.id,
            "🤖 <b>ثبت‌نام با ربات</b>\n\n📝 مرحله ۱ از ۴:\nنام کاربری (حداقل ۳ کاراکتر):",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو"))
        _bot.register_next_step_handler(msg, _process_signup_username)

    def _process_signup_username(message):
        tg_id = message.from_user.id
        if message.text == "❌ لغو":
            _signup_states.pop(tg_id, None)
            return _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        username = message.text.strip()
        if len(username) < 3:
            return _bot.reply_to(message, "❌ حداقل ۳ کاراکتر. دوباره:")
        if db.get_account_by_username(username):
            return _bot.reply_to(message, "❌ این نام قبلاً ثبت شده. نام دیگر:")
        _signup_states[tg_id]["username"] = username
        _signup_states[tg_id]["state"] = "password"
        msg = _bot.reply_to(message, f"✅ نام: <b>{username}</b>\n\n📝 مرحله ۲ از ۴:\nرمز عبور (حداقل ۶ کاراکتر):",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو"))
        _bot.register_next_step_handler(msg, _process_signup_password)

    def _process_signup_password(message):
        tg_id = message.from_user.id
        if message.text == "❌ لغو":
            _signup_states.pop(tg_id, None)
            return _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        password = message.text.strip()
        if len(password) < 6:
            return _bot.reply_to(message, "❌ حداقل ۶ کاراکتر. دوباره:")
        _signup_states[tg_id]["password"] = password
        _signup_states[tg_id]["state"] = "phone"
        msg = _bot.reply_to(message, "✅ رمز ذخیره شد.\n\n📝 مرحله ۳ از ۴:\nشماره تلفن (مثال: +989123456789):",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو"))
        _bot.register_next_step_handler(msg, _process_signup_phone)

    def _process_signup_phone(message):
        tg_id = message.from_user.id
        if message.text == "❌ لغو":
            _signup_states.pop(tg_id, None)
            return _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        phone = message.text.strip()
        if not phone.startswith("+"):
            return _bot.reply_to(message, "❌ باید با + شروع شود. مثال: +989123456789")
        _signup_states[tg_id]["phone"] = phone
        _bot.reply_to(message, "⏳ در حال ارسال کد...")
        def send_code():
            try:
                async def _send():
                    cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
                    await cl.connect()
                    result = await cl.send_code_request(phone)
                    sess = cl.session.save()
                    await cl.disconnect()
                    return result.phone_code_hash, sess
                loop = asyncio.new_event_loop()
                ph, sess = loop.run_until_complete(_send())
                loop.close()
                _signup_states[tg_id]["phone_hash"] = ph
                _signup_states[tg_id]["partial_sess"] = sess
                _signup_states[tg_id]["state"] = "code"
                _bot.send_message(message.chat.id,
                    "✅ کد ارسال شد!\n\n📝 مرحله ۴ از ۴:\nکد ۵ رقمی را وارد کنید:",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو"))
                _bot.register_next_step_handler(message, _process_signup_code)
            except FloodWaitError as e:
                _bot.send_message(message.chat.id, f"⏰ {e.seconds} ثانیه صبر کنید.")
                _signup_states.pop(tg_id, None)
            except Exception as e:
                _bot.send_message(message.chat.id, f"❌ خطا: {e}")
                _signup_states.pop(tg_id, None)
        threading.Thread(target=send_code, daemon=True).start()

    def _process_signup_code(message):
        tg_id = message.from_user.id
        if message.text == "❌ لغو":
            _signup_states.pop(tg_id, None)
            return _bot.reply_to(message, "❌ لغو شد.", reply_markup=types.ReplyKeyboardRemove())
        code = message.text.strip()
        if not code.isdigit() or len(code) < 4:
            return _bot.reply_to(message, "❌ کد باید عدد ۴-۵ رقمی باشد:")
        data = _signup_states[tg_id]
        _bot.reply_to(message, "⏳ در حال تأیید...", reply_markup=types.ReplyKeyboardRemove())
        def verify():
            try:
                async def _verify():
                    cl = TelegramClient(StringSession(data["partial_sess"]), config.API_ID, config.API_HASH)
                    await cl.connect()
                    await cl.sign_in(phone=data["phone"], code=code, phone_code_hash=data["phone_hash"])
                    me = await cl.get_me()
                    sess = cl.session.save()
                    await cl.disconnect()
                    return me.id, me.first_name, sess
                loop = asyncio.new_event_loop()
                tg_user_id, first_name, sess = loop.run_until_complete(_verify())
                loop.close()
                new_id = db.create_account(data["username"], data["password"])
                if not new_id:
                    _bot.send_message(message.chat.id, "❌ خطا در ایجاد حساب")
                    return
                db.init_user_settings(new_id)
                db.save_telegram_user_id(new_id, tg_user_id)
                db.save_session(new_id, sess, data["phone"])
                db.set_setting(new_id, "logged_in", "1")
                _signup_states.pop(tg_id, None)
                _bot.send_message(message.chat.id,
                    f"✅ <b>ثبت‌نام کامل شد!</b>\n\n👤 نام: <b>{data['username']}</b>\n💎 موجودی: <b>{config.WELCOME_TOKENS} الماس</b>\n\n🎉 حالا از دکمه‌های زیر استفاده کنید:",
                    reply_markup=user_keyboard())
            except SessionPasswordNeededError:
                _signup_states[tg_id]["state"] = "2fa"
                msg = _bot.send_message(message.chat.id,
                    "🔐 رمز دومرحله‌ای را وارد کنید:",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ لغو"))
                _bot.register_next_step_handler(msg, _process_signup_2fa)
            except (PhoneCodeInvalidError,):
                _bot.send_message(message.chat.id, "❌ کد اشتباه. /start بزنید.")
                _signup_states.pop(tg_id, None)
            except Exception as e:
                _bot.send_message(message.chat.id, f"❌ خطا: {e}")
                _signup_states.pop(tg_id, None)
        threading.Thread(target=verify, daemon=True).start()

    def _process_signup_2fa(message):
        tg_id = message.from_user.id
        if message.text == "❌ لغو":
            _signup_states.pop(tg_id, None)
            return
        data = _signup_states[tg_id]
        _bot.reply_to(message, "⏳ در حال تأیید...", reply_markup=types.ReplyKeyboardRemove())
        def verify():
            try:
                async def _verify():
                    cl = TelegramClient(StringSession(data["partial_sess"]), config.API_ID, config.API_HASH)
                    await cl.connect()
                    await cl.sign_in(password=message.text.strip())
                    me = await cl.get_me()
                    sess = cl.session.save()
                    await cl.disconnect()
                    return me.id, sess
                loop = asyncio.new_event_loop()
                tg_user_id, sess = loop.run_until_complete(_verify())
                loop.close()
                new_id = db.create_account(data["username"], data["password"])
                if new_id:
                    db.init_user_settings(new_id)
                    db.save_telegram_user_id(new_id, tg_user_id)
                    db.save_session(new_id, sess, data["phone"])
                    db.set_setting(new_id, "logged_in", "1")
                _signup_states.pop(tg_id, None)
                _bot.send_message(message.chat.id,
                    f"✅ <b>ثبت‌نام کامل شد!</b>\n👤 {data['username']}",
                    reply_markup=user_keyboard())
            except Exception as e:
                _bot.send_message(message.chat.id, f"❌ خطا: {e}")
                _signup_states.pop(tg_id, None)
        threading.Thread(target=verify, daemon=True).start()

    @_bot.callback_query_handler(func=lambda call: call.data == "signup_site_disabled")
    def callback_site_disabled(call):
        _bot.answer_callback_query(call.id, "⚠️ این قابلیت غیرفعال است.\nاز «ثبت‌نام با ربات» استفاده کنید.", show_alert=True)

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه بررسی عضویت
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        try:
            is_member, missing = cache.check_user_membership(_bot, call.from_user.id)
            if is_member:
                _bot.answer_callback_query(call.id, "عضویت تأیید شد! ✅")
                try: _bot.delete_message(call.message.chat.id, call.message.message_id)
                except: pass
                cmd_start(call.message)
            else:
                _bot.answer_callback_query(call.id, f"هنوز در {len(missing)} کانال عضو نشده‌اید! ❌", show_alert=True)
        except Exception as e:
            logger.error(f"❌ خطا در callback_check_join: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه به‌روزرسانی منو
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "🔄 به‌روزرسانی منو")
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
                site_url = getattr(config, "SITE_URL", "")
                markup = types.InlineKeyboardMarkup()
                if site_url: markup.add(types.InlineKeyboardButton("🌐 اتصال به تلگرام", url=site_url))
                _bot.reply_to(message, f"🔗 حساب شما متصل نیست!\n\n👤 {account['username']}\n📝 لطفاً از پنل وب اتصال را کامل کنید.",
                    reply_markup=markup if site_url else None)
                return
            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            is_owner = (tg_id == config.OWNER_TG_ID)
            markup = owner_keyboard() if is_owner else user_keyboard()
            bot_status = "🟢 فعال" if settings.get("self_bot_active") == "1" else "🔴 غیرفعال"
            _bot.reply_to(message,
                f"🔄 <b>منو به‌روزرسانی شد</b> ✅\n\n👋 {account['username']}\n📊 وضعیت سلف: {bot_status}\n💎 موجودی: {stats['balance']}\n📈 کل دریافتی: {stats['total_earned']}",
                reply_markup=markup)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_refresh_menu: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه‌های اصلی (فقط پیوی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "💰 موجودی")
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=user_keyboard())
            stats = get_user_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.reply_to(message,
                f"💎 <b>موجودی الماس</b>\n\n💰 فعلی: <b>{stats['balance']}</b>\n📊 کل: <b>{stats['total_earned']}</b>\n👥 رفرال: <b>{ref_count}</b> نفر\n💵 قیمت هر الماس: <b>{token_price} تومان</b>\n\n🎁 هدیه روزانه: {config.DAILY_TOKEN_GIFT} الماس",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_balance: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🎁 هدیه روزانه")
    def cmd_daily(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            success, msg = db.claim_daily_token(account["id"])
            if success:
                stats = get_user_stats(account["id"])
                _bot.reply_to(message, f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>", reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            else: _bot.reply_to(message, msg, reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_daily: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔗 رفرال")
    def cmd_referral(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            link = f"https://t.me/{BOT_USERNAME}?start=ref_{account['id']}"
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            referral_value = config.REFERRAL_TOKENS * token_price
            _bot.reply_to(message,
                f"🔗 <b>لینک رفرال شما:</b>\n<code>{link}</code>\n\n👥 تعداد: <b>{ref_count}</b>\n🎁 پاداش: <b>{config.REFERRAL_TOKENS} الماس</b> (معادل {referral_value} تومان)",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_referral: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🛒 خرید الماس")
    def cmd_buy(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            username_txt = account["username"] if account else str(message.from_user.id)
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("📩 خرید از مالک (@Amele55)", url="https://t.me/Amele55"))
            sponsors = getattr(config, 'SPONSORS', [])
            for sp in sponsors: markup.add(types.InlineKeyboardButton(f"🤝 {sp['name']}: @{sp['username']}", url=f"https://t.me/{sp['username']}"))
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.reply_to(message,
                f"🛒 <b>خرید الماس</b>\n\n💰 قیمت هر الماس: <b>{token_price} تومان</b>\n👤 یوزرنیم پنل شما: <b>{username_txt}</b>\n\nبرای خرید، روی دکمه «خرید از مالک» کلیک کنید و یوزرنیم پنل خود را ارسال نمایید.",
                reply_markup=markup)
        except Exception as e: logger.error(f"❌ خطا در cmd_buy: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "👤 پروفایل من")
    def cmd_profile(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            text = f"👤 <b>پروفایل کاربری</b>\n\n🆔 یوزرنیم: <b>{account['username']}</b>\n💎 موجودی: <b>{stats['balance']}</b>\n📊 کل دریافتی: <b>{stats['total_earned']}</b>\n👥 رفرال: <b>{db.get_referral_count(account['id'])}</b>\n🔤 فونت فعلی: <b>{settings.get('selected_font', '0')}</b>\n\n📅 تاریخ ثبت: {account.get('created_at', 'نامشخص')}"
            _bot.reply_to(message, text, reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_profile: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📊 وضعیت سلف")
    def cmd_status(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            settings = get_user_settings(account["id"])
            status_map = {"self_bot_active": "سلف‌بات", "secretary_active": "منشی", "anti_delete_active": "ضد حذف",
                "anti_link_active": "ضد لینک", "auto_seen_active": "سین خودکار", "auto_reaction_active": "ری‌اکشن",
                "private_lock_active": "قفل پیوی", "enemy_reply_active": "پاسخ دشمن", "auto_save_media": "ذخیره مدیا",
                "clock_name_active": "ساعت نام", "clock_bio_active": "ساعت بیو"}
            lines = [f"📊 <b>وضعیت سلف</b>\n"]
            for key, label in status_map.items():
                icon = "✅" if settings.get(key) == "1" else "❌"
                lines.append(f"{icon} {label}")
            lines.append(f"\n🔤 فونت: {settings.get('selected_font', '0')}")
            lines.append(f"👥 دشمن: {len(db.get_enemies(account['id']))} نفر")
            lines.append(f"💚 دوست: {len(db.get_friends(account['id']))} نفر")
            _bot.reply_to(message, "\n".join(lines), reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_status: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📖 راهنما")
    def cmd_help(message):
        try:
            if not require_membership(message): return
            help_text = """📖 <b>راهنمای AMEL SELF55</b>
🔹 <b>دستورات سلف‌بات:</b>
• سلف روشن / سلف خاموش
• وضعیت
• راهنما
🔹 <b>لیست‌ها:</b>
• تنظیم دشمن / حذف دشمن [ریپلای یا آیدی]
• نمایش لیست دشمن / پاک کردن لیست دشمن
• تنظیم دوست / حذف دوست
• نمایش لیست دوست / پاک کردن لیست دوست
🔹 <b>منشی:</b>
• منشی روشن / خاموش
• پیام منشی [متن]
🔹 <b>امنیت:</b>
• ضد حذف روشن / خاموش
• ضد لینک روشن / خاموش
• قفل پیوی روشن / خاموش
• پاسخ دشمن روشن / خاموش
🔹 <b>اتوماسیون:</b>
• سین خودکار روشن / خاموش
• ری‌اکشن روشن / خاموش / [ایموجی]
• ذخیره مدیا روشن / خاموش
• ساعت نام روشن / خاموش
• ساعت بیو روشن / خاموش
🔹 <b>ابزار:</b>
• ترجمه [متن]
• هوا [شهر]
• ارز
🔹 <b>فونت:</b>
• فونت [0-8] — تغییر فونت
• لیست فونت — نمایش نمونه‌ها
💡 برای مدیریت از دکمه‌های زیر استفاده کنید!"""
            _bot.reply_to(message, help_text, reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_help: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # تنظیمات سلف (فقط پیوی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚙️ تنظیمات سلف")
    def cmd_settings(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            settings = get_user_settings(account["id"])
            text = f"⚙️ <b>تنظیمات سلف</b>\n\n🟢 سلف: {'فعال' if settings.get('self_bot_active') == '1' else 'غیرفعال'}\n🤖 منشی: {'فعال' if settings.get('secretary_active') == '1' else 'غیرفعال'}\n🛡️ ضد حذف: {'فعال' if settings.get('anti_delete_active') == '1' else 'غیرفعال'}\n🔗 ضد لینک: {'فعال' if settings.get('anti_link_active') == '1' else 'غیرفعال'}\n🔒 قفل پیوی: {'فعال' if settings.get('private_lock_active') == '1' else 'غیرفعال'}\n⚔️ پاسخ دشمن: {'فعال' if settings.get('enemy_reply_active') == '1' else 'غیرفعال'}\n👁️ سین خودکار: {'فعال' if settings.get('auto_seen_active') == '1' else 'غیرفعال'}\n❤️ ری‌اکشن: {'فعال' if settings.get('auto_reaction_active') == '1' else 'غیرفعال'}\n💾 ذخیره مدیا: {'فعال' if settings.get('auto_save_media') == '1' else 'غیرفعال'}\n⏰ ساعت نام: {'فعال' if settings.get('clock_name_active') == '1' else 'غیرفعال'}\n⏰ ساعت بیو: {'فعال' if settings.get('clock_bio_active') == '1' else 'غیرفعال'}\n\n🔤 فونت: {settings.get('selected_font', '0')}"
            _bot.reply_to(message, text, reply_markup=settings_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_settings: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🟢 سلف روشن")
    def cmd_start_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            from bot import bot_manager
            try: loop = asyncio.get_event_loop()
            except:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            ok = bot_manager.start(account["id"], loop, check_tokens=True)
            if ok:
                db.set_setting(account["id"], "self_bot_active", "1")
                _bot.reply_to(message, "✅ سلف‌بات روشن شد!", reply_markup=settings_keyboard())
            else:
                balance = db.get_token_balance(account["id"])
                _bot.reply_to(message, f"❌ الماس کافی ندارید!\n💎 موجودی: {balance}\n⚡ نیاز: {config.TOKENS_PER_SESSION} الماس", reply_markup=settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_start_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔴 سلف خاموش")
    def cmd_stop_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            from bot import bot_manager
            bot_manager.stop(account["id"])
            db.set_setting(account["id"], "self_bot_active", "0")
            _bot.reply_to(message, "❌ سلف‌بات خاموش شد.", reply_markup=settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_stop_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🤖 منشی")
    def cmd_secretary_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            status = db.get_setting(account["id"], "secretary_active", "0")
            msg_text = db.get_setting(account["id"], "secretary_message", "در حال حاضر در دسترس نیستم.")
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(f"منشی {'روشن' if status == '1' else 'خاموش'}", "✏️ تغییر پیام منشی")
            markup.add("🔙 بازگشت")
            _bot.reply_to(message,
                f"🤖 <b>تنظیمات منشی</b>\n\nوضعیت: {'🟢 فعال' if status == '1' else '🔴 غیرفعال'}\nپیام: {msg_text}\n\n💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد.",
                reply_markup=markup)
        except Exception as e: logger.error(f"❌ خطا در cmd_secretary_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("منشی "))
    def cmd_toggle_secretary(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "secretary_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "secretary_active", new_status)
            _bot.reply_to(message, f"🤖 منشی {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=settings_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_secretary: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "✏️ تغییر پیام منشی")
    def cmd_change_secretary_msg(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            msg = _bot.reply_to(message, "✏️ <b>پیام جدید منشی را وارد کنید:</b>\n\n💡 می‌توانید از HTML نیز استفاده کنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            _bot.register_next_step_handler(msg, process_secretary_msg, account["id"])
        except Exception as e: logger.error(f"❌ خطا در cmd_change_secretary_msg: {e}")

    def process_secretary_msg(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به تنظیمات.", reply_markup=settings_keyboard())
                return
            db.set_setting(owner_id, "secretary_message", message.text)
            _bot.reply_to(message, "✅ پیام منشی ذخیره شد!", reply_markup=settings_keyboard())
        except Exception as e: logger.error(f"❌ خطا در process_secretary_msg: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🛡️ امنیت")
    def cmd_security_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            settings = get_user_settings(account["id"])
            text = f"🛡️ <b>تنظیمات امنیتی</b>\n\n🛡️ ضد حذف: {'✅ فعال' if settings.get('anti_delete_active') == '1' else '❌ غیرفعال'}\n🔗 ضد لینک: {'✅ فعال' if settings.get('anti_link_active') == '1' else '❌ غیرفعال'}\n🔒 قفل پیوی: {'✅ فعال' if settings.get('private_lock_active') == '1' else '❌ غیرفعال'}\n⚔️ پاسخ دشمن: {'✅ فعال' if settings.get('enemy_reply_active') == '1' else '❌ غیرفعال'}"
            _bot.reply_to(message, text, reply_markup=security_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_security_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🛡️ ضد حذف"))
    def cmd_toggle_anti_delete(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "anti_delete_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "anti_delete_active", new_status)
            _bot.reply_to(message, f"🛡️ ضد حذف {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=security_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_anti_delete: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🔗 ضد لینک"))
    def cmd_toggle_anti_link(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "anti_link_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "anti_link_active", new_status)
            _bot.reply_to(message, f"🔗 ضد لینک {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=security_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_anti_link: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🔒 قفل پیوی"))
    def cmd_toggle_private_lock(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "private_lock_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "private_lock_active", new_status)
            _bot.reply_to(message, f"🔒 قفل پیوی {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=security_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_private_lock: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("⚔️ پاسخ دشمن"))
    def cmd_toggle_enemy_reply(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "enemy_reply_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "enemy_reply_active", new_status)
            _bot.reply_to(message, f"⚔️ پاسخ دشمن {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=security_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_enemy_reply: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚡ اتوماسیون")
    def cmd_automation_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            settings = get_user_settings(account["id"])
            text = f"⚡ <b>تنظیمات اتوماسیون</b>\n\n👁️ سین خودکار: {'✅ فعال' if settings.get('auto_seen_active') == '1' else '❌ غیرفعال'}\n❤️ ری‌اکشن: {'✅ فعال' if settings.get('auto_reaction_active') == '1' else '❌ غیرفعال'}\n💾 ذخیره مدیا: {'✅ فعال' if settings.get('auto_save_media') == '1' else '❌ غیرفعال'}\n⏰ ساعت نام: {'✅ فعال' if settings.get('clock_name_active') == '1' else '❌ غیرفعال'}\n⏰ ساعت بیو: {'✅ فعال' if settings.get('clock_bio_active') == '1' else '❌ غیرفعال'}"
            _bot.reply_to(message, text, reply_markup=automation_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_automation_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("👁️ سین خودکار"))
    def cmd_toggle_auto_seen(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "auto_seen_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_seen_active", new_status)
            _bot.reply_to(message, f"👁️ سین خودکار {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=automation_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_auto_seen: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("❤️ ری‌اکشن"))
    def cmd_toggle_auto_reaction(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "auto_reaction_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_reaction_active", new_status)
            _bot.reply_to(message, f"❤️ ری‌اکشن خودکار {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=automation_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_auto_reaction: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("💾 ذخیره مدیا"))
    def cmd_toggle_auto_save(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "auto_save_media", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_save_media", new_status)
            _bot.reply_to(message, f"💾 ذخیره مدیا {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=automation_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_auto_save: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("⏰ ساعت نام/بیو"))
    def cmd_clock_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            settings = get_user_settings(account["id"])
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(f"ساعت نام {'روشن' if settings.get('clock_name_active') == '1' else 'خاموش'}")
            markup.add(f"ساعت بیو {'روشن' if settings.get('clock_bio_active') == '1' else 'خاموش'}")
            markup.add("🔙 بازگشت")
            _bot.reply_to(message,
                f"⏰ <b>تنظیمات ساعت</b>\n\nساعت نام: {'🟢 فعال' if settings.get('clock_name_active') == '1' else '🔴 غیرفعال'}\nساعت بیو: {'🟢 فعال' if settings.get('clock_bio_active') == '1' else '🔴 غیرفعال'}\n🔤 فونت فعلی: {settings.get('selected_font', '0')}",
                reply_markup=markup)
        except Exception as e: logger.error(f"❌ خطا در cmd_clock_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("ساعت نام "))
    def cmd_toggle_clock_name(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "clock_name_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "clock_name_active", new_status)
            _bot.reply_to(message, f"⏰ ساعت نام {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=settings_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_clock_name: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("ساعت بیو "))
    def cmd_toggle_clock_bio(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            status = db.get_setting(account["id"], "clock_bio_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "clock_bio_active", new_status)
            _bot.reply_to(message, f"⏰ ساعت بیو {'روشن' if new_status == '1' else 'خاموش'} شد!", reply_markup=settings_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_toggle_clock_bio: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔤 فونت")
    def cmd_font_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            current_font = db.get_setting(account["id"], "selected_font", "0")
            text = f"🔤 <b>انتخاب فونت</b>\n\nفونت فعلی: <b>{current_font}</b>\n\n💡 برای تغییر، روی یک دکمه کلیک کنید."
            _bot.reply_to(message, text, reply_markup=font_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_font_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("فونت ") and len(m.text) <= 7)
    def cmd_set_font(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            font_id = message.text.split()[-1]
            if font_id in ["0", "1", "2", "3", "4", "5", "6", "7", "8"]:
                db.set_setting(account["id"], "selected_font", font_id)
                _bot.reply_to(message, f"✅ فونت {font_id} انتخاب شد!", reply_markup=font_keyboard())
            else: _bot.reply_to(message, "❌ شماره فونت باید بین ۰ تا ۸ باشد.", reply_markup=font_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_set_font: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📝 لیست فونت")
    def cmd_list_fonts(message):
        try:
            if not require_membership(message): return
            test_text = "امیر"
            samples = {"0": "متن عادی", "1": "𝗕𝗹𝗱 𝗦𝗮𝗻𝘀", "2": "𝘐𝘵𝘢𝘭𝘪 𝘚𝘢𝘯𝘴", "3": "𝙼𝚘𝚘𝚜𝚙𝚌",
                "4": "Ｆｕｌｌｗｉｄｔｈ", "5": "𝐒𝐞𝐫𝐟 𝐁𝐥𝐝", "6": "𝒮𝒸𝓇𝓅𝓉", "7": "S̶t̶r̶i̶k̶e̶t̶h̶r̶o̶u̶g̶h̶", "8": "U̲n̲d̲e̲r̲l̲i̲n̲e̲"}
            from bot import FONTS
            lines = ["📝 <b>لیست فونت‌ها با نمونه:</b>\n"]
            lines.append("─" * 35)
            for k, v in samples.items():
                fn = FONTS.get(k, FONTS["0"])
                converted = fn(test_text)
                lines.append(f"<b>فونت {k}</b> — {v}: ")
                lines.append(f"  `{converted}` ")
                lines.append(" ")
            lines.append("─" * 35)
            lines.append("\n💡 استفاده: <code>فونت [شماره]</code>")
            lines.append("مثال: <code>فونت 3</code>")
            _bot.reply_to(message, "\n".join(lines), reply_markup=font_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_list_fonts: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 لیست‌ها")
    def cmd_lists_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            enemy_count = len(db.get_enemies(account["id"]))
            friend_count = len(db.get_friends(account["id"]))
            text = f"📋 <b>مدیریت لیست‌ها</b>\n\n👥 دشمن: <b>{enemy_count}</b> نفر\n💚 دوست: <b>{friend_count}</b> نفر"
            _bot.reply_to(message, text, reply_markup=lists_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_lists_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "👤 مدیریت دشمن")
    def cmd_enemy_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            enemies = db.get_enemies(account["id"])
            text = f"👤 <b>مدیریت دشمن</b>\n\n"
            if enemies:
                text += f"تعداد: <b>{len(enemies)}</b> نفر\n\n"
                for i, e in enumerate(enemies[:5], 1): text += f"{i}. {e.get('name') or e.get('username') or e.get('user_id')}\n"
                if len(enemies) > 5: text += f"\nو {len(enemies) - 5} نفر دیگر..."
            else: text += "📭 لیست دشمن خالی است."
            _bot.reply_to(message, text, reply_markup=enemy_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_enemy_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "💚 مدیریت دوست")
    def cmd_friend_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            friends = db.get_friends(account["id"])
            text = f"💚 <b>مدیریت دوست</b>\n\n"
            if friends:
                text += f"تعداد: <b>{len(friends)}</b> نفر\n\n"
                for i, f in enumerate(friends[:5], 1): text += f"{i}. {f.get('name') or f.get('username') or f.get('user_id')}\n"
                if len(friends) > 5: text += f"\nو {len(friends) - 5} نفر دیگر..."
            else: text += "📭 لیست دوست خالی است."
            _bot.reply_to(message, text, reply_markup=friend_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_friend_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "➕ افزودن دشمن")
    def cmd_add_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            msg = _bot.reply_to(message, "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            _bot.register_next_step_handler(msg, process_add_enemy, account["id"])
        except Exception as e: logger.error(f"❌ خطا در cmd_add_enemy_prompt: {e}")

    def process_add_enemy(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                username = sender.username
                name = sender.first_name
                db.add_enemy(owner_id, user_id, username, name)
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دشمن اضافه شد!", reply_markup=enemy_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                db.add_enemy(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دشمن اضافه شد!", reply_markup=enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_add_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "❌ حذف دشمن")
    def cmd_remove_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            msg = _bot.reply_to(message, "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            _bot.register_next_step_handler(msg, process_remove_enemy, account["id"])
        except Exception as e: logger.error(f"❌ خطا در cmd_remove_enemy_prompt: {e}")

    def process_remove_enemy(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                if db.remove_enemy(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر از لیست دشمن حذف شد!", reply_markup=enemy_keyboard())
                else: _bot.reply_to(message, "❌ کاربر در لیست دشمن نبود!", reply_markup=enemy_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                if db.remove_enemy(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دشمن حذف شد!", reply_markup=enemy_keyboard())
                else: _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دشمن نبود!", reply_markup=enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_remove_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 نمایش دشمن‌ها")
    def cmd_show_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            enemies = db.get_enemies(account["id"])
            if not enemies:
                _bot.reply_to(message, "📋 لیست دشمن خالی است.", reply_markup=enemy_keyboard())
                return
            lines = [f"🔴 <b>لیست دشمن ({len(enemies)} نفر):</b>\n"]
            for i, e in enumerate(enemies, 1):
                name = e.get('name') or e.get('username') or e.get('user_id')
                uid = e.get('user_id')
                lines.append(f"{i}. {name} — <code>{uid}</code>")
            _bot.reply_to(message, "\n".join(lines), reply_markup=enemy_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_show_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🗑️ پاک کردن دشمن‌ها")
    def cmd_clear_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            db.clear_enemies(account["id"])
            _bot.reply_to(message, "🗑️ لیست دشمن پاک شد!", reply_markup=enemy_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_clear_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "➕ افزودن دوست")
    def cmd_add_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            msg = _bot.reply_to(message, "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            _bot.register_next_step_handler(msg, process_add_friend, account["id"])
        except Exception as e: logger.error(f"❌ خطا در cmd_add_friend_prompt: {e}")

    def process_add_friend(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                username = sender.username
                name = sender.first_name
                db.add_friend(owner_id, user_id, username, name)
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دوست اضافه شد!", reply_markup=friend_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                db.add_friend(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دوست اضافه شد!", reply_markup=friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_add_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "❌ حذف دوست")
    def cmd_remove_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            msg = _bot.reply_to(message, "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            _bot.register_next_step_handler(msg, process_remove_friend, account["id"])
        except Exception as e: logger.error(f"❌ خطا در cmd_remove_friend_prompt: {e}")

    def process_remove_friend(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به لیست‌ها.", reply_markup=lists_keyboard())
                return
            replied = message.reply_to_message
            if replied:
                sender = replied.from_user
                user_id = sender.id
                if db.remove_friend(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر از لیست دوست حذف شد!", reply_markup=friend_keyboard())
                else: _bot.reply_to(message, "❌ کاربر در لیست دوست نبود!", reply_markup=friend_keyboard())
                return
            try:
                user_id = int(message.text.strip())
                if db.remove_friend(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دوست حذف شد!", reply_markup=friend_keyboard())
                else: _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دوست نبود!", reply_markup=friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", reply_markup=friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_remove_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 نمایش دوست‌ها")
    def cmd_show_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            friends = db.get_friends(account["id"])
            if not friends:
                _bot.reply_to(message, "📋 لیست دوست خالی است.", reply_markup=friend_keyboard())
                return
            lines = [f"💚 <b>لیست دوست ({len(friends)} نفر):</b>\n"]
            for i, f in enumerate(friends, 1):
                name = f.get('name') or f.get('username') or f.get('user_id')
                uid = f.get('user_id')
                lines.append(f"{i}. {name} — <code>{uid}</code>")
            _bot.reply_to(message, "\n".join(lines), reply_markup=friend_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_show_friends: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🗑️ پاک کردن دوست‌ها")
    def cmd_clear_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: return
            db.clear_friends(account["id"])
            _bot.reply_to(message, "🗑️ لیست دوست پاک شد!", reply_markup=friend_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_clear_friends: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔙 بازگشت")
    def cmd_back(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            is_owner = (message.from_user.id == config.OWNER_TG_ID)
            _bot.reply_to(message, "🔙 بازگشت به منوی اصلی.", reply_markup=owner_keyboard() if is_owner else user_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_back: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🎯 چالش‌ها (فقط مالک در پیوی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🎯 چالش‌ها")
    def cmd_challenges(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                _bot.reply_to(message, "⛔ این بخش فقط برای مالک است.")
                return
            _bot.reply_to(message,
                "🎯 **پنل مدیریت چالش‌ها**\n\n🧮 **چالش ریاضی**: هر ۲ ساعت یکبار در گروه\n⚽ **پیش‌بینی جام جهانی**: شرط‌بندی روی مسابقات",
                reply_markup=challenges_keyboard())
        except Exception as e: logger.error(f"❌ cmd_challenges error: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🧮 چالش ریاضی")
    def cmd_math_challenge(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            settings = db.get_challenge_settings(1)
            current = settings.get('math_challenge_active', False)
            db.update_challenge_settings(1, 'math_challenge_active', not current)
            status = "🟢 فعال" if not current else "🔴 غیرفعال"
            _bot.reply_to(message, f"🧮 **چالش ریاضی**\n\nوضعیت: {status}\n📅 هر ۲ ساعت یکبار در گروه ارسال می‌شود.\n💰 جایزه: ۱ الماس به پاسخ‌دهنده صحیح",
                reply_markup=challenges_keyboard())
        except Exception as e: logger.error(f"❌ cmd_math_challenge error: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚽ پیش‌بینی جام جهانی")
    def cmd_worldcup(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            _owner_states[message.chat.id] = {'step': 'team1'}
            msg = _bot.reply_to(message, "⚽ **ایجاد چالش جدید جام جهانی**\n\n📝 نام تیم اول را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو"))
            _bot.register_next_step_handler(msg, process_worldcup_team1)
        except Exception as e: logger.error(f"❌ cmd_worldcup error: {e}")

    def process_worldcup_team1(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            team1 = message.text.strip()
            _owner_states[message.chat.id] = {'step': 'team2', 'team1': team1}
            msg = _bot.reply_to(message, f"⚽ تیم اول: **{team1}**\n\n📝 نام تیم دوم را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو"))
            _bot.register_next_step_handler(msg, process_worldcup_team2, team1)
        except Exception as e: logger.error(f"❌ process_worldcup_team1 error: {e}")

    def process_worldcup_team2(message, team1):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            team2 = message.text.strip()
            _owner_states[message.chat.id] = {'step': 'time', 'team1': team1, 'team2': team2}
            msg = _bot.reply_to(message, f"⚽ تیم اول: **{team1}**\n⚽ تیم دوم: **{team2}**\n\n🕐 ساعت بازی را وارد کنید (مثال: 21:30):",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو"))
            _bot.register_next_step_handler(msg, process_worldcup_time, team1, team2)
        except Exception as e: logger.error(f"❌ process_worldcup_team2 error: {e}")

    def process_worldcup_time(message, team1, team2):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            match_time = message.text.strip()
            if not re.match(r'^\d{2}:\d{2}$', match_time):
                _bot.reply_to(message, "❌ فرمت ساعت اشتباه است!\nلطفاً ساعت را به فرمت <code>HH:MM</code> وارد کنید.\nمثال: <code>21:30</code>",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو"))
                return
            iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
            today = datetime.datetime.now(iran_tz).date()
            full_datetime = f"{today.isoformat()} {match_time}:00"
            _owner_states[message.chat.id] = {'step': 'photo', 'team1': team1, 'team2': team2, 'time': full_datetime}
            msg = _bot.reply_to(message, f"⚽ **اطلاعات مسابقه**\n\nتیم اول: **{team1}**\nتیم دوم: **{team2}**\nزمان: **{match_time}** (به وقت ایران)\n\n🖼️ لطفاً عکس یا لوگوی مسابقه را ارسال کنید:\n(می‌توانید یک عکس ارسال کنید یا روی «ردی» کلیک کنید)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("⏭ ردی"))
            _bot.register_next_step_handler(msg, process_worldcup_photo, team1, team2, full_datetime)
        except Exception as e: logger.error(f"❌ process_worldcup_time error: {e}")

    def process_worldcup_photo(message, team1, team2, match_time):
        try:
            photo_file_id = None
            if message.text == "⏭ ردی": logger.info("⏭ بدون عکس")
            elif message.photo: photo_file_id = message.photo[-1].file_id; logger.info(f"📸 عکس دریافت شد: {photo_file_id}")
            else:
                _bot.reply_to(message, "❌ لطفاً یک عکس ارسال کنید یا روی «ردی» کلیک کنید.")
                return
            bet_id = db.create_worldcup_bet(1, team1, team2, match_time, photo_file_id)
            if not bet_id:
                _bot.reply_to(message, "❌ خطا در ایجاد چالش. لطفاً مجدداً تلاش کنید.", reply_markup=owner_keyboard())
                return
            logger.info(f"✅ چالش با ID {bet_id} در دیتابیس ثبت شد")
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton(f"⚽ {team1}", callback_data=f"bet_wc_{bet_id}_{team1}"),
                types.InlineKeyboardButton(f"⚽ {team2}", callback_data=f"bet_wc_{bet_id}_{team2}"))
            caption = (f"⚽ **مسابقه جام جهانی**\n\n🆚 **{team1}** vs **{team2}**\n🕐 زمان: {match_time}\n\n💰 روی تیم مورد نظر شرط ببندید!\n📊 هر کاربر می‌تواند شرط خود را ثبت کند.\n🏆 برنده تمام الماس‌ها را دریافت می‌کند!")
            target_chat = "@Gp_SelfNexo"
            try:
                logger.info(f"📢 ارسال به گروه {target_chat}")
                if photo_file_id: sent = _bot.send_photo(target_chat, photo_file_id, caption=caption, reply_markup=markup)
                else: sent = _bot.send_message(target_chat, caption, reply_markup=markup)
                if sent:
                    try:
                        db.update_challenge_message(bet_id, sent.message_id, sent.chat.id)
                        logger.info(f"✅ پیام با موفقیت ارسال شد. Message ID: {sent.message_id}")
                    except Exception as e: logger.error(f"❌ خطا در ذخیره message_id: {e}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"❌ خطا در ارسال به گروه: {error_msg}")
                if "chat not found" in error_msg.lower() or "bot" in error_msg.lower():
                    _bot.reply_to(message, f"❌ گروه @Gp_SelfNexo پیدا نشد!\n\nلطفاً مراحل زیر را انجام دهید:\n1️⃣ ربات @{BOT_USERNAME} را به گروه اضافه کنید\n2️⃣ به ربات اجازه ارسال پیام بدهید\n3️⃣ دوباره امتحان کنید\n\nخطا: {error_msg}", reply_markup=owner_keyboard())
                else: _bot.reply_to(message, f"❌ خطا در ارسال: {error_msg}", reply_markup=owner_keyboard())
                return
            _owner_states.pop(message.chat.id, None)
            _bot.reply_to(message, f"✅ **چالش ایجاد شد!**\n\n⚽ {team1} vs {team2}\n🕐 {match_time}\n📢 در گروه @Gp_SelfNexo ارسال شد.",
                reply_markup=owner_keyboard())
        except Exception as e:
            logger.error(f"❌ process_worldcup_photo error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=owner_keyboard())

    @_bot.callback_query_handler(func=lambda call: call.data.startswith('bet_wc_'))
    def callback_wc_bet(call):
        try:
            parts = call.data.split('_')
            if len(parts) < 4:
                _bot.answer_callback_query(call.id, "❌ لینک نامعتبر.", show_alert=True)
                return
            try: bet_id = int(parts[2])
            except ValueError:
                _bot.answer_callback_query(call.id, "❌ شناسه نامعتبر.", show_alert=True)
                return
            selected_team = parts[3]
            bet = db.get_worldcup_bet_by_message(call.message.message_id, call.message.chat.id)
            if not bet: bet = db.get_bet_game(bet_id)
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
            msg = _bot.send_message(call.message.chat.id,
                f"⚽ **شرط‌بندی**\n\nتیم انتخاب شده: **{selected_team}**\n💰 میزان الماس خود را وارد کنید:\n\n📊 موجودی شما: {db.get_token_balance(account['id'])} الماس\n💡 عدد  برای لغو",
                reply_to_message_id=call.message.message_id)
            _bot.register_next_step_handler(msg, process_wc_bet_amount, bet['id'], tg_id, selected_team)
            _bot.answer_callback_query(call.id, "✅ انتخاب ثبت شد!")
        except Exception as e:
            logger.error(f"❌ callback_wc_bet error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    def process_wc_bet_amount(message, bet_id, user_tg_id, selected_team):
        try:
            try: amount = int(message.text.strip())
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
            _bot.reply_to(message, f"✅ **شرط شما ثبت شد!**\n\n⚽ تیم: **{selected_team}**\n💎 میزان: **{amount}** الماس\n\n🔄 پس از پایان بازی، برنده اعلام می‌شود.")
        except Exception as e:
            logger.error(f"❌ process_wc_bet_amount error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🏆 اعلام برنده")
    def cmd_announce_winner(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            bet = db.get_active_worldcup_bet(1)
            if not bet:
                _bot.reply_to(message, "❌ هیچ مسابقه فعالی وجود ندارد.")
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton(f"🏆 {bet['team1']}", callback_data=f"winner_{bet['team1']}"),
                types.InlineKeyboardButton(f"🏆 {bet['team2']}", callback_data=f"winner_{bet['team2']}"))
            _bot.reply_to(message, f"🏆 **اعلام برنده مسابقه**\n\n⚽ {bet['team1']} vs {bet['team2']}\n🕐 زمان: {bet['match_time']}\n\nتیم برنده را انتخاب کنید:",
                reply_markup=markup)
        except Exception as e: logger.error(f"❌ cmd_announce_winner error: {e}")

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
                if account: db.add_tokens(account['id'], winner_amount)
            db.finish_worldcup_bet(bet['id'], winner)
            target_chat = "@Gp_SelfNexo"
            try:
                _bot.send_message(target_chat, f"🏆 **نتیجه مسابقه**\n\n⚽ برنده: **{winner}**\n💎 کل الماس‌ها: **{total_tokens}**\n👥 تعداد برندگان: **{len(winners)}** نفر\n🎁 هر برنده: **{winner_amount}** الماس\n\n🎉 به برندگان تبریک می‌گوییم!")
            except Exception as e: logger.error(f"❌ خطا در ارسال نتیجه به گروه: {e}")
            _bot.answer_callback_query(call.id, f"✅ برنده {winner} اعلام شد!")
            _bot.reply_to(call.message, f"✅ برنده **{winner}** اعلام شد!", reply_markup=owner_keyboard())
        except Exception as e:
            logger.error(f"❌ callback_winner error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📢 پیام عمومی")
    def cmd_broadcast(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            msg = _bot.reply_to(message, "📢 **ارسال پیام عمومی**\n\n✏️ متن پیام خود را وارد کنید:\n(از HTML برای فرمت‌دهی استفاده کنید)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو"))
            _bot.register_next_step_handler(msg, process_broadcast)
        except Exception as e: logger.error(f"❌ cmd_broadcast error: {e}")

    def process_broadcast(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            broadcast_text = message.text
            users = db.get_all_accounts()
            success_count = 0
            _bot.reply_to(message, f"⏳ در حال ارسال پیام به {len(users)} کاربر...", reply_markup=owner_keyboard())
            for user in users:
                tg_id = db.get_telegram_id_by_owner(user['id'])
                if tg_id:
                    try:
                        _bot.send_message(tg_id, broadcast_text, parse_mode="HTML")
                        success_count += 1
                        time.sleep(0.05)
                    except Exception as e: logger.error(f"❌ ارسال به {tg_id} ناموفق: {e}")
            _bot.send_message(message.chat.id, f"✅ **پیام عمومی ارسال شد!**\n\n📨 ارسال به: {success_count} از {len(users)} کاربر",
                reply_markup=owner_keyboard())
        except Exception as e:
            logger.error(f"❌ process_broadcast error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=owner_keyboard())

    # ══════════════════════════════════════════════════════════════════════════
    # 📌 دستورات گروه (فقط موجودی و شرط‌بندی)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'] and m.text and m.text.strip() == "موجودی")
    def cmd_group_balance(message):
        try:
            account = get_user_account(message.from_user.id)
            if account:
                stats = db.get_token_stats(account["id"])
                _bot.reply_to(message, f"💎 موجودی شما: {stats['balance']} الماس")
            else: _bot.reply_to(message, "❌ شما در پنل ثبت‌نام نکرده‌اید!\nلطفاً در ربات @Nexo55bot ثبت‌نام کنید.")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_group_balance: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'] and m.text and m.text.strip().startswith("شرط بندی"))
    def cmd_bet(message):
        try:
            text = message.text.strip()
            parts = text.split()
            if len(parts) < 3:
                _bot.reply_to(message, "❌ فرمت صحیح:\n<code>شرط بندی [مقدار]</code>\nمثال: <code>شرط بندی 100</code>")
                return
            try:
                bet_amount = int(parts[-1])
                if bet_amount <= 0:
                    _bot.reply_to(message, "❌ مقدار باید بیشتر از ۰ باشد.")
                    return
            except ValueError:
                _bot.reply_to(message, "❌ مقدار باید عدد باشد.\nمثال: <code>شرط بندی 100</code>")
                return
            chat_id = message.chat.id
            player1_id = message.from_user.id
            account = db.get_account_by_tg_id(player1_id)
            if not account:
                _bot.reply_to(message, "❌ شما در پنل ثبت‌نام نکرده‌اید!\nلطفاً در ربات @Nexo55bot ثبت‌نام کنید.")
                return
            balance = db.get_token_balance(account['id'])
            if balance < bet_amount:
                _bot.reply_to(message, f"❌ موجودی ناکافی!\n💎 موجودی شما: {balance} الماس\n📊 نیاز: {bet_amount} الماس")
                return
            db.deduct_tokens(account['id'], bet_amount)
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("🎲 شرکت در قرعه‌کشی", callback_data=f"join_bet_{chat_id}_{int(time.time())}"))
            sent = _bot.reply_to(message,
                f"🎲 **شرط‌بندی جدید!**\n\n👤 بازیکن اول: @{message.from_user.username or message.from_user.first_name}\n💰 مبلغ شرط: <b>{bet_amount}</b> الماس\n\n👇 روی دکمه زیر کلیک کنید تا در قرعه‌کشی شرکت کنید!\n⏳ این بازی تا ۱ ساعت اعتبار دارد.",
                reply_markup=markup)
            db.create_bet_game(1, chat_id, player1_id, bet_amount, sent.message_id)
        except Exception as e:
            logger.error(f"❌ خطا در cmd_bet: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    @_bot.callback_query_handler(func=lambda call: call.data.startswith('join_bet_'))
    def callback_join_bet(call):
        try:
            parts = call.data.split('_')
            if len(parts) < 3:
                _bot.answer_callback_query(call.id, "❌ لینک نامعتبر.", show_alert=True)
                return
            chat_id = int(parts[2])
            player2_id = call.from_user.id
            game = db.get_bet_game_by_message(chat_id, call.message.message_id)
            if not game:
                _bot.answer_callback_query(call.id, "❌ این بازی منقضی شده است.", show_alert=True)
                return
            if game['player1_id'] == player2_id:
                _bot.answer_callback_query(call.id, "❌ شما خودتان بازیکن اول هستید!", show_alert=True)
                return
            if game['player2_id']:
                _bot.answer_callback_query(call.id, "❌ قبلاً یک بازیکن دیگر ثبت شده است.", show_alert=True)
                return
            account = db.get_account_by_tg_id(player2_id)
            if not account:
                _bot.answer_callback_query(call.id, "❌ شما در پنل ثبت‌نام نکرده‌اید!\nلطفاً در ربات @Nexo55bot ثبت‌نام کنید.", show_alert=True)
                return
            bet_amount = game['bet_amount']
            balance = db.get_token_balance(account['id'])
            if balance < bet_amount:
                _bot.answer_callback_query(call.id, f"❌ موجودی ناکافی!\n💎 موجودی شما: {balance} الماس\n📊 نیاز: {bet_amount} الماس", show_alert=True)
                return
            db.deduct_tokens(account['id'], bet_amount)
            db.join_bet_game(game['id'], player2_id)
            player1_account = db.get_account_by_tg_id(game['player1_id'])
            player1_name = f"@{player1_account['username']}" if player1_account else str(game['player1_id'])
            player2_name = f"@{call.from_user.username or call.from_user.first_name}"
            winner_id = random.choice([game['player1_id'], player2_id])
            db.finish_bet_game(game['id'], winner_id)
            winner_account = db.get_account_by_tg_id(winner_id)
            winner_name = f"@{winner_account['username']}" if winner_account else str(winner_id)
            total_amount = bet_amount * 2
            db.add_tokens(winner_account['id'], total_amount)
            try:
                _bot.edit_message_reply_markup(chat_id=chat_id, message_id=game['message_id'], reply_markup=None)
            except: pass
            _bot.send_message(chat_id,
                f"🎉 **نتیجه قرعه‌کشی!**\n\n👤 بازیکن اول: {player1_name}\n👤 بازیکن دوم: {player2_name}\n💰 مبلغ شرط: <b>{bet_amount}</b> الماس\n🏆 کل جایزه: <b>{total_amount}</b> الماس\n\n🎊 **برنده: {winner_name}**\n\n💎 {total_amount} الماس به حساب برنده واریز شد!")
            _bot.answer_callback_query(call.id, "✅ شما در قرعه‌کشی شرکت کردید!")
        except Exception as e:
            logger.error(f"❌ خطا در callback_join_bet: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    @_bot.message_handler(func=lambda m: m.text and m.text.strip().startswith("انتقال"))
    def cmd_transfer(message):
        try:
            replied = message.reply_to_message
            if not replied:
                _bot.reply_to(message, "❌ لطفاً روی پیام کاربر مورد نظر ریپلای کنید.\nفرمت: <code>انتقال [مقدار]</code>\nمثال: <code>انتقال 100</code>")
                return
            parts = message.text.strip().split()
            if len(parts) < 2:
                _bot.reply_to(message, "❌ فرمت صحیح:\n<code>انتقال [مقدار]</code>\nمثال: <code>انتقال 100</code>")
                return
            try:
                amount = int(parts[1])
                if amount <= 0:
                    _bot.reply_to(message, "❌ مقدار باید بیشتر از ۰ باشد.")
                    return
            except ValueError:
                _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
                return
            from_tg_id = message.from_user.id
            to_tg_id = replied.from_user.id
            if from_tg_id == to_tg_id:
                _bot.reply_to(message, "❌ نمی‌توانید به خودتان الماس انتقال دهید.")
                return
            from_account = db.get_account_by_tg_id(from_tg_id)
            if not from_account:
                _bot.reply_to(message, "❌ شما در پنل ثبت‌نام نکرده‌اید!\nلطفاً در ربات @Nexo55bot ثبت‌نام کنید.")
                return
            balance = db.get_token_balance(from_account['id'])
            if balance < amount:
                _bot.reply_to(message, f"❌ موجودی ناکافی!\n💎 موجودی شما: {balance} الماس\n📊 نیاز: {amount} الماس")
                return
            to_account = db.get_account_by_tg_id(to_tg_id)
            if not to_account:
                _bot.reply_to(message, "❌ کاربر مورد نظر در پنل ثبت‌نام نکرده است.")
                return
            success = db.transfer_tokens(from_account['id'], to_tg_id, amount)
            if success:
                new_balance = db.get_token_balance(from_account['id'])
                _bot.reply_to(message,
                    f"✅ **انتقال الماس انجام شد!**\n\n👤 از: @{message.from_user.username or message.from_user.first_name}\n👤 به: @{replied.from_user.username or replied.from_user.first_name}\n💰 مقدار: <b>{amount}</b> الماس\n💎 موجودی شما: <b>{new_balance}</b> الماس")
                try:
                    _bot.send_message(to_tg_id, f"🎁 **دریافت الماس!**\n\n👤 از: @{message.from_user.username or message.from_user.first_name}\n💰 مقدار: <b>{amount}</b> الماس\n💎 به حساب شما واریز شد!")
                except: pass
            else: _bot.reply_to(message, "❌ خطا در انتقال الماس. لطفاً مجدداً تلاش کنید.")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_transfer: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'])
    def ignore_group_messages(message): pass

    @_bot.message_handler(func=lambda m: m.chat.type == 'private')
    def cmd_unknown_private(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            if not require_membership(message): return
            is_owner = (message.from_user.id == config.OWNER_TG_ID)
            _bot.reply_to(message, "📱 لطفاً از دکمه‌های زیر استفاده کنید:", reply_markup=owner_keyboard() if is_owner else user_keyboard())
        except Exception as e: logger.error(f"❌ خطا در cmd_unknown_private: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📢 پنل مدیریت مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "📢 مدیریت", chat_types=['private'])
    def cmd_admin_panel(message):
        if message.from_user.id != OWNER_TG_ID: return
        _bot.reply_to(message, "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=_admin_panel_keyboard())

    @_bot.callback_query_handler(func=lambda call: call.data.startswith("admin_") or call.data.startswith("rmch_") or call.data.startswith("wcwin_") or call.data.startswith("wc_") or call.data == "addch_prompt")
    def callback_admin(call):
        if call.from_user.id != OWNER_TG_ID:
            return _bot.answer_callback_query(call.id, "❌ فقط مالک دسترسی دارد", show_alert=True)
        try:
            data = call.data
            if data == "admin_panel" or data == "admin_back":
                _bot.edit_message_text("📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=_admin_panel_keyboard())
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
                else: text = "📋 لیست چنل‌ها خالی است.\n\n"
                text += "\nبرای افزودن چنل جدید از دکمه زیر استفاده کنید:"
                markup.add(types.InlineKeyboardButton("➕ افزودن چنل جدید", callback_data="addch_prompt"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            elif data.startswith("rmch_"):
                ch = data[5:]
                if not ch.startswith("@"): ch = "@" + ch
                if db.remove_forced_channel(ch):
                    _bot.answer_callback_query(call.id, f"✅ چنل {ch} حذف شد")
                    call.data = "admin_channels"
                    callback_admin(call)
                else: _bot.answer_callback_query(call.id, "❌ خطا در حذف")
                return
            elif data == "addch_prompt":
                _owner_states[call.from_user.id] = {"state": "waiting_channel"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text("📝 آیدی چنل را ارسال کنید (با @ شروع شود):\n\nمثال: <code>@mychannel</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            elif data == "admin_users":
                accounts = db.get_all_accounts()
                if not accounts: text = "هیچ کاربری ثبت نشده."
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
                        markup.add(types.InlineKeyboardButton(f"✅ {c['team1']}", callback_data=f"wcwin_{c['id']}_{c['team1']}"),
                            types.InlineKeyboardButton(f"✅ {c['team2']}", callback_data=f"wcwin_{c['id']}_{c['team2']}"))
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
                    for r in results:
                        if r["result"] == "won":
                            try: _bot.send_message(r["user_tg_id"], f"🎉 تبریک! شرط شما درست بود.\n💎 <b>{r['amount']} الماس</b> دریافت کردید.")
                            except: pass
                else: _bot.answer_callback_query(call.id, f"❌ خطا: {results}", show_alert=True)
                return
            elif data == "admin_lottery":
                _owner_states[call.from_user.id] = {"state": "lottery_amount"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text("🎲 <b>ایجاد قرعه‌کشی گروهی (مالک)</b>\n\n💎 مبلغ جایزه را ارسال کنید (الماس):\n\nمثال: <code>100</code>\n\n📌 قرعه‌کشی در گروه <code>@amelselfgap</code> ایجاد می‌شود",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            elif data == "admin_transfer":
                _owner_states[call.from_user.id] = {"state": "transfer_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text("💎 <b>انتقال الماس (از طرف سیستم)</b>\n\n📝 یوزرنیم کاربر مقصد را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            elif data == "admin_give":
                _owner_states[call.from_user.id] = {"state": "give_user", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text("💰 <b>دادن الماس به کاربر</b>\n\n📝 یوزرنیم کاربر را ارسال کنید:\n\nمثال: <code>ali</code>",
                    chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                _bot.answer_callback_query(call.id)
                return
            else: _bot.answer_callback_query(call.id, "❌ گزینه نامعتبر")
        except Exception as e:
            print(f"❌ خطا در callback_admin: {e}")
            try: _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except: pass

    @_bot.message_handler(func=lambda m: m.from_user.id == OWNER_TG_ID and m.from_user.id in _owner_states, chat_types=['private'])
    def handle_owner_state(message):
        try:
            state_data = _owner_states[message.from_user.id]
            state = state_data["state"]
            text = message.text.strip()
            if state == "waiting_channel":
                if not text.startswith("@"): text = "@" + text
                if db.add_forced_channel(text):
                    _bot.reply_to(message, f"✅ چنل <b>{text}</b> اضافه شد.", reply_markup=_owner_keyboard())
                else: _bot.reply_to(message, f"⚠️ خطا یا تکراری است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            elif state == "wc_team1":
                state_data["data"]["team1"] = text
                state_data["state"] = "wc_team2"
                _bot.reply_to(message, f"✅ تیم اول: <b>{text}</b>\n\n📝 مرحله  از ۴:\nنام <b>تیم دوم</b> را ارسال کنید:")
            elif state == "wc_team2":
                state_data["data"]["team2"] = text
                state_data["state"] = "wc_time"
                _bot.reply_to(message, f"✅ تیم دوم: <b>{text}</b>\n\n📝 مرحله ۳ از ۴:\n⏰ ساعت بازی را ارسال کنید:\n\nمثال: <code>20:30</code>")
            elif state == "wc_time":
                state_data["data"]["time"] = text
                state_data["state"] = "wc_bet"
                _bot.reply_to(message, f"✅ ساعت: <b>{text}</b>\n\n📝 مرحله ۴ از ۴:\n💎 مبلغ شرط (الماس) را ارسال کنید:\n\nمثال: <code>10</code>")
            elif state == "wc_bet":
                try: bet_amount = int(text)
                except: return _bot.reply_to(message, "❌ مبلغ باید عدد باشد. دوباره تلاش کنید:")
                data = state_data["data"]
                challenge_id = db.create_world_cup_challenge(data["team1"], data["team2"], data["time"], bet_amount)
                group = getattr(config, 'WORLD_CUP_GROUP', '@amelselfgap')
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton(f"🔵 {data['team1']}", callback_data=f"bet_wc_{challenge_id}_{data['team1']}"),
                    types.InlineKeyboardButton(f"🔴 {data['team2']}", callback_data=f"bet_wc_{challenge_id}_{data['team2']}"))
                try:
                    msg = _bot.send_message(group,
                        f"⚽️ <b>چالش جام جهانی!</b>\n\n🆚 <b>{data['team1']}</b> در برابر <b>{data['team2']}</b>\n⏰ ساعت: <b>{data['time']}</b>\n💎 مبلغ شرط: <b>{bet_amount} الماس</b>\n\nکدام تیم برنده می‌شود؟ شرط ببندید!",
                        reply_markup=markup)
                    db.update_challenge_message(challenge_id, msg.message_id, msg.chat.id)
                    _bot.reply_to(message, f"✅ چالش با موفقیت ایجاد شد!\n\n🆚 {data['team1']} vs {data['team2']}\n⏰ {data['time']} | 💎 {bet_amount}\n📢 ID چالش: <code>{challenge_id}</code>",
                        reply_markup=_owner_keyboard())
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا در ارسال به گروه: {e}\nمطمئن شوید ربات در {group} ادمین است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            elif state == "lottery_amount":
                try: prize = int(text)
                except: return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
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
                    _bot.reply_to(message, f"✅ قرعه‌کشی در گروه {group} ایجاد شد!\n\n💎 جایزه: {prize} الماس\n📢 ID: <code>{lottery_id}</code>",
                        reply_markup=_owner_keyboard())
                    threading.Timer(120, _auto_finish_lottery, args=[lottery_id, group]).start()
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا: {e}\nمطمئن شوید ربات در {group} ادمین است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            elif state == "transfer_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "transfer_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            elif state == "transfer_amount":
                try: amount = int(text)
                except: return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                username = state_data["data"]["username"]
                to_account = db.get_account_by_username(username)
                if not to_account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                db.add_tokens(to_account["id"], amount)
                new_balance = db.get_token_balance(to_account["id"])
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try: _bot.send_message(to_tg_id, f"🎁 <b>{amount} الماس</b> از طرف سیستم دریافت کردید!\n💎 موجودی جدید: <b>{new_balance}</b>")
                    except: pass
                _bot.reply_to(message, f"✅ <b>{amount} الماس</b> به <b>{to_account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b>",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            elif state == "give_user":
                state_data["data"]["username"] = text.lstrip("@")
                state_data["state"] = "give_amount"
                _bot.reply_to(message, f"📝 کاربر: <b>{text}</b>\n\n💎 مبلغ الماس را ارسال کنید:")
            elif state == "give_amount":
                try: amount = int(text)
                except: return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                username = state_data["data"]["username"]
                account = db.get_account_by_username(username)
                if not account:
                    _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.", reply_markup=_owner_keyboard())
                    _owner_states.pop(message.from_user.id, None)
                    return
                db.add_tokens(account["id"], amount)
                new_balance = db.get_token_balance(account["id"])
                token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
                tg_id = db.get_telegram_id_by_owner(account["id"])
                if tg_id:
                    try: _bot.send_message(tg_id, f"🎁 <b>{amount} الماس</b> از طرف مالک دریافت کردید!\n💎 موجودی جدید: <b>{new_balance}</b>")
                    except: pass
                _bot.reply_to(message, f"✅ <b>{amount}</b> الماس به <b>{account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b> (معادل {new_balance * token_price} تومان)",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
        except Exception as e:
            print(f"❌ خطا در handle_owner_state: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}", reply_markup=_owner_keyboard())
            _owner_states.pop(message.from_user.id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # 🎲 قرعه‌کشی در گروه و پیوی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("قرعه "), chat_types=['private', 'group', 'supergroup'])
    def cmd_lottery(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            parts = message.text.split()
            if len(parts) < 2: return _bot.reply_to(message, "❗ فرمت: قرعه [تعداد الماس]\nمثال: قرعه 100")
            try:
                prize = int(parts[1])
                if prize < 1: return _bot.reply_to(message, "❌ مبلغ باید بیشتر از 0 باشد.")
            except: return _bot.reply_to(message, "❌ مبلغ باید عدد باشد.")
            balance = db.get_token_balance(account["id"])
            if balance < prize: return _bot.reply_to(message, f"❌ موجودی کافی ندارید! نیاز به {prize} الماس دارید.\nموجودی فعلی: {balance} الماس")
            if not db.deduct_tokens(account["id"], prize): return _bot.reply_to(message, "❌ خطا در کسر الماس!")
            lottery_id = db.create_lottery(chat_id=message.chat.id, creator_tg_id=message.from_user.id, prize_amount=prize, duration_minutes=2, entry_fee=prize)
            _lottery_players[lottery_id] = [message.from_user.id]
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton(f"🎲 شرکت در قرعه‌کشی ({prize} الماس)", callback_data=f"join_lottery_{lottery_id}"))
            creator_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            msg = _bot.reply_to(message,
                f"🎉 <b>قرعه‌کشی!</b>\n\n👤 سازنده: {creator_name}\n💎 مبلغ ورودی: <b>{prize} الماس</b>\n💰 مجموع جایزه: <b>{prize * 2} الماس</b>\n👥 شرکت‌کنندگان: ۱ نفر\n\n⏳ برای شرکت، روی دکمه زیر کلیک کنید!\n(با ورود نفر دوم، قرعه‌کشی انجام می‌شود)",
                reply_markup=markup)
            db.update_lottery_message(lottery_id, msg.message_id)
            threading.Timer(120, _auto_finish_lottery, args=[lottery_id, message.chat.id]).start()
        except Exception as e:
            print(f"❌ خطا در cmd_lottery: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

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
            if not account: return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            entry_fee = lottery["prize_amount"]
            balance = db.get_token_balance(account["id"])
            if balance < entry_fee:
                return _bot.answer_callback_query(call.id, f"❌ موجودی کافی ندارید! نیاز به {entry_fee} الماس دارید.", show_alert=True)
            if not db.deduct_tokens(account["id"], entry_fee):
                return _bot.answer_callback_query(call.id, "❌ خطا در کسر الماس!", show_alert=True)
            success, msg = db.join_lottery(lottery_id, call.from_user.id, account["id"], entry_fee)
            if success:
                if lottery_id not in _lottery_players: _lottery_players[lottery_id] = []
                _lottery_players[lottery_id].append(call.from_user.id)
                _bot.answer_callback_query(call.id, f"✅ با {entry_fee} الماس ثبت‌نام کردید!", show_alert=True)
                if len(_lottery_players[lottery_id]) >= 2:
                    _finish_lottery_immediately(lottery_id, call.message.chat.id)
                else:
                    try:
                        _bot.edit_message_text(f"🎉 <b>قرعه‌کشی!</b>\n\n💎 مبلغ ورودی: <b>{entry_fee} الماس</b>\n💰 مجموع جایزه: <b>{entry_fee * 2} الماس</b>\n👥 شرکت‌کنندگان: {len(_lottery_players[lottery_id])} نفر\n\n⏳ منتظر نفر دوم...",
                            chat_id=call.message.chat.id, message_id=call.message.message_id)
                    except: pass
            else: _bot.answer_callback_query(call.id, msg, show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_join_lottery: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)

    def _finish_lottery_immediately(lottery_id, chat_id):
        try:
            lottery = db.get_lottery(lottery_id)
            if not lottery or lottery["status"] != "active": return
            participants = db.get_lottery_participants(lottery_id)
            if len(participants) < 2: return
            total_prize = lottery["prize_amount"] * 2
            winner = random.choice(participants)
            db.add_tokens(winner["owner_id"], total_prize)
            db.finish_lottery(lottery_id, winner["user_tg_id"], winner["owner_id"])
            try:
                winner_account = db.get_account(winner["owner_id"])
                winner_name = winner_account["username"] if winner_account else str(winner["user_tg_id"])
            except: winner_name = str(winner["user_tg_id"])
            msg_text = (f"🎉 <b>قرعه‌کشی به پایان رسید!</b>\n\n🏆 برنده: <b>{winner_name}</b>\n💎 مجموع جایزه: <b>{total_prize} الماس</b>\n👥 شرکت‌کنندگان: {len(participants)} نفر\n\n🎊 تبریک به برنده!")
            if _bot:
                try:
                    _bot.send_message(chat_id, msg_text)
                    _bot.send_message(winner["user_tg_id"], f"🎉 تبریک! شما برنده قرعه‌کشی شدید!\n💎 <b>{total_prize} الماس</b> به حساب شما واریز شد.")
                    for p in participants:
                        if p["user_tg_id"] != winner["user_tg_id"]:
                            try: _bot.send_message(p["user_tg_id"], f"😔 متاسفانه شما برنده قرعه‌کشی نشدید!\n💎 {lottery['prize_amount']} الماس از حساب شما کسر شد.")
                            except: pass
                except Exception as e: print(f"❌ خطا در ارسال پیام: {e}")
            _lottery_players.pop(lottery_id, None)
        except Exception as e: print(f"❌ خطا در _finish_lottery_immediately: {e}")

    def _auto_finish_lottery(lottery_id, chat_id):
        try:
            lottery = db.get_lottery(lottery_id)
            if not lottery or lottery["status"] != "active": return
            participants = db.get_lottery_participants(lottery_id)
            if len(participants) < 2:
                db.finish_lottery(lottery_id, None, None)
                creator_id = lottery["creator_tg_id"]
                creator_account = db.get_account_by_tg_id(creator_id)
                if creator_account: db.add_tokens(creator_account["id"], lottery["prize_amount"])
                if _bot:
                    _bot.send_message(chat_id, f"⏰ قرعه‌کشی لغو شد!\n\n❌ تعداد شرکت‌کنندگان کافی نبود (حداقل ۲ نفر).\n💎 {lottery['prize_amount']} الماس به سازنده برگشت داده شد.")
            _lottery_players.pop(lottery_id, None)
        except Exception as e: print(f"❌ خطا در _auto_finish_lottery: {e}")

    @_bot.message_handler(func=lambda m: m.text and m.text == "موجودی", chat_types=['group', 'supergroup'])
    def cmd_balance_group(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            stats = db.get_token_stats(account["id"])
            _bot.reply_to(message, f"💎 <b>موجودی شما:</b>\n💰 الماس: <b>{stats['balance']}</b>")
        except Exception as e: print(f"❌ خطا در cmd_balance_group: {e}")

    @_bot.message_handler(func=lambda m: m.text and m.text.startswith("انتقال "), chat_types=['private', 'group', 'supergroup'])
    def cmd_transfer(message):
        try:
            parts = message.text.split()
            if len(parts) < 3: return _bot.reply_to(message, "❗ فرمت: انتقال [یوزرنیم] [تعداد]\nمثال: انتقال @ali 10")
            username = parts[1].lstrip("@")
            try:
                amount = int(parts[2])
                if amount < 1: return _bot.reply_to(message, "❌ مقدار باید بیشتر از 0 باشد.")
            except: return _bot.reply_to(message, "❌ مقدار باید عدد باشد.")
            from_account = get_user_account(message.from_user.id)
            if not from_account: return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            to_account = db.get_account_by_username(username)
            if not to_account: return _bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد.")
            if to_account["id"] == from_account["id"]: return _bot.reply_to(message, "❌ نمی‌توانید به خودتان الماس انتقال دهید.")
            success, msg = db.transfer_diamonds(from_account["id"], to_account["id"], amount)
            if success:
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try: _bot.send_message(to_tg_id, f"💎 <b>{amount} الماس</b> از @{message.from_user.username or 'کاربر'} دریافت کردید!")
                    except: pass
            _bot.reply_to(message, msg)
        except Exception as e:
            print(f"❌ خطا در cmd_transfer: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}")

    @_bot.message_handler(func=lambda m: True, chat_types=['private'])
    def cmd_unknown(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return _bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.\n/start را بزنید.")
            kb = _owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard()
            _bot.reply_to(message, "⚠️ دستور نامعتبر. از دکمه‌های زیر استفاده کنید:", reply_markup=kb)
        except Exception as e: print(f"❌ خطا در cmd_unknown: {e}")

    def _process_referral_async(referrer_id, tg_id):
        try:
            if db.process_referral(referrer_id, tg_id):
                referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                if referrer_tg and _bot:
                    _bot.send_message(referrer_tg, f"🎉 یک نفر با لینک شما عضو شد!\n<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
        except Exception as e: print(f"❌ خطا در رفرال: {e}")

    def _polling_loop():
        while True:
            try:
                _bot.infinity_polling(timeout=20, long_polling_timeout=15, restart_on_change=False, skip_pending=True)
            except Exception as e:
                if "409" in str(e):
                    time.sleep(10)
                    try: _bot.delete_webhook(drop_pending_updates=True)
                    except: pass
                else:
                    print(f"⚠️ خطای polling: {e}")
                    time.sleep(3)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    print(f"✅ ربات الماس @{BOT_USERNAME} استارت شد")
