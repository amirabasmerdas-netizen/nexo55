# telegram_bot.py
import threading
import telebot
from telebot import types
import database as db
import db_cache as cache
import config
import datetime
import time
import random
import logging
import re

# ─── تنظیم لاگ ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = config.OWNER_TG_ID

# ─── کش کاربران ──────────────────────────────────────────────────────────────
_user_cache = {}
_user_cache_time = {}
_CACHE_TTL = 5

def get_user_account(tg_id: int):
    now = datetime.datetime.now().timestamp()
    
    if tg_id in _user_cache:
        if now - _user_cache_time.get(tg_id, 0) < _CACHE_TTL:
            return _user_cache[tg_id]
    
    account = db.get_account_by_tg_id(tg_id)
    _user_cache[tg_id] = account
    _user_cache_time[tg_id] = now
    logger.info(f"🔍 جستجوی کاربر {tg_id} در دیتابیس: {account}")
    return account

def clear_user_cache(tg_id: int = None):
    if tg_id:
        _user_cache.pop(tg_id, None)
        _user_cache_time.pop(tg_id, None)
    else:
        _user_cache.clear()
        _user_cache_time.clear()
    logger.info("✅ کش کاربران پاک شد")

# ─── کش تنظیمات ──────────────────────────────────────────────────────────────
_user_settings_cache = {}
_cache_timestamps = {}

def get_cached_setting(owner_id: int, key: str, default=None):
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
    _user_settings_cache.clear()
    _cache_timestamps.clear()
    logger.info("✅ کش تنظیمات پاک شد")

# ─── متغیرهای چالش ──────────────────────────────────────────────────────────
_waiting_for_bet_amount = {}  # {user_id: (bet_id, selected_team)}
_waiting_for_worldcup = {}    # مرحله ایجاد چالش جام جهانی

