import threading
import telebot
from telebot import types
import database as db
import db_cache as cache
import config
import datetime

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = config.OWNER_TG_ID

# ─── کش کاربران ──────────────────────────────────────────────────────────────
_user_cache = {}
_user_cache_time = {}
_CACHE_TTL = 5  # کش 5 ثانیه برای جلوگیری از کش قدیمی

def get_user_account(tg_id: int):
    """
    دریافت حساب کاربر - از دیتابیس دائمی (Supabase) با کش 5 ثانیه
    برای اینکه کاربر بعد از ثبت‌نام سریعاً شناسایی بشه
    """
    now = datetime.datetime.now().timestamp()
    
    # بررسی کش با TTL کوتاه
    if tg_id in _user_cache:
        if now - _user_cache_time.get(tg_id, 0) < _CACHE_TTL:
            return _user_cache[tg_id]
    
    # دریافت از دیتابیس دائمی
    account = db.get_account_by_tg_id(tg_id)
    _user_cache[tg_id] = account
    _user_cache_time[tg_id] = now
    print(f"🔍 جستجوی کاربر {tg_id} در دیتابیس: {account}")
    return account

def clear_user_cache(tg_id: int = None):
    """پاک کردن کش کاربر (بعد از ثبت‌نام یا لاگین)"""
    if tg_id:
        _user_cache.pop(tg_id, None)
        _user_cache_time.pop(tg_id, None)
    else:
        _user_cache.clear()
        _user_cache_time.clear()
    print("✅ کش کاربران پاک شد")

# ─── کش تنظیمات ──────────────────────────────────────────────────────────────
_user_settings_cache = {}
_cache_timestamps = {}

def get_cached_setting(owner_id: int, key: str, default=None):
    """
    دریافت تنظیمات - از دیتابیس دائمی (Supabase) با کش 5 ثانیه
    """
    cache_key = f"{owner_id}:{key}"
    now = datetime.datetime.now().timestamp()
    
    if cache_key in _user_settings_cache:
        if now - _cache_timestamps.get(cache_key, 0) < _CACHE_TTL:
            return _user_settings_cache[cache_key]
    
    value = db.get_setting(owner_id, key, default)
    _user_settings_cache[cache_key] = value
    _cache_timestamps[cache_key] = now
    return value

def clear_settings_cache():
    """پاک کردن کش تنظیمات"""
    _user_settings_cache.clear()
    _cache_timestamps.clear()
    print("✅ کش تنظیمات پاک شد")

def get_bot():
    return _bot