def get_bot():
    return _bot

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

    _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=False)

    try:
        me = _bot.get_me()
        BOT_USERNAME = me.username
        logger.info(f"🤖 ربات مدیریت: @{BOT_USERNAME}")
    except Exception as e:
        logger.error(f"❌ خطا در اتصال ربات مدیریت: {e}")
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
        return db.get_token_stats(owner_id)

    def get_user_settings(owner_id: int):
        keys = [
            "self_bot_active", "secretary_active", "anti_delete_active",
            "anti_link_active", "auto_seen_active", "auto_reaction_active",
            "private_lock_active", "enemy_reply_active", "auto_save_media",
            "clock_name_active", "clock_bio_active", "selected_font",
        ]
        return {k: get_cached_setting(owner_id, k, "0") for k in keys}

    # ─── بررسی عضویت در چنل‌های اجباری ──────────────────────────────────────
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
        _bot.reply_to(
            message,
            "⛔️ <b>ورود به ربات منوط به عضویت در کانال‌های زیر است:</b>\n\n"
            f"{channels_list}\n\n"
            "👇 روی هر کانال کلیک کنید و Join بزنید، سپس دکمه «بررسی عضویت من» را بزنید:",
            reply_markup=markup
        )

    # ─── ساخت کیبوردها (فقط برای پیوی) ──────────────────────────────────────
    def user_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("📖 راهنما", "👤 پروفایل من")
        markup.add("🔄 به‌روزرسانی منو")
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

    def owner_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("💰 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        markup.add("⚙️ تنظیمات سلف", "📊 وضعیت سلف")
        markup.add("🎯 چالش‌ها", "📢 پیام عمومی")
        markup.add("🏆 اعلام برنده", "👤 پروفایل من")
        markup.add("📖 راهنما", "🔄 به‌روزرسانی منو")
        return markup

    def challenges_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("🧮 چالش ریاضی", "⚽ پیش‌بینی جام جهانی")
        markup.add("🔙 بازگشت")
        return markup

    def owner_users_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("📋 لیست کاربران", "🎁 هدیه به کاربر")
        markup.add("🔙 بازگشت")
        return markup

    # ─── /start ─────────────────────────────────────────────────────────────
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
                            try:
                                _bot.send_message(referrer_tg, 
                                    f"🎉 یک نفر با لینک شما عضو شد!\n"
                                    f"<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
                            except:
                                pass
                except:
                    pass

            if not require_membership(message):
                return

            account = get_user_account(tg_id)
            logger.info(f"👤 حساب کاربری پیدا شده: {account}")
            site_url = getattr(config, "SITE_URL", "")

            if not account:
                logger.warning(f"❌ کاربر {tg_id} در دیتابیس دائمی پیدا نشد")
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

            logged_in = db.get_setting(account["id"], "logged_in") == "1"
            logger.info(f"📊 وضعیت لاگین کاربر {tg_id}: {logged_in}")
            
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

            stats = get_user_stats(account["id"])
            settings = get_user_settings(account["id"])
            
            is_owner = (tg_id == config.OWNER_TG_ID)
            
            markup = owner_keyboard() if is_owner else user_keyboard()
            token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
            
            bot_status = "🟢 فعال" if settings.get("self_bot_active") == "1" else "🔴 غیرفعال"
            
            _bot.reply_to(
                message,
                f"👋 خوش برگشتی <b>{account['username']}</b>!\n\n"
                f"📊 <b>وضعیت سلف:</b> {bot_status}\n"
                f"💎 <b>موجودی الماس:</b> {stats['balance']}\n"
                f"📈 <b>کل دریافتی:</b> {stats['total_earned']}\n\n"
                f"⚡ هر <b>۲ الماس</b> = <b>۲ ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=markup
            )

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
                _bot.reply_to(message, f"⚠️ خطا رخ داد: {str(e)}\n\nلطفاً دوباره /start بزنید.")
            except:
                pass

    # ─── دکمه بررسی عضویت ──────────────────────────────────────────────────
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

    # ─── دکمه به‌روزرسانی منو ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text == "🔄 به‌روزرسانی منو")
    def cmd_refresh_menu(message):
        try:
            tg_id = message.from_user.id
            
            if not require_membership(message):
                return
            
            clear_user_cache(tg_id)
            
            account = get_user_account(tg_id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", 
                                   reply_markup=types.ReplyKeyboardRemove())
            
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
                f"💎 موجودی: {stats['balance']}\n"
                f"📈 کل دریافتی: {stats['total_earned']}",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_refresh_menu: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}")

    # ─── دکمه‌های اصلی (فقط پیوی) ────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "💰 موجودی")
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
                f"💎 <b>موجودی الماس</b>\n\n"
                f"💰 فعلی: <b>{stats['balance']}</b>\n"
                f"📊 کل: <b>{stats['total_earned']}</b>\n"
                f"👥 رفرال: <b>{ref_count}</b> نفر\n"
                f"💵 قیمت هر الماس: <b>{token_price} تومان</b>\n\n"
                f"🎁 هدیه روزانه: {config.DAILY_TOKEN_GIFT} الماس",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_balance: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🎁 هدیه روزانه")
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
                _bot.reply_to(message, f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>", 
                            reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
            else:
                _bot.reply_to(message, msg, 
                            reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_daily: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔗 رفرال")
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
                f"🎁 پاداش: <b>{config.REFERRAL_TOKENS} الماس</b> (معادل {referral_value} تومان)",
                reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_referral: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🛒 خرید الماس")
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
                f"🛒 <b>خرید الماس</b>\n\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>\n"
                f"👤 یوزرنیم پنل شما: <b>{username_txt}</b>\n\n"
                f"برای خرید، روی دکمه «خرید از مالک» کلیک کنید و یوزرنیم پنل خود را ارسال نمایید.",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"❌ خطا در cmd_buy: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "👤 پروفایل من")
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
            text += f"💎 موجودی: <b>{stats['balance']}</b>\n"
            text += f"📊 کل دریافتی: <b>{stats['total_earned']}</b>\n"
            text += f"👥 رفرال: <b>{db.get_referral_count(account['id'])}</b>\n"
            text += f"🔤 فونت فعلی: <b>{settings.get('selected_font', '0')}</b>\n"
            text += f"\n📅 تاریخ ثبت: {account.get('created_at', 'نامشخص')}"
            
            _bot.reply_to(message, text, 
                         reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_profile: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📊 وضعیت سلف")
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
            logger.error(f"❌ خطا در cmd_status: {e}")

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
            
            _bot.reply_to(message, help_text,
                         reply_markup=user_keyboard() if message.from_user.id != config.OWNER_TG_ID else owner_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_help: {e}")

    # ─── تنظیمات سلف (فقط پیوی) ────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚙️ تنظیمات سلف")
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
            logger.error(f"❌ خطا در cmd_settings: {e}")

    # ─── دکمه‌های تنظیمات (فقط پیوی) ──────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🟢 سلف روشن")
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
                    f"❌ الماس کافی ندارید!\n💎 موجودی: {balance}\n⚡ نیاز: {config.TOKENS_PER_SESSION} الماس",
                    reply_markup=settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_start_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔴 سلف خاموش")
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
            logger.error(f"❌ خطا در cmd_stop_bot: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=settings_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🤖 منشی")
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
            logger.error(f"❌ خطا در cmd_secretary_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("منشی "))
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
            logger.error(f"❌ خطا در cmd_toggle_secretary: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "✏️ تغییر پیام منشی")
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
            logger.error(f"❌ خطا در cmd_change_secretary_msg: {e}")

    def process_secretary_msg(message, owner_id):
        try:
            if message.text == "🔙 بازگشت":
                _bot.reply_to(message, "🔙 بازگشت به تنظیمات.", reply_markup=settings_keyboard())
                return
            
            db.set_setting(owner_id, "secretary_message", message.text)
            _user_settings_cache.pop(f"{owner_id}:secretary_message", None)
            _bot.reply_to(message, "✅ پیام منشی ذخیره شد!", reply_markup=settings_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در process_secretary_msg: {e}")

    # ─── امنیت (فقط پیوی) ──────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🛡️ امنیت")
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
            logger.error(f"❌ خطا در cmd_security_menu: {e}")

    # ─── دکمه‌های امنیت (فقط پیوی) ────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🛡️ ضد حذف"))
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
            logger.error(f"❌ خطا در cmd_toggle_anti_delete: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🔗 ضد لینک"))
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
            logger.error(f"❌ خطا در cmd_toggle_anti_link: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("🔒 قفل پیوی"))
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
            logger.error(f"❌ خطا در cmd_toggle_private_lock: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("⚔️ پاسخ دشمن"))
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
            logger.error(f"❌ خطا در cmd_toggle_enemy_reply: {e}")

    # ─── اتوماسیون (فقط پیوی) ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚡ اتوماسیون")
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
            logger.error(f"❌ خطا در cmd_automation_menu: {e}")

    # ─── دکمه‌های اتوماسیون (فقط پیوی) ────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("👁️ سین خودکار"))
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
            logger.error(f"❌ خطا در cmd_toggle_auto_seen: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("❤️ ری‌اکشن"))
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
            logger.error(f"❌ خطا در cmd_toggle_auto_reaction: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("💾 ذخیره مدیا"))
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
            logger.error(f"❌ خطا در cmd_toggle_auto_save: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("⏰ ساعت نام/بیو"))
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
            logger.error(f"❌ خطا در cmd_clock_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("ساعت نام "))
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
            logger.error(f"❌ خطا در cmd_toggle_clock_name: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("ساعت بیو "))
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
            logger.error(f"❌ خطا در cmd_toggle_clock_bio: {e}")

    # ─── فونت (فقط پیوی) ────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔤 فونت")
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
            logger.error(f"❌ خطا در cmd_font_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text.startswith("فونت ") and len(m.text) <= 7)
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
            logger.error(f"❌ خطا در cmd_set_font: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📝 لیست فونت")
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
            logger.error(f"❌ خطا در cmd_list_fonts: {e}")

    # ─── لیست‌ها (فقط پیوی) ─────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 لیست‌ها")
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
            logger.error(f"❌ خطا در cmd_lists_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "👤 مدیریت دشمن")
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
            logger.error(f"❌ خطا در cmd_enemy_menu: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "💚 مدیریت دوست")
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
            logger.error(f"❌ خطا در cmd_friend_menu: {e}")

    # ─── دکمه‌های دشمن (فقط پیوی) ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "➕ افزودن دشمن")
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
            logger.error(f"❌ خطا در cmd_add_enemy_prompt: {e}")

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
            logger.error(f"❌ خطا در process_add_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "❌ حذف دشمن")
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
            logger.error(f"❌ خطا در cmd_remove_enemy_prompt: {e}")

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
            logger.error(f"❌ خطا در process_remove_enemy: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=enemy_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 نمایش دشمن‌ها")
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
            logger.error(f"❌ خطا در cmd_show_enemies: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🗑️ پاک کردن دشمن‌ها")
    def cmd_clear_enemies(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            db.clear_enemies(account["id"])
            _bot.reply_to(message, "🗑️ لیست دشمن پاک شد!", reply_markup=enemy_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_clear_enemies: {e}")

    # ─── دکمه‌های دوست (فقط پیوی) ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "➕ افزودن دوست")
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
            logger.error(f"❌ خطا در cmd_add_friend_prompt: {e}")

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
            logger.error(f"❌ خطا در process_add_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "❌ حذف دوست")
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
            logger.error(f"❌ خطا در cmd_remove_friend_prompt: {e}")

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
            logger.error(f"❌ خطا در process_remove_friend: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=friend_keyboard())

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📋 نمایش دوست‌ها")
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
            logger.error(f"❌ خطا در cmd_show_friends: {e}")

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🗑️ پاک کردن دوست‌ها")
    def cmd_clear_friends(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return
            
            db.clear_friends(account["id"])
            _bot.reply_to(message, "🗑️ لیست دوست پاک شد!", reply_markup=friend_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_clear_friends: {e}")

    # ─── دکمه بازگشت (فقط پیوی) ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🔙 بازگشت")
    def cmd_back(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.")
            
            is_owner = (message.from_user.id == config.OWNER_TG_ID)
            _bot.reply_to(message, "🔙 بازگشت به منوی اصلی.", 
                         reply_markup=owner_keyboard() if is_owner else user_keyboard())
        except Exception as e:
            logger.error(f"❌ خطا در cmd_back: {e}")

    # ─── ════════════════════════════════════════════════════════════════ ───
    # ─── 🎯 چالش‌ها (فقط مالک در پیوی) ───────────────────────────────── ───
    # ─── ════════════════════════════════════════════════════════════════ ───

    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🎯 چالش‌ها")
    def cmd_challenges(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                _bot.reply_to(message, "⛔ این بخش فقط برای مالک است.")
                return
            
            _bot.reply_to(
                message,
                "🎯 **پنل مدیریت چالش‌ها**\n\n"
                "🧮 **چالش ریاضی**: هر ۲ ساعت یکبار در گروه\n"
                "⚽ **پیش‌بینی جام جهانی**: شرط‌بندی روی مسابقات",
                reply_markup=challenges_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ cmd_challenges error: {e}")

    # ─── چالش ریاضی ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🧮 چالش ریاضی")
    def cmd_math_challenge(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                return
            
            settings = db.get_challenge_settings(1)
            current = settings.get('math_challenge_active', False)
            
            db.update_challenge_settings(1, 'math_challenge_active', not current)
            
            status = "🟢 فعال" if not current else "🔴 غیرفعال"
            _bot.reply_to(
                message,
                f"🧮 **چالش ریاضی**\n\n"
                f"وضعیت: {status}\n"
                f"📅 هر ۲ ساعت یکبار در گروه ارسال می‌شود.\n"
                f"💰 جایزه: ۱ الماس به پاسخ‌دهنده صحیح",
                reply_markup=challenges_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ cmd_math_challenge error: {e}")

    # ─── پیش‌بینی جام جهانی ──────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "⚽ پیش‌بینی جام جهانی")
    def cmd_worldcup(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                return
            
            _waiting_for_worldcup[message.chat.id] = {'step': 'team1'}
            
            msg = _bot.reply_to(
                message,
                "⚽ **ایجاد چالش جدید جام جهانی**\n\n"
                "📝 نام تیم اول را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_team1)
        except Exception as e:
            logger.error(f"❌ cmd_worldcup error: {e}")

    def process_worldcup_team1(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            
            team1 = message.text.strip()
            _waiting_for_worldcup[message.chat.id] = {'step': 'team2', 'team1': team1}
            
            msg = _bot.reply_to(
                message,
                f"⚽ تیم اول: **{team1}**\n\n"
                "📝 نام تیم دوم را وارد کنید:",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_team2, team1)
        except Exception as e:
            logger.error(f"❌ process_worldcup_team1 error: {e}")

    def process_worldcup_team2(message, team1):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            
            team2 = message.text.strip()
            _waiting_for_worldcup[message.chat.id] = {'step': 'time', 'team1': team1, 'team2': team2}
            
            msg = _bot.reply_to(
                message,
                f"⚽ تیم اول: **{team1}**\n"
                f"⚽ تیم دوم: **{team2}**\n\n"
                "🕐 ساعت بازی را وارد کنید (مثال: 21:30):",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_worldcup_time, team1, team2)
        except Exception as e:
            logger.error(f"❌ process_worldcup_team2 error: {e}")

    def process_worldcup_time(message, team1, team2):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            
            match_time = message.text.strip()
            
            if not re.match(r'^\d{2}:\d{2}$', match_time):
                _bot.reply_to(
                    message, 
                    "❌ فرمت ساعت اشتباه است!\n"
                    "لطفاً ساعت را به فرمت <code>HH:MM</code> وارد کنید.\n"
                    "مثال: <code>21:30</code>",
                    reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
                )
                return
            
            iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
            today = datetime.datetime.now(iran_tz).date()
            full_datetime = f"{today.isoformat()} {match_time}:00"
            
            _waiting_for_worldcup[message.chat.id] = {'step': 'photo', 'team1': team1, 'team2': team2, 'time': full_datetime}
            
            msg = _bot.reply_to(
                message,
                f"⚽ **اطلاعات مسابقه**\n\n"
                f"تیم اول: **{team1}**\n"
                f"تیم دوم: **{team2}**\n"
                f"زمان: **{match_time}** (به وقت ایران)\n\n"
                "🖼️ لطفاً عکس یا لوگوی مسابقه را ارسال کنید:\n"
                "(می‌توانید یک عکس ارسال کنید یا روی «ردی» کلیک کنید)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("⏭ ردی")
            )
            _bot.register_next_step_handler(msg, process_worldcup_photo, team1, team2, full_datetime)
        except Exception as e:
            logger.error(f"❌ process_worldcup_time error: {e}")

    def process_worldcup_photo(message, team1, team2, match_time):
        try:
            photo_file_id = None
            
            if message.text == "⏭ ردی":
                logger.info("⏭ بدون عکس")
            elif message.photo:
                photo_file_id = message.photo[-1].file_id
                logger.info(f"📸 عکس دریافت شد: {photo_file_id}")
            else:
                _bot.reply_to(message, "❌ لطفاً یک عکس ارسال کنید یا روی «ردی» کلیک کنید.")
                return
            
            bet_id = db.create_worldcup_bet(1, team1, team2, match_time, photo_file_id)
            logger.info(f"✅ چالش با ID {bet_id} در دیتابیس ثبت شد")
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(f"⚽ {team1}", callback_data=f"bet_{team1}"),
                types.InlineKeyboardButton(f"⚽ {team2}", callback_data=f"bet_{team2}")
            )
            
            caption = (
                f"⚽ **مسابقه جام جهانی**\n\n"
                f"🆚 **{team1}** vs **{team2}**\n"
                f"🕐 زمان: {match_time}\n\n"
                f"💰 روی تیم مورد نظر شرط ببندید!\n"
                f"📊 هر کاربر می‌تواند شرط خود را ثبت کند.\n"
                f"🏆 برنده تمام الماس‌ها را دریافت می‌کند!"
            )
            
            target_chat = "@Gp_SelfNexo"
            
            try:
                logger.info(f"📢 ارسال به گروه {target_chat}")
                
                if photo_file_id:
                    sent = _bot.send_photo(
                        target_chat, 
                        photo_file_id,
                        caption=caption, 
                        reply_markup=markup
                    )
                else:
                    sent = _bot.send_message(target_chat, caption, reply_markup=markup)
                
                logger.info(f"✅ پیام با موفقیت ارسال شد. Message ID: {sent.message_id if sent else 'N/A'}")
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"❌ خطا در ارسال به گروه: {error_msg}")
                
                if "chat not found" in error_msg.lower() or "bot" in error_msg.lower():
                    _bot.reply_to(
                        message, 
                        f"❌ گروه @Gp_SelfNexo پیدا نشد!\n\n"
                        f"لطفاً مراحل زیر را انجام دهید:\n"
                        f"1️⃣ ربات @{BOT_USERNAME} را به گروه اضافه کنید\n"
                        f"2️⃣ به ربات اجازه ارسال پیام بدهید\n"
                        f"3️⃣ دوباره امتحان کنید\n\n"
                        f"خطا: {error_msg}", 
                        reply_markup=owner_keyboard()
                    )
                else:
                    _bot.reply_to(message, f"❌ خطا در ارسال: {error_msg}", reply_markup=owner_keyboard())
                return
            
            _waiting_for_worldcup.pop(message.chat.id, None)
            
            _bot.reply_to(
                message,
                f"✅ **چالش ایجاد شد!**\n\n"
                f"⚽ {team1} vs {team2}\n"
                f"🕐 {match_time}\n"
                f"📢 در گروه @Gp_SelfNexo ارسال شد.",
                reply_markup=owner_keyboard()
            )
        except Exception as e:
            logger.error(f"❌ process_worldcup_photo error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=owner_keyboard())

    # ─── پردازش دکمه‌های شرط‌بندی جام جهانی ──────────────────────────────────
    @_bot.callback_query_handler(func=lambda call: call.data.startswith('bet_'))
    def callback_bet(call):
        try:
            selected_team = call.data.split('_')[1]
            
            bet = db.get_active_worldcup_bet(1)
            if not bet:
                _bot.answer_callback_query(call.id, "❌ این چالش منقضی شده است.", show_alert=True)
                return
            
            tg_id = call.from_user.id
            
            account = db.get_account_by_tg_id(tg_id)
            if not account:
                _bot.answer_callback_query(call.id, "❌ ابتدا در پنل ثبت‌نام کنید.", show_alert=True)
                return
            
            _waiting_for_bet_amount[tg_id] = (bet['id'], selected_team)
            
            msg = _bot.send_message(
                call.message.chat.id,
                f"⚽ **شرط‌بندی**\n\n"
                f"تیم انتخاب شده: **{selected_team}**\n"
                f"💰 میزان الماس خود را وارد کنید:\n\n"
                f"📊 موجودی شما: {db.get_token_balance(account['id'])} الماس\n"
                f"💡 عدد ۰ برای لغو",
                reply_to_message_id=call.message.message_id
            )
            
            _bot.register_next_step_handler(msg, process_bet_amount, bet['id'], tg_id, selected_team)
            _bot.answer_callback_query(call.id, "✅ انتخاب ثبت شد!")
            
        except Exception as e:
            logger.error(f"❌ callback_bet error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    def process_bet_amount(message, bet_id, user_tg_id, selected_team):
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
                _bot.reply_to(
                    message,
                    f"❌ موجودی ناکافی!\n"
                    f"💎 موجودی شما: {balance} الماس\n"
                    f"📊 نیاز: {amount} الماس"
                )
                return
            
            db.deduct_tokens(account['id'], amount)
            db.place_bet(bet_id, user_tg_id, selected_team, amount)
            
            _bot.reply_to(
                message,
                f"✅ **شرط شما ثبت شد!**\n\n"
                f"⚽ تیم: **{selected_team}**\n"
                f"💎 میزان: **{amount}** الماس\n\n"
                f"🔄 پس از پایان بازی، برنده اعلام می‌شود."
            )
            
        except Exception as e:
            logger.error(f"❌ process_bet_amount error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}")

    # ─── اعلام برنده جام جهانی ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🏆 اعلام برنده")
    def cmd_announce_winner(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                return
            
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
                f"🏆 **اعلام برنده مسابقه**\n\n"
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
                    f"🏆 **نتیجه مسابقه**\n\n"
                    f"⚽ برنده: **{winner}**\n"
                    f"💎 کل الماس‌ها: **{total_tokens}**\n"
                    f"👥 تعداد برندگان: **{len(winners)}** نفر\n"
                    f"🎁 هر برنده: **{winner_amount}** الماس\n\n"
                    f"🎉 به برندگان تبریک می‌گوییم!"
                )
            except Exception as e:
                logger.error(f"❌ خطا در ارسال نتیجه به گروه: {e}")
            
            _bot.answer_callback_query(call.id, f"✅ برنده {winner} اعلام شد!")
            _bot.reply_to(call.message, f"✅ برنده **{winner}** اعلام شد!", reply_markup=owner_keyboard())
            
        except Exception as e:
            logger.error(f"❌ callback_winner error: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    # ─── پیام عمومی ──────────────────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "📢 پیام عمومی")
    def cmd_broadcast(message):
        try:
            if message.from_user.id != config.OWNER_TG_ID:
                return
            
            msg = _bot.reply_to(
                message,
                "📢 **ارسال پیام عمومی**\n\n"
                "✏️ متن پیام خود را وارد کنید:\n"
                "(از HTML برای فرمت‌دهی استفاده کنید)",
                reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("🔙 لغو")
            )
            _bot.register_next_step_handler(msg, process_broadcast)
        except Exception as e:
            logger.error(f"❌ cmd_broadcast error: {e}")

    def process_broadcast(message):
        try:
            if message.text == "🔙 لغو":
                _bot.reply_to(message, "❌ لغو شد.", reply_markup=owner_keyboard())
                return
            
            broadcast_text = message.text
            
            users = db.get_all_accounts()
            success_count = 0
            
            _bot.reply_to(
                message,
                f"⏳ در حال ارسال پیام به {len(users)} کاربر...",
                reply_markup=owner_keyboard()
            )
            
            for user in users:
                tg_id = db.get_telegram_id_by_owner(user['id'])
                if tg_id:
                    try:
                        _bot.send_message(tg_id, broadcast_text, parse_mode="HTML")
                        success_count += 1
                        time.sleep(0.05)
                    except Exception as e:
                        logger.error(f"❌ ارسال به {tg_id} ناموفق: {e}")
            
            _bot.send_message(
                message.chat.id,
                f"✅ **پیام عمومی ارسال شد!**\n\n"
                f"📨 ارسال به: {success_count} از {len(users)} کاربر",
                reply_markup=owner_keyboard()
            )
            
        except Exception as e:
            logger.error(f"❌ process_broadcast error: {e}")
            _bot.reply_to(message, f"❌ خطا: {str(e)}", reply_markup=owner_keyboard())

    # ─── ════════════════════════════════════════════════════════════════ ───
    # ─── 📌 دستورات گروه (فقط موجودی و شرط‌بندی) ────────────────────── ───
    # ─── ════════════════════════════════════════════════════════════════ ───

    # ─── پاسخ به دستور موجودی در گروه ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'] and m.text and m.text.strip() == "موجودی")
    def cmd_group_balance(message):
        try:
            account = get_user_account(message.from_user.id)
            if account:
                stats = db.get_token_stats(account["id"])
                _bot.reply_to(message, f"💎 موجودی شما: {stats['balance']} الماس")
            else:
                _bot.reply_to(message, "❌ شما در پنل ثبت‌نام نکرده‌اید!\nلطفاً در ربات @Nexo55bot ثبت‌نام کنید.")
        except Exception as e:
            logger.error(f"❌ خطا در cmd_group_balance: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ─── پاسخ به دستور شرط‌بندی در گروه ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'] and m.text and m.text.strip().startswith("شرط بندی"))
    def cmd_bet(message):
        try:
            text = message.text.strip()
            parts = text.split()
            
            if len(parts) < 3:
                _bot.reply_to(message, 
                    "❌ فرمت صحیح:\n"
                    "<code>شرط بندی [مقدار]</code>\n"
                    "مثال: <code>شرط بندی 100</code>")
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
            
            # کسر الماس از بازیکن اول
            db.deduct_tokens(account['id'], bet_amount)
            
            # ایجاد دکمه با شناسه یکتا
            import time
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("🎲 شرکت در قرعه‌کشی", callback_data=f"join_bet_{chat_id}_{int(time.time())}"))
            
            sent = _bot.reply_to(
                message,
                f"🎲 **شرط‌بندی جدید!**\n\n"
                f"👤 بازیکن اول: @{message.from_user.username or message.from_user.first_name}\n"
                f"💰 مبلغ شرط: <b>{bet_amount}</b> الماس\n\n"
                f"👇 روی دکمه زیر کلیک کنید تا در قرعه‌کشی شرکت کنید!\n"
                f"⏳ این بازی تا ۱ ساعت اعتبار دارد.",
                reply_markup=markup
            )
            
            db.create_bet_game(1, chat_id, player1_id, bet_amount, sent.message_id)
            
        except Exception as e:
            logger.error(f"❌ خطا در cmd_bet: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ─── پردازش دکمه شرکت در قرعه‌کشی شرط‌بندی ──────────────────────────────────
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
            
            import random
            winner_id = random.choice([game['player1_id'], player2_id])
            
            db.finish_bet_game(game['id'], winner_id)
            
            winner_account = db.get_account_by_tg_id(winner_id)
            winner_name = f"@{winner_account['username']}" if winner_account else str(winner_id)
            
            total_amount = bet_amount * 2
            db.add_tokens(winner_account['id'], total_amount)
            
            try:
                _bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=game['message_id'],
                    reply_markup=None
                )
            except:
                pass
            
            _bot.send_message(
                chat_id,
                f"🎉 **نتیجه قرعه‌کشی!**\n\n"
                f"👤 بازیکن اول: {player1_name}\n"
                f"👤 بازیکن دوم: {player2_name}\n"
                f"💰 مبلغ شرط: <b>{bet_amount}</b> الماس\n"
                f"🏆 کل جایزه: <b>{total_amount}</b> الماس\n\n"
                f"🎊 **برنده: {winner_name}**\n\n"
                f"💎 {total_amount} الماس به حساب برنده واریز شد!"
            )
            
            _bot.answer_callback_query(call.id, "✅ شما در قرعه‌کشی شرکت کردید!")
            
        except Exception as e:
            logger.error(f"❌ خطا در callback_join_bet: {e}")
            _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    # ─── پاک‌سازی خودکار شرط‌بندی‌های منقضی شده ──────────────────────────────────
    def clean_expired_bets():
        try:
            expired_games = db.get_expired_bet_games()
            for game in expired_games:
                db.expire_bet_game(game['id'])
                
                account = db.get_account_by_tg_id(game['player1_id'])
                if account:
                    db.add_tokens(account['id'], game['bet_amount'])
                
                try:
                    _bot.send_message(
                        game['chat_id'],
                        f"⏰ **بازی منقضی شد!**\n\n"
                        f"شرط‌بندی با مبلغ <b>{game['bet_amount']}</b> الماس\n"
                        f"پس از ۱ ساعت حریفی پیدا نشد.\n"
                        f"💎 الماس به بازیکن اول برگشت داده شد."
                    )
                    
                    try:
                        _bot.edit_message_reply_markup(
                            chat_id=game['chat_id'],
                            message_id=game['message_id'],
                            reply_markup=None
                        )
                    except:
                        pass
                        
                except Exception as e:
                    logger.error(f"❌ خطا در ارسال پیام انقضا: {e}")
                    
        except Exception as e:
            logger.error(f"❌ خطا در clean_expired_bets: {e}")

    # ─── تایمر پاک‌سازی خودکار ──────────────────────────────────────────────────
    def start_cleanup_timer():
        def cleanup_loop():
            while True:
                time.sleep(300)
                try:
                    clean_expired_bets()
                except Exception as e:
                    logger.error(f"❌ خطا در cleanup_loop: {e}")
        
        t = threading.Thread(target=cleanup_loop, daemon=True)
        t.start()
        logger.info("✅ تایمر پاک‌سازی شرط‌بندی‌ها شروع شد (هر ۵ دقیقه)")

    # ─── پاسخ به دستور انتقال الماس ──────────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.text and m.text.strip().startswith("انتقال"))
    def cmd_transfer(message):
        try:
            replied = message.reply_to_message
            if not replied:
                _bot.reply_to(message, 
                    "❌ لطفاً روی پیام کاربر مورد نظر ریپلای کنید.\n"
                    "فرمت: <code>انتقال [مقدار]</code>\n"
                    "مثال: <code>انتقال 100</code>")
                return
            
            parts = message.text.strip().split()
            if len(parts) < 2:
                _bot.reply_to(message, 
                    "❌ فرمت صحیح:\n"
                    "<code>انتقال [مقدار]</code>\n"
                    "مثال: <code>انتقال 100</code>")
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
                
                _bot.reply_to(
                    message,
                    f"✅ **انتقال الماس انجام شد!**\n\n"
                    f"👤 از: @{message.from_user.username or message.from_user.first_name}\n"
                    f"👤 به: @{replied.from_user.username or replied.from_user.first_name}\n"
                    f"💰 مقدار: <b>{amount}</b> الماس\n"
                    f"💎 موجودی شما: <b>{new_balance}</b> الماس"
                )
                
                try:
                    _bot.send_message(
                        to_tg_id,
                        f"🎁 **دریافت الماس!**\n\n"
                        f"👤 از: @{message.from_user.username or message.from_user.first_name}\n"
                        f"💰 مقدار: <b>{amount}</b> الماس\n"
                        f"💎 به حساب شما واریز شد!"
                    )
                except:
                    pass
            else:
                _bot.reply_to(message, "❌ خطا در انتقال الماس. لطفاً مجدداً تلاش کنید.")
                
        except Exception as e:
            logger.error(f"❌ خطا در cmd_transfer: {e}")
            _bot.reply_to(message, f"⚠️ خطا: {str(e)}")

    # ─── ❌ نادیده گرفتن همه پیام‌های گروه ──────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'])
    def ignore_group_messages(message):
        pass

    # ─── پیام‌های ناشناخته (فقط پیوی) ──────────────────────────────────────────
    @_bot.message_handler(func=lambda m: m.chat.type == 'private')
    def cmd_unknown_private(message):
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
            logger.error(f"❌ خطا در cmd_unknown_private: {e}")

    # ─── شروع تایمر پاک‌سازی ──────────────────────────────────────────────────
    start_cleanup_timer()

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
                    logger.error(f"❌ خطا در polling: {e}")
                    _t.sleep(5)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    logger.info(f"✅ ربات مدیریت @{BOT_USERNAME} استارت شد.")