def start_token_bot():
    global _bot, BOT_USERNAME

    # ✅ تست اتصال به دیتابیس دائمی
    try:
        test = db.get_all_accounts()
        print(f"✅ اتصال به دیتابیس دائمی (Supabase) برقرار است! تعداد کاربران: {len(test)}")
    except Exception as e:
        print(f"❌ خطا در اتصال به دیتابیس دائمی: {e}")
        return

    # ✅ تست اتصال به دیتابیس موقت
    try:
        channels = cache.get_forced_channels()
        print(f"✅ اتصال به دیتابیس موقت (SQLite) برقرار است! تعداد چنل‌ها: {len(channels)}")
    except Exception as e:
        print(f"❌ خطا در اتصال به دیتابیس موقت: {e}")

    if not config.BOT_TOKEN:
        print("⚠️ BOT_TOKEN تنظیم نشده — ربات مدیریت غیرفعال است")
        return

    _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=False)

    try:
        me = _bot.get_me()
        BOT_USERNAME = me.username
        print(f"🤖 ربات مدیریت: @{BOT_USERNAME}")
    except Exception as e:
        print(f"❌ خطا در اتصال ربات مدیریت: {e}")
        _bot = None
        return

    import time as _time
    for attempt in range(3):
        try:
            _bot.delete_webhook(drop_pending_updates=True)
            _time.sleep(3)
            break
        except:
            _time.sleep(3)

    # ─── توابع کمکی ──────────────────────────────────────────────────────────
    def get_user_stats(owner_id: int):
        """دریافت آمار کاربر از دیتابیس دائمی"""
        return db.get_token_stats(owner_id)

    def get_user_settings(owner_id: int):
        """دریافت تنظیمات کاربر از دیتابیس دائمی"""
        keys = [
            "self_bot_active", "secretary_active", "anti_delete_active",
            "anti_link_active", "auto_seen_active", "auto_reaction_active",
            "private_lock_active", "enemy_reply_active", "auto_save_media",
            "clock_name_active", "clock_bio_active", "selected_font",
        ]
        return {k: get_cached_setting(owner_id, k, "0") for k in keys}

    # ─── بررسی عضویت در چنل‌های اجباری (از دیتابیس موقت) ──────────────────
    def require_membership(message):
        """
        بررسی عضویت کاربر در چنل‌های اجباری
        ✅ از دیتابیس موقت (db_cache) استفاده میکنه
        """
        tg_id = message.from_user.id
        is_member, missing = cache.check_user_membership(_bot, tg_id)
        if not is_member:
            send_forced_channels_menu(message, missing)
            return False
        return True

    def send_forced_channels_menu(message, missing_channels):
        """نمایش منوی عضویت اجباری"""
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

    # ─── ساخت کیبوردها ──────────────────────────────────────────────────────
    def user_keyboard():
        """کیبورد اصلی کاربر"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید توکن")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("📖 راهنما", "👤 پروفایل من")
        markup.add("🔄 به‌روزرسانی منو")
        return markup

    def settings_keyboard():
        """کیبورد تنظیمات سلف"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
        markup.add("🤖 منشی", "🛡️ امنیت")
        markup.add("⚡ اتوماسیون", "🔤 فونت")
        markup.add("📋 لیست‌ها", "🔙 بازگشت")
        return markup

    def security_keyboard():
        """کیبورد تنظیمات امنیتی"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🛡️ ضد حذف", "🔗 ضد لینک")
        markup.add("🔒 قفل پیوی", "⚔️ پاسخ دشمن")
        markup.add("🔙 بازگشت")
        return markup

    def automation_keyboard():
        """کیبورد تنظیمات اتوماسیون"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👁️ سین خودکار", "❤️ ری‌اکشن")
        markup.add("💾 ذخیره مدیا", "⏰ ساعت نام/بیو")
        markup.add("🔙 بازگشت")
        return markup

    def lists_keyboard():
        """کیبورد مدیریت لیست‌ها"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("👤 مدیریت دشمن", "💚 مدیریت دوست")
        markup.add("🔙 بازگشت")
        return markup

    def enemy_keyboard():
        """کیبورد مدیریت دشمن"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دشمن", "❌ حذف دشمن")
        markup.add("📋 نمایش دشمن‌ها", "🗑️ پاک کردن دشمن‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def friend_keyboard():
        """کیبورد مدیریت دوست"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("➕ افزودن دوست", "❌ حذف دوست")
        markup.add("📋 نمایش دوست‌ها", "🗑️ پاک کردن دوست‌ها")
        markup.add("🔙 بازگشت")
        return markup

    def font_keyboard():
        """کیبورد انتخاب فونت"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=4)
        markup.add("فونت 0", "فونت 1", "فونت 2", "فونت 3")
        markup.add("فونت 4", "فونت 5", "فونت 6", "فونت 7")
        markup.add("فونت 8", "📝 لیست فونت", "🔙 بازگشت")
        return markup

    def owner_keyboard():
        """کیبورد مالک"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید توکن")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("📢 مدیریت چنل‌ها", "👥 مدیریت کاربران")
        markup.add("📖 راهنما", "👤 پروفایل من")
        markup.add("🔄 به‌روزرسانی منو")
        return markup

    def owner_users_keyboard():
        """کیبورد مدیریت کاربران برای مالک"""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("📋 لیست کاربران", "🎁 هدیه به کاربر")
        markup.add("🔙 بازگشت")
        return markup

    # ─── /start ─────────────────────────────────────────────────────────────
    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id
            print(f"📩 دستور start از کاربر {tg_id} دریافت شد")
            
            # ۱. پردازش رفرال
            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None
            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    if db.process_referral(referrer_id, tg_id):
                        referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                        if referrer_tg:
                            try:
                                _bot.send_message(referrer_tg, 
                                    f"🎉 یک نفر با لینک شما عضو شد!\n"
                                    f"<b>+{config.REFERRAL_TOKENS} توکن</b> دریافت کردید 🪙")
                            except:
                                pass
                except:
                    pass

            # ۲. بررسی عضویت در چنل‌های اجباری (از دیتابیس موقت)
            if not require_membership(message):
                return

            # ۳. بررسی حساب کاربری (از دیتابیس دائمی)
            account = get_user_account(tg_id)
            print(f"👤 حساب کاربری پیدا شده: {account}")
            site_url = getattr(config, "SITE_URL", "")

            # ❌ اگر حساب وجود نداشت
            if not account:
                print(f"❌ کاربر {tg_id} در دیتابیس دائمی پیدا نشد")
                markup = types.InlineKeyboardMarkup()
                if site_url:
                    markup.add(types.InlineKeyboardButton("🌐 ثبت‌نام در پنل", url=site_url))
                _bot.reply_to(message, 
                    "👋 <b>سلام!</b>\n\n"
                    "❌ شما هنوز در پنل وب ثبت‌نام نکرده‌اید!\n\n"
                    "📝 مراحل:\n"
                    "1️⃣ در پنل وب ثبت‌نام کنید\n"
                    "2️⃣ حساب تلگرام را وصل کنید\n"
                    "3️⃣ دوباره /start بزنید", 
                    reply_markup=markup if site_url else None)
                return

            # ✅ حساب وجود دارد ولی لاگین نیست
            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            print(f"📊 وضعیت لاگین کاربر {tg_id}: {logged_in}")
            
            if not logged_in:
                markup = types.InlineKeyboardMarkup()
                if site_url:
                    markup.add(types.InlineKeyboardButton("🌐 اتصال به تلگرام", url=site_url))
                _bot.reply_to(message, 
                    f"👋 <b>سلام {account['username']}!</b>\n\n"
                    "🔗 شما در پنل وب ثبت‌نام کرده‌اید ولی حساب تلگرام متصل نیست!\n\n"
                    "📝 مراحل:\n"
                    "1️⃣ وارد پنل وب شوید\n"
                    "2️⃣ روی «اتصال به تلگرام» کلیک کنید\n"
                    "3️⃣ شماره و کد را وارد کنید\n"
                    "4️⃣ دوباره /start بزنید", 
                    reply_markup=markup if site_url else None)
                return

            # ✅ کاربر کامل است - نمایش منو
            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            
            # تشخیص مالک
            is_owner = (tg_id == config.OWNER_TG_ID)
            
            markup = owner_keyboard() if is_owner else user_keyboard()
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            # وضعیت سلف
            bot_status = "🟢 فعال" if settings.get("self_bot_active") == "1" else "🔴 غیرفعال"
            
            _bot.reply_to(
                message,
                f"👋 خوش برگشتی <b>{account['username']}</b>!\n\n"
                f"📊 <b>وضعیت سلف:</b> {bot_status}\n"
                f"🪙 <b>موجودی توکن:</b> {stats['balance']}\n"
                f"📈 <b>کل دریافتی:</b> {stats['total_earned']}\n\n"
                f"⚡ هر <b>۲ توکن</b> = <b>۲ ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر توکن: <b>{token_price} تومان</b>",
                reply_markup=markup
            )

            # نمایش اسپانسرها
            sponsors = getattr(config, 'SPONSORS', [])
            if sponsors:
                sponsors_text = "🤝 <b>اسپانسرهای رسمی پروژه:</b>\n"
                for sp in sponsors:
                    sponsors_text += f"🔸 @{sp['username']}\n"
                sponsors_text += f"\n👑 <b>مالک و پشتیبانی:</b> @{config.OWNER_USERNAME}"
                _bot.send_message(message.chat.id, sponsors_text)
        
        except Exception as e:
            print(f"❌ خطا در cmd_start: {e}")
            try:
                _bot.reply_to(message, f"⚠️ خطا رخ داد: {str(e)}\n\nلطفاً دوباره /start بزنید.")
            except:
                pass

    # ─── دکمه بررسی عضویت (از دیتابیس موقت) ──────────────────────────────
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
            print(f"❌ خطا در callback_check_join: {e}")

    # ─── دکمه به‌روزرسانی منو ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔄 به‌روزرسانی منو")
    def cmd_refresh_menu(message):
        """به‌روزرسانی منوی کاربر"""
        try:
            tg_id = message.from_user.id
            
            if not require_membership(message):
                return
            
            # پاک کردن کش کاربر
            clear_user_cache(tg_id)
            
            account = get_user_account(tg_id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", 
                                   reply_markup=types.ReplyKeyboardRemove())
            
            # بررسی مجدد لاگین
            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            if not logged_in:
                site_url = getattr(config, "SITE_URL", "")
                markup = types.InlineKeyboardMarkup()
                if site_url:
                    markup.add(types.InlineKeyboardButton("🌐 اتصال به تلگرام", url=site_url))
                _bot.reply_to(message, 
                    f"🔗 حساب شما متصل نیست!\n\n"
                    f"👤 {account['username']}\n"
                    f"📝 لطفاً از پنل وب اتصال را کامل کنید.",
                    reply_markup=markup if site_url else None)
                return
            
            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            
            is_owner = (tg_id == config.OWNER_TG_ID)
            markup = owner_keyboard() if is_owner else user_keyboard()
            bot_status = "🟢 فعال" if settings.get("self_bot_active") == "1" else "🔴 غیرفعال"
            
            _bot.reply_to(
                message,
                f"🔄 <b>منو به‌روزرسانی شد</b> ✅\n\n"
                f"👋 {account['username']}\n"
                f"📊 وضعیت سلف: {bot_status}\n"
                f"🪙 موجودی: {stats['balance']}\n"
                f"📈 کل دریافتی: {stats['total_earned']}",
                reply_markup=markup
            )
        except Exception as e:
            print(f"❌ خطا در cmd_refresh_menu: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}")

    # ─── دکمه‌های اصلی ────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "💰 موجودی")
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: 
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=user_keyboard())
            
            stats = get_user_stats(account["id"])
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            _bot.reply_to(
                message,
                f"🪙 <b>موجودی توکن</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر توکن: <b>{token_price} تومان</b>\n\n"
                f"🎁 هدیه روزانه: {config.DAILY_TOKEN_GIFT} توکن",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard()
            )
        except Exception as e:
            print(f"❌ خطا در cmd_balance: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🎁 هدیه روزانه")
    def cmd_daily(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: 
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", 
                                   reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            
            success, msg = db.claim_daily_token(account["id"])
            if success:
                stats = get_user_stats(account["id"])
                _bot.reply_to(message, f"{msg}\n\n💰 موجودی جدید: <b>{stats['balance']}</b>", 
                            reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            else:
                _bot.reply_to(message, msg, 
                            reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_daily: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔗 رفرال")
    def cmd_referral(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account: 
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", 
                                   reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            
            link = f"https://t.me/{BOT_USERNAME}?start=ref_{account['id']}"
            ref_count = db.get_referral_count(account["id"])
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            referral_value = config.REFERRAL_TOKENS * token_price
            
            _bot.reply_to(
                message,
                f"🔗 <b>لینک رفرال شما:</b>\n<code>{link}</code>\n\n"
                f"👥 تعداد: <b>{ref_count}</b>\n"
                f"🎁 پاداش: <b>{config.REFERRAL_TOKENS} توکن</b> (معادل {referral_value} تومان)",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard()
            )
        except Exception as e:
            print(f"❌ خطا در cmd_referral: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🛒 خرید توکن")
    def cmd_buy(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            username_txt = account["username"] if account else str(message.from_user.id)
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("📩 خرید از مالک (@Amele55)", url="https://t.me/Amele55"))
            sponsors = getattr(config, 'SPONSORS', [])
            for sp in sponsors:
                markup.add(types.InlineKeyboardButton(f"🤝 {sp['name']}: @{sp['username']}", url=f"https://t.me/{sp['username']}"))

            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            _bot.reply_to(
                message,
                f"🛒 <b>خرید توکن</b>\n\n"
                f"💰 قیمت هر توکن: <b>{token_price} تومان</b>\n"
                f"👤 یوزرنیم پنل شما: <b>{username_txt}</b>\n\n"
                f"برای خرید، روی دکمه «خرید از مالک» کلیک کنید و یوزرنیم پنل خود را ارسال نمایید.",
                reply_markup=markup
            )
        except Exception as e:
            print(f"❌ خطا در cmd_buy: {e}")

    @_bot.message_handler(func=lambda m: m.text == "👤 پروفایل من")
    def cmd_profile(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            
            text = f"👤 <b>پروفایل کاربری</b>\n\n"
            text += f"🆔 یوزرنیم: <b>{account['username']}</b>\n"
            text += f"🪙 موجودی: <b>{stats['balance']}</b>\n"
            text += f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n"
            text += f"👥 رفرال: <b>{db.get_referral_count(account['id'])}</b>\n"
            text += f"🔤 فونت فعلی: <b>{settings.get('selected_font', '0')}</b>\n"
            text += f"\n📅 تاریخ ثبت: {account.get('created_at', 'نامشخص')}"
            
            _bot.reply_to(message, text, 
                         reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_profile: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📊 وضعیت سلف")
    def cmd_status(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            settings = get_user_settings(account["id"])
            
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
            
            _bot.reply_to(message, "\n".join(lines),
                         reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_status: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📖 راهنما")
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
            
            _bot.reply_to(message, help_text,
                         reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_help: {e}")

    # ─── تنظیمات سلف (از دیتابیس دائمی) ────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "⚙️ تنظیمات سلف")
    def cmd_settings(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            settings = get_user_settings(account["id"])
            
            text = f"⚙️ <b>تنظیمات سلف</b>\n\n"
            text += f"🟢 سلف: {'فعال' if settings.get('self_bot_active') == '1' else 'غیرفعال'}\n"
            text += f"🤖 منشی: {'فعال' if settings.get('secretary_active') == '1' else 'غیرفعال'}\n"
            text += f"🛡️ ضد حذف: {'فعال' if settings.get('anti_delete_active') == '1' else 'غیرفعال'}\n"
            text += f"🔗 ضد لینک: {'فعال' if settings.get('anti_link_active') == '1' else 'غیرفعال'}\n"
            text += f"🔒 قفل پیوی: {'فعال' if settings.get('private_lock_active') == '1' else 'غیرفعال'}\n"
            text += f"⚔️ پاسخ دشمن: {'فعال' if settings.get('enemy_reply_active') == '1' else 'غیرفعال'}\n"
            text += f"👁️ سین خودکار: {'فعال' if settings.get('auto_seen_active') == '1' else 'غیرفعال'}\n"
            text += f"❤️ ری‌اکشن: {'فعال' if settings.get('auto_reaction_active') == '1' else 'غیرفعال'}\n"
            text += f"💾 ذخیره مدیا: {'فعال' if settings.get('auto_save_media') == '1' else 'غیرفعال'}\n"
            text += f"⏰ ساعت نام: {'فعال' if settings.get('clock_name_active') == '1' else 'غیرفعال'}\n"
            text += f"⏰ ساعت بیو: {'فعال' if settings.get('clock_bio_active') == '1' else 'غیرفعال'}\n"
            text += f"\n🔤 فونت: {settings.get('selected_font', '0')}"
            
            _bot.reply_to(message, text, reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_settings: {e}")

    # ─── دکمه‌های تنظیمات ──────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🟢 سلف روشن")
    def cmd_start_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            from bot import bot_manager
            import asyncio
            
            try:
                loop = asyncio.get_event_loop()
            except:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            ok = bot_manager.start(account["id"], loop, check_tokens=True)
            if ok:
                db.set_setting(account["id"], "self_bot_active", "1")
                _bot.reply_to(message, "✅ سلف‌بات روشن شد!", reply_markup=settings_keyboard())
            else:
                balance = db.get_token_balance(account["id"])
                _bot.reply_to(message, 
                    f"❌ توکن کافی ندارید!\n💰 موجودی: {balance}\n⚡ نیاز: {config.TOKENS_PER_SESSION} توکن",
                    reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_start_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "🔴 سلف خاموش")
    def cmd_stop_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            from bot import bot_manager
            bot_manager.stop(account["id"])
            db.set_setting(account["id"], "self_bot_active", "0")
            _bot.reply_to(message, "❌ سلف‌بات خاموش شد.", reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_stop_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "🤖 منشی")
    def cmd_secretary_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            status = get_cached_setting(account["id"], "secretary_active", "0")
            msg_text = get_cached_setting(account["id"], "secretary_message", "در حال حاضر در دسترس نیستم.")
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(f"منشی {'روشن' if status == '1' else 'خاموش'}", "✏️ تغییر پیام منشی")
            markup.add("🔙 بازگشت")
            
            _bot.reply_to(message,
                f"🤖 <b>تنظیمات منشی</b>\n\n"
                f"وضعیت: {'🟢 فعال' if status == '1' else '🔴 غیرفعال'}\n"
                f"پیام: {msg_text}\n\n"
                f"💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد.",
                reply_markup=markup)
        except Exception as e:
            print(f"❌ خطا در cmd_secretary_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("منشی "))
    def cmd_toggle_secretary(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "secretary_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "secretary_active", new_status)
            
            _user_settings_cache.pop(f"{account['id']}:secretary_active", None)
            
            _bot.reply_to(message, 
                f"🤖 منشی {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_secretary: {e}")

    @_bot.message_handler(func=lambda m: m.text == "✏️ تغییر پیام منشی")
    def cmd_change_secretary_msg(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>پیام جدید منشی را وارد کنید:</b>\n\n"
                "💡 می‌توانید از HTML نیز استفاده کنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_secretary_msg, account["id"])
        except Exception as e:
            print(f"❌ خطا در cmd_change_secretary_msg: {e}")

    def process_secretary_msg(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به تنظیمات.", reply_markup=settings_keyboard())
                return
            
            db.set_setting(owner_id, "secretary_message", message.text)
            _user_settings_cache.pop(f"{owner_id}:secretary_message", None)
            _bot.reply_to(message, "✅ پیام منشی ذخیره شد!", reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_secretary_msg: {e}")

    # ─── امنیت ──────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🛡️ امنیت")
    def cmd_security_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            settings = get_user_settings(account["id"])
            
            text = f"🛡️ <b>تنظیمات امنیتی</b>\n\n"
            text += f"🛡️ ضد حذف: {'✅ فعال' if settings.get('anti_delete_active') == '1' else '❌ غیرفعال'}\n"
            text += f"🔗 ضد لینک: {'✅ فعال' if settings.get('anti_link_active') == '1' else '❌ غیرفعال'}\n"
            text += f"🔒 قفل پیوی: {'✅ فعال' if settings.get('private_lock_active') == '1' else '❌ غیرفعال'}\n"
            text += f"⚔️ پاسخ دشمن: {'✅ فعال' if settings.get('enemy_reply_active') == '1' else '❌ غیرفعال'}"
            
            _bot.reply_to(message, text, reply_markup=security_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_security_menu: {e}")

    # ─── دکمه‌های امنیت ────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text.startswith("🛡️ ضد حذف"))
    def cmd_toggle_anti_delete(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "anti_delete_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "anti_delete_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:anti_delete_active", None)
            
            _bot.reply_to(message, 
                f"🛡️ ضد حذف {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=security_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_anti_delete: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🔗 ضد لینک"))
    def cmd_toggle_anti_link(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "anti_link_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "anti_link_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:anti_link_active", None)
            
            _bot.reply_to(message, 
                f"🔗 ضد لینک {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=security_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_anti_link: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🔒 قفل پیوی"))
    def cmd_toggle_private_lock(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "private_lock_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "private_lock_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:private_lock_active", None)
            
            _bot.reply_to(message, 
                f"🔒 قفل پیوی {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=security_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_private_lock: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("⚔️ پاسخ دشمن"))
    def cmd_toggle_enemy_reply(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "enemy_reply_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "enemy_reply_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:enemy_reply_active", None)
            
            _bot.reply_to(message, 
                f"⚔️ پاسخ دشمن {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=security_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_enemy_reply: {e}")

    # ─── اتوماسیون ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "⚡ اتوماسیون")
    def cmd_automation_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            settings = get_user_settings(account["id"])
            
            text = f"⚡ <b>تنظیمات اتوماسیون</b>\n\n"
            text += f"👁️ سین خودکار: {'✅ فعال' if settings.get('auto_seen_active') == '1' else '❌ غیرفعال'}\n"
            text += f"❤️ ری‌اکشن: {'✅ فعال' if settings.get('auto_reaction_active') == '1' else '❌ غیرفعال'}\n"
            text += f"💾 ذخیره مدیا: {'✅ فعال' if settings.get('auto_save_media') == '1' else '❌ غیرفعال'}\n"
            text += f"⏰ ساعت نام: {'✅ فعال' if settings.get('clock_name_active') == '1' else '❌ غیرفعال'}\n"
            text += f"⏰ ساعت بیو: {'✅ فعال' if settings.get('clock_bio_active') == '1' else '❌ غیرفعال'}"
            
            _bot.reply_to(message, text, reply_markup=automation_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_automation_menu: {e}")

    # ─── دکمه‌های اتوماسیون ────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text.startswith("👁️ سین خودکار"))
    def cmd_toggle_auto_seen(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "auto_seen_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_seen_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:auto_seen_active", None)
            
            _bot.reply_to(message, 
                f"👁️ سین خودکار {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=automation_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_auto_seen: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("❤️ ری‌اکشن"))
    def cmd_toggle_auto_reaction(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "auto_reaction_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_reaction_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:auto_reaction_active", None)
            
            _bot.reply_to(message, 
                f"❤️ ری‌اکشن خودکار {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=automation_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_auto_reaction: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("💾 ذخیره مدیا"))
    def cmd_toggle_auto_save(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "auto_save_media", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "auto_save_media", new_status)
            _user_settings_cache.pop(f"{account['id']}:auto_save_media", None)
            
            _bot.reply_to(message, 
                f"💾 ذخیره مدیا {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=automation_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_auto_save: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("⏰ ساعت نام/بیو"))
    def cmd_clock_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            settings = get_user_settings(account["id"])
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(f"ساعت نام {'روشن' if settings.get('clock_name_active') == '1' else 'خاموش'}")
            markup.add(f"ساعت بیو {'روشن' if settings.get('clock_bio_active') == '1' else 'خاموش'}")
            markup.add("🔙 بازگشت")
            
            _bot.reply_to(message,
                f"⏰ <b>تنظیمات ساعت</b>\n\n"
                f"ساعت نام: {'🟢 فعال' if settings.get('clock_name_active') == '1' else '🔴 غیرفعال'}\n"
                f"ساعت بیو: {'🟢 فعال' if settings.get('clock_bio_active') == '1' else '🔴 غیرفعال'}\n"
                f"🔤 فونت فعلی: {settings.get('selected_font', '0')}",
                reply_markup=markup)
        except Exception as e:
            print(f"❌ خطا در cmd_clock_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("ساعت نام "))
    def cmd_toggle_clock_name(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "clock_name_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "clock_name_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:clock_name_active", None)
            
            _bot.reply_to(message, 
                f"⏰ ساعت نام {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_clock_name: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("ساعت بیو "))
    def cmd_toggle_clock_bio(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            status = get_cached_setting(account["id"], "clock_bio_active", "0")
            new_status = "0" if status == "1" else "1"
            db.set_setting(account["id"], "clock_bio_active", new_status)
            _user_settings_cache.pop(f"{account['id']}:clock_bio_active", None)
            
            _bot.reply_to(message, 
                f"⏰ ساعت بیو {'روشن' if new_status == '1' else 'خاموش'} شد!",
                reply_markup=settings_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_toggle_clock_bio: {e}")

    # ─── فونت ────────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔤 فونت")
    def cmd_font_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            current_font = get_cached_setting(account["id"], "selected_font", "0")
            
            text = f"🔤 <b>انتخاب فونت</b>\n\n"
            text += f"فونت فعلی: <b>{current_font}</b>\n\n"
            text += "💡 برای تغییر، روی یک دکمه کلیک کنید."
            
            _bot.reply_to(message, text, reply_markup=font_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_font_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("فونت ") and len(m.text) <= 7)
    def cmd_set_font(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            font_id = message.text.split()[-1]
            if font_id in ["0", "1", "2", "3", "4", "5", "6", "7", "8"]:
                db.set_setting(account["id"], "selected_font", font_id)
                _user_settings_cache.pop(f"{account['id']}:selected_font", None)
                _bot.reply_to(message, f"✅ فونت {font_id} انتخاب شد!", reply_markup=font_keyboard())
            else:
                _bot.reply_to(message, "❌ شماره فونت باید بین ۰ تا ۸ باشد.", reply_markup=font_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_set_font: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📝 لیست فونت")
    def cmd_list_fonts(message):
        try:
            if not require_membership(message): return
            
            test_text = "امیر"
            samples = {
                "0": "متن عادی",
                "1": "𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀", 
                "2": "𝘐𝘵𝘢𝘭𝘪𝘤 𝘚𝘢𝘯𝘴",
                "3": "𝙼𝚘𝚗𝚘𝚜𝚙𝚊𝚌𝚎",
                "4": "Ｆｕｌｌｗｉｄｔｈ",
                "5": "𝐒𝐞𝐫𝐢𝐟 𝐁𝐨𝐥𝐝",
                "6": "𝒮𝒸𝓇𝒾𝓅𝓉",
                "7": "S̶t̶r̶i̶k̶e̶t̶h̶r̶o̶u̶g̶h̶",
                "8": "U̲n̲d̲e̲r̲l̲i̲n̲e̲"
            }
            
            from bot import FONTS
            
            lines = ["📝 <b>لیست فونت‌ها با نمونه:</b>\n"]
            lines.append("─" * 35)
            
            for k, v in samples.items():
                fn = FONTS.get(k, FONTS["0"])
                converted = fn(test_text)
                lines.append(f"<b>فونت {k}</b> — {v}:")
                lines.append(f"  <code>{converted}</code>")
                lines.append("")
            
            lines.append("─" * 35)
            lines.append("\n💡 استفاده: <code>فونت [شماره]</code>")
            lines.append("مثال: <code>فونت 3</code>")
            
            _bot.reply_to(message, "\n".join(lines), reply_markup=font_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_list_fonts: {e}")

    # ─── لیست‌ها ─────────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "📋 لیست‌ها")
    def cmd_lists_menu(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            enemy_count = len(db.get_enemies(account["id"]))
            friend_count = len(db.get_friends(account["id"]))
            
            text = f"📋 <b>مدیریت لیست‌ها</b>\n\n"
            text += f"👥 دشمن: <b>{enemy_count}</b> نفر\n"
            text += f"💚 دوست: <b>{friend_count}</b> نفر"
            
            _bot.reply_to(message, text, reply_markup=lists_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_lists_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "👤 مدیریت دشمن")
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
            
            _bot.reply_to(message, text, reply_markup=enemy_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_enemy_menu: {e}")

    @_bot.message_handler(func=lambda m: m.text == "💚 مدیریت دوست")
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
            
            _bot.reply_to(message, text, reply_markup=friend_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_friend_menu: {e}")

    # ─── دکمه‌های دشمن ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "➕ افزودن دشمن")
    def cmd_add_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_add_enemy, account["id"])
        except Exception as e:
            print(f"❌ خطا در cmd_add_enemy_prompt: {e}")

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
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دشمن اضافه شد!", 
                            reply_markup=enemy_keyboard())
                return
            
            try:
                user_id = int(message.text.strip())
                db.add_enemy(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دشمن اضافه شد!", 
                            reply_markup=enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", 
                            reply_markup=enemy_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_add_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "❌ حذف دشمن")
    def cmd_remove_enemy_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_remove_enemy, account["id"])
        except Exception as e:
            print(f"❌ خطا در cmd_remove_enemy_prompt: {e}")

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
                    _bot.reply_to(message, f"✅ کاربر از لیست دشمن حذف شد!", 
                                reply_markup=enemy_keyboard())
                else:
                    _bot.reply_to(message, "❌ کاربر در لیست دشمن نبود!", 
                                reply_markup=enemy_keyboard())
                return
            
            try:
                user_id = int(message.text.strip())
                if db.remove_enemy(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دشمن حذف شد!", 
                                reply_markup=enemy_keyboard())
                else:
                    _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دشمن نبود!", 
                                reply_markup=enemy_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", 
                            reply_markup=enemy_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_remove_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "📋 نمایش دشمن‌ها")
    def cmd_show_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
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
        except Exception as e:
            print(f"❌ خطا در cmd_show_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🗑️ پاک کردن دشمن‌ها")
    def cmd_clear_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            db.clear_enemies(account["id"])
            _bot.reply_to(message, "🗑️ لیست دشمن پاک شد!", reply_markup=enemy_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_clear_enemies: {e}")

    # ─── دکمه‌های دوست ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "➕ افزودن دوست")
    def cmd_add_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_add_friend, account["id"])
        except Exception as e:
            print(f"❌ خطا در cmd_add_friend_prompt: {e}")

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
                _bot.reply_to(message, f"✅ {name or username or user_id} به لیست دوست اضافه شد!", 
                            reply_markup=friend_keyboard())
                return
            
            try:
                user_id = int(message.text.strip())
                db.add_friend(owner_id, user_id, None, str(user_id))
                _bot.reply_to(message, f"✅ کاربر {user_id} به لیست دوست اضافه شد!", 
                            reply_markup=friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", 
                            reply_markup=friend_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_add_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "❌ حذف دوست")
    def cmd_remove_friend_prompt(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>آیدی عددی کاربر مورد نظر را وارد کنید:</b>\n\n"
                "💡 می‌توانید روی پیام کاربر ریپلای کنید و سپس این دستور را بزنید.",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_remove_friend, account["id"])
        except Exception as e:
            print(f"❌ خطا در cmd_remove_friend_prompt: {e}")

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
                    _bot.reply_to(message, f"✅ کاربر از لیست دوست حذف شد!", 
                                reply_markup=friend_keyboard())
                else:
                    _bot.reply_to(message, "❌ کاربر در لیست دوست نبود!", 
                                reply_markup=friend_keyboard())
                return
            
            try:
                user_id = int(message.text.strip())
                if db.remove_friend(owner_id, user_id):
                    _bot.reply_to(message, f"✅ کاربر {user_id} از لیست دوست حذف شد!", 
                                reply_markup=friend_keyboard())
                else:
                    _bot.reply_to(message, f"❌ کاربر {user_id} در لیست دوست نبود!", 
                                reply_markup=friend_keyboard())
            except ValueError:
                _bot.reply_to(message, "❌ لطفاً یک آیدی عددی معتبر وارد کنید یا روی پیام ریپلای کنید.", 
                            reply_markup=friend_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_remove_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.text == "📋 نمایش دوست‌ها")
    def cmd_show_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
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
        except Exception as e:
            print(f"❌ خطا در cmd_show_friends: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🗑️ پاک کردن دوست‌ها")
    def cmd_clear_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            db.clear_friends(account["id"])
            _bot.reply_to(message, "🗑️ لیست دوست پاک شد!", reply_markup=friend_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_clear_friends: {e}")

    # ─── دکمه بازگشت ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔙 بازگشت")
    def cmd_back(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            is_owner = (message.from_user.id == config.OWNER_TG_ID)
            _bot.reply_to(message, "🔙 بازگشت به منوی اصلی.", 
                         reply_markup=owner_keyboard() if is_owner else user_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_back: {e}")

    # ─── دستورات مالک ──────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "📢 مدیریت چنل‌ها")
    def cmd_admin_channels(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            # ✅ از دیتابیس موقت (db_cache) برای چنل‌ها استفاده میکنه
            channels = cache.get_forced_channels()
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add("➕ افزودن چنل", "❌ حذف چنل")
            markup.add("📋 نمایش چنل‌ها", "🔙 بازگشت")
            
            text = "📢 <b>مدیریت چنل‌های اجباری</b>\n\n"
            if channels:
                text += "چنل‌های فعلی:\n" + "\n".join([f"🔸 {ch}" for ch in channels])
            else:
                text += "📭 لیست چنل‌ها خالی است."
            
            _bot.reply_to(message, text, reply_markup=markup)
        except Exception as e:
            print(f"❌ خطا در cmd_admin_channels: {e}")

    @_bot.message_handler(func=lambda m: m.text == "➕ افزودن چنل")
    def cmd_add_channel_prompt(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>یوزرنیم کانال را وارد کنید:</b>\n\n"
                "مثال: <code>@ChannelID</code>",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_add_channel)
        except Exception as e:
            print(f"❌ خطا در cmd_add_channel_prompt: {e}")

    def process_add_channel(message):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت.", reply_markup=owner_keyboard())
                return
            
            username = message.text.strip()
            # ✅ از دیتابیس موقت (db_cache) استفاده میکنه
            if cache.add_forced_channel(username):
                _bot.reply_to(message, f"✅ چنل {username} اضافه شد!", 
                            reply_markup=owner_keyboard())
            else:
                _bot.reply_to(message, "❌ خطا یا چنل تکراری است.", 
                            reply_markup=owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در process_add_channel: {e}")

    @_bot.message_handler(func=lambda m: m.text == "❌ حذف چنل")
    def cmd_remove_channel_prompt(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            # ✅ از دیتابیس موقت (db_cache) استفاده میکنه
            channels = cache.get_forced_channels()
            if not channels:
                _bot.reply_to(message, "📭 لیست چنل‌ها خالی است.", reply_markup=owner_keyboard())
                return
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for ch in channels:
                markup.add(f"🗑️ {ch}")
            markup.add("🔙 بازگشت")
            
            _bot.reply_to(message, 
                "✏️ <b>روی چنل مورد نظر برای حذف کلیک کنید:</b>",
                reply_markup=markup)
        except Exception as e:
            print(f"❌ خطا در cmd_remove_channel_prompt: {e}")

    @_bot.message_handler(func=lambda m: m.text.startswith("🗑️ @"))
    def cmd_remove_channel(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            username = message.text.replace("🗑️ ", "")
            # ✅ از دیتابیس موقت (db_cache) استفاده میکنه
            if cache.remove_forced_channel(username):
                _bot.reply_to(message, f"✅ چنل {username} حذف شد!", 
                            reply_markup=owner_keyboard())
            else:
                _bot.reply_to(message, f"❌ چنل {username} یافت نشد.", 
                            reply_markup=owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_remove_channel: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📋 نمایش چنل‌ها")
    def cmd_show_channels(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            # ✅ از دیتابیس موقت (db_cache) استفاده میکنه
            channels = cache.get_forced_channels()
            if not channels:
                _bot.reply_to(message, "📭 لیست چنل‌ها خالی است.", 
                            reply_markup=owner_keyboard())
                return
            
            text = "📋 <b>چنل‌های اجباری فعلی:</b>\n\n"
            for ch in channels:
                text += f"🔸 {ch}\n"
            
            _bot.reply_to(message, text, reply_markup=owner_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_show_channels: {e}")

    @_bot.message_handler(func=lambda m: m.text == "👥 مدیریت کاربران")
    def cmd_admin_users(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            # ✅ از دیتابیس دائمی (Supabase) استفاده میکنه
            users = db.get_all_accounts()
            
            text = f"👥 <b>مدیریت کاربران</b>\n\n"
            text += f"تعداد کل کاربران: <b>{len(users)}</b> نفر\n\n"
            
            if users:
                for i, u in enumerate(users[:10], 1):
                    bal = db.get_token_balance(u['id'])
                    text += f"{i}. {u['username']} — 🪙{bal}\n"
                if len(users) > 10:
                    text += f"\nو {len(users) - 10} نفر دیگر..."
            
            _bot.reply_to(message, text, reply_markup=owner_users_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_admin_users: {e}")

    @_bot.message_handler(func=lambda m: m.text == "📋 لیست کاربران")
    def cmd_list_users(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            # ✅ از دیتابیس دائمی (Supabase) استفاده میکنه
            users = db.get_all_accounts()
            if not users:
                _bot.reply_to(message, "📭 هیچ کاربری ثبت نشده.", 
                            reply_markup=owner_users_keyboard())
                return
            
            lines = [f"👥 <b>لیست کاربران ({len(users)} نفر):</b>\n"]
            for u in users:
                bal = db.get_token_balance(u['id'])
                tg_id = db.get_telegram_id_by_owner(u['id'])
                status = "✅" if tg_id else "❌"
                lines.append(f"{status} {u['username']} — 🪙{bal}")
            
            _bot.reply_to(message, "\n".join(lines), reply_markup=owner_users_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_list_users: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🎁 هدیه به کاربر")
    def cmd_give_prompt(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID: return
            
            msg = _bot.reply_to(message, 
                "✏️ <b>فرمت:</b>\n"
                "<code>/give username amount</code>\n\n"
                "مثال: <code>/give amir 10</code>",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 بازگشت"))
            
            _bot.register_next_step_handler(msg, process_give_tokens)
        except Exception as e:
            print(f"❌ خطا در cmd_give_prompt: {e}")

    def process_give_tokens(message):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت.", reply_markup=owner_keyboard())
                return
            
            parts = message.text.strip().split()
            if len(parts) < 3:
                _bot.reply_to(message, "❌ فرمت: /give username amount", 
                            reply_markup=owner_users_keyboard())
                return
            
            target = parts[1].lstrip("@")
            try:
                amount = int(parts[2])
            except:
                _bot.reply_to(message, "❌ مقدار باید عدد باشد.", 
                            reply_markup=owner_users_keyboard())
                return
            
            # ✅ از دیتابیس دائمی (Supabase) استفاده میکنه
            account = db.get_account_by_username(target)
            if not account:
                _bot.reply_to(message, f"❌ کاربر '{target}' یافت نشد.", 
                            reply_markup=owner_users_keyboard())
                return
            
            db.add_tokens(account["id"], amount)
            new_balance = db.get_token_balance(account["id"])
            
            _bot.reply_to(message, 
                f"✅ <b>{amount}</b> توکن به <b>{account['username']}</b> داده شد.\n"
                f"💰 موجودی جدید: <b>{new_balance}</b>",
                reply_markup=owner_users_keyboard())
            
            tg_id = db.get_telegram_id_by_owner(account["id"])
            if tg_id:
                try:
                    _bot.send_message(tg_id, 
                        f"🎁 <b>{amount}</b> توکن از طرف مالک دریافت کردید!\n"
                        f"💰 موجودی جدید: <b>{new_balance}</b> توکن")
                except:
                    pass
        except Exception as e:
            print(f"❌ خطا در process_give_tokens: {e}")

    # ─── پیام‌های ناشناخته ──────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: True)
    def cmd_unknown(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            if not require_membership(message):
                return

            is_owner = (message.from_user.id == config.OWNER_TG_ID)
            _bot.reply_to(message, "📱 لطفاً از دکمه‌های زیر استفاده کنید:", 
                         reply_markup=owner_keyboard() if is_owner else user_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_unknown: {e}")

    # ─── حلقه Polling ──────────────────────────────────────────────────────
    def _polling_loop():
        import time as _t
        while True:
            try:
                _bot.infinity_polling(timeout=30, long_polling_timeout=25, 
                                      restart_on_change=False, skip_pending=True)
            except Exception as e:
                if "409" in str(e):
                    _t.sleep(10)
                    try:
                        _bot.delete_webhook(drop_pending_updates=True)
                    except:
                        pass
                else:
                    _t.sleep(5)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    print(f"✅ ربات مدیریت @{BOT_USERNAME} استارت شد.")
