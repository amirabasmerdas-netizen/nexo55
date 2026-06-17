import threading
import time
import telebot
from telebot import types
import database as db
import config
import datetime
import random

_bot = None
BOT_USERNAME = None
OWNER_TG_ID = 8296865861

# ══════════════════════════════════════════════════════════════════════════════
# 🚀 سیستم Cache پیشرفته برای سرعت
# ══════════════════════════════════════════════════════════════════════════════
class SmartCache:
    """Cache هوشمند چند منظوره"""
    def __init__(self):
        self._data = {}
        self._timestamps = {}
    
    def get(self, key, default=None):
        if key in self._data and key in self._timestamps:
            ttl = self._get_ttl(key)
            if time.time() - self._timestamps[key] < ttl:
                return self._data[key]
            else:
                del self._data[key]
                del self._timestamps[key]
        return default
    
    def set(self, key, value):
        self._data[key] = value
        self._timestamps[key] = time.time()
    
    def invalidate(self, pattern=None):
        if pattern is None:
            self._data.clear()
            self._timestamps.clear()
        else:
            keys_to_del = [k for k in self._data if k.startswith(pattern)]
            for k in keys_to_del:
                del self._data[k]
                if k in self._timestamps:
                    del self._timestamps[k]
    
    def _get_ttl(self, key):
        """TTL بر اساس نوع cache"""
        if key.startswith("membership_"):
            return 900  # 15 دقیقه
        if key.startswith("account_"):
            return 300  # 5 دقیقه
        if key.startswith("stats_"):
            return 60   # 1 دقیقه
        if key.startswith("challenge_"):
            return 120  # 2 دقیقه
        if key.startswith("lottery_"):
            return 60   # 1 دقیقه
        return 300      # پیش‌فرض 5 دقیقه

cache = SmartCache()

# State management برای فرم‌های مالک
_owner_states = {}


def get_bot():
    return _bot


def _check_membership_cached(user_id):
    """بررسی عضویت با cache - فقط یک بار API call"""
    cache_key = f"membership_{user_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    is_member, missing = db.check_user_membership(_bot, user_id)
    result = (is_member, missing)
    cache.set(cache_key, result)
    return result


def _get_account_cached(tg_id):
    """دریافت حساب با cache"""
    cache_key = f"account_{tg_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    account = db.get_account_by_tg_id(tg_id)
    if account:
        cache.set(cache_key, account)
    return account


def _invalidate_cache(user_id=None):
    """پاک کردن cache"""
    if user_id:
        cache.invalidate(f"membership_{user_id}")
        cache.invalidate(f"account_{user_id}")
        cache.invalidate(f"stats_")
    else:
        cache.invalidate("membership_")
        cache.invalidate("account_")
        cache.invalidate("stats_")


def start_token_bot():
    global _bot, BOT_USERNAME

    if not config.BOT_TOKEN:
        print("⚠️ BOT_TOKEN تنظیم نشده — ربات الماس غیرفعال است")
        return

    # ✅ تغییر مهم: threaded=True برای سرعت بالاتر
    _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4)

    try:
        me = _bot.get_me()
        BOT_USERNAME = me.username
        print(f"🤖 ربات الماس: @{BOT_USERNAME}")
    except Exception as e:
        print(f"❌ خطا در اتصال ربات الماس: {e}")
        _bot = None
        return

    import time as _time
    for attempt in range(3):
        try:
            _bot.delete_webhook(drop_pending_updates=True)
            _time.sleep(2)
            break
        except:
            _time.sleep(2)

    # ══════════════════════════════════════════════════════════════════════════
    # توابع کمکی بهینه‌شده
    # ══════════════════════════════════════════════════════════════════════════
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
        is_member, missing = _check_membership_cached(message.from_user.id)
        if not is_member:
            send_forced_channels_menu(message, missing)
            return False
        return True

    def _user_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, input_field_placeholder="یک گزینه را انتخاب کنید:")
        markup.add("💎 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
        return markup

    def _owner_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, input_field_placeholder="یک گزینه را انتخاب کنید:")
        markup.add("💎 موجودی", "🎁 هدیه روزانه")
        markup.add("🔗 رفرال", "🛒 خرید الماس")
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
            types.InlineKeyboardButton("🎲 قرعه‌کشی", callback_data="admin_lottery")
        )
        markup.add(
            types.InlineKeyboardButton("💎 انتقال الماس", callback_data="admin_transfer"),
            types.InlineKeyboardButton("💰 دادن الماس", callback_data="admin_give")
        )
        markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
        return markup

    # ══════════════════════════════════════════════════════════════════════════
    # /start - بهینه‌شده
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            tg_id = message.from_user.id
            parts = message.text.strip().split()
            ref_code = parts[1] if len(parts) > 1 else None
            
            # پردازش رفرال (غیر بلاک‌کننده)
            if ref_code and ref_code.startswith("ref_"):
                try:
                    referrer_id = int(ref_code[4:])
                    threading.Thread(target=_process_referral_async, args=(referrer_id, tg_id), daemon=True).start()
                except: pass

            # بررسی عضویت (با cache)
            is_member, missing = _check_membership_cached(tg_id)
            if not is_member:
                send_forced_channels_menu(message, missing)
                return

            # دریافت حساب (با cache)
            account = _get_account_cached(tg_id)
            site_url = getattr(config, "SITE_URL", "")

            if not account:
                markup = types.InlineKeyboardMarkup()
                if site_url:
                    markup.add(types.InlineKeyboardButton("🌐 ورود به پنل وب", url=site_url))
                _bot.reply_to(message, 
                    "👋 <b>سلام!</b>\n\n"
                    "برای استفاده از ربات:\n"
                    "1️⃣ در پنل وب ثبت‌نام کنید\n"
                    "2️⃣ حساب تلگرام را وصل کنید\n"
                    "3️⃣ دوباره /start بزنید", 
                    reply_markup=markup if site_url else None)
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
                f"⚡ هر <b>۲ الماس</b> = <b>۲ ساعت</b> سلف‌بات\n"
                f"💰 قیمت هر الماس: <b>{token_price} تومان</b>",
                reply_markup=markup
            )

            # اسپانسرها فقط در PV و فقط یک بار
            if message.chat.type == 'private':
                sponsors = getattr(config, 'SPONSORS', [])
                if sponsors:
                    sponsors_text = "🤝 <b>اسپانسرهای رسمی پروژه:</b>\n"
                    for sp in sponsors:
                        sponsors_text += f"🔸 @{sp['username']}\n"
                    sponsors_text += f"\n👑 <b>مالک و پشتیبانی:</b> @{config.OWNER_USERNAME}"
                    _bot.send_message(message.chat.id, sponsors_text)
        except Exception as e:
            print(f"❌ خطا در cmd_start: {e}")

    def _process_referral_async(referrer_id, tg_id):
        """پردازش رفرال در thread جداگانه"""
        try:
            if db.process_referral(referrer_id, tg_id):
                referrer_tg = db.get_telegram_id_by_owner(referrer_id)
                if referrer_tg and _bot:
                    _bot.send_message(referrer_tg, 
                        f"🎉 یک نفر با لینک شما عضو شد!\n"
                        f"<b>+{config.REFERRAL_TOKENS} الماس</b> دریافت کردید 💎")
        except Exception as e:
            print(f"❌ خطا در رفرال: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Callback: بررسی عضویت
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data == "check_join")
    def callback_check_join(call):
        try:
            # پاک کردن cache قبل از بررسی مجدد
            cache.invalidate(f"membership_{call.from_user.id}")
            is_member, missing = _check_membership_cached(call.from_user.id)
            if is_member:
                _bot.answer_callback_query(call.id, "عضویت تأیید شد! ✅")
                try: _bot.delete_message(call.message.chat.id, call.message.message_id)
                except: pass
                cmd_start(call.message)
            else:
                _bot.answer_callback_query(call.id, f"هنوز در {len(missing)} کانال عضو نشده‌اید! ❌", show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_check_join: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دکمه‌های منوی اصلی - بهینه‌شده
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "💎 موجودی", chat_types=['private'])
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
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
            print(f"❌ خطا در cmd_balance: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🎁 هدیه روزانه", chat_types=['private'])
    def cmd_daily(message):
        try:
            if not require_membership(message): return
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            success, msg = db.claim_daily_token(account["id"])
            # پاک کردن cache بعد از تغییر موجودی
            cache.invalidate(f"account_{message.from_user.id}")
            
            if success:
                stats = db.get_token_stats(account["id"])
                _bot.reply_to(message, f"{msg}\n\n💎 موجودی جدید: <b>{stats['balance']}</b>", reply_markup=_user_keyboard())
            else:
                _bot.reply_to(message, msg, reply_markup=_user_keyboard())
        except Exception as e:
            print(f"❌ خطا در cmd_daily: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🔗 رفرال", chat_types=['private'])
    def cmd_referral(message):
        try:
            if not require_membership(message): return
            account = _get_account_cached(message.from_user.id)
            if not account:
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
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
            print(f"❌ خطا در cmd_referral: {e}")

    @_bot.message_handler(func=lambda m: m.text == "🛒 خرید الماس", chat_types=['private'])
    def cmd_buy(message):
        try:
            if not require_membership(message): return
            account = _get_account_cached(message.from_user.id)
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
            print(f"❌ خطا در cmd_buy: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 📢 پنل مدیریت مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: m.text == "📢 مدیریت", chat_types=['private'])
    def cmd_admin_panel(message):
        if message.from_user.id != OWNER_TG_ID: return
        _bot.reply_to(message, 
            "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=_admin_panel_keyboard())

    # ══════════════════════════════════════════════════════════════════════════
    # 🎯 Callback handler پنل مدیریت - بهینه‌شده
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
    def callback_admin(call):
        if call.from_user.id != OWNER_TG_ID:
            return _bot.answer_callback_query(call.id, "❌ فقط مالک دسترسی دارد", show_alert=True)
        
        try:
            action = call.data[6:]  # حذف "admin_"
            
            if action in ("panel", "back"):
                _bot.edit_message_text(
                    call.message.chat.id, call.message.message_id,
                    "📢 <b>پنل مدیریت مالک</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
                    reply_markup=_admin_panel_keyboard()
                )
            
            elif action == "channels":
                channels = db.get_forced_channels()
                markup = types.InlineKeyboardMarkup(row_width=1)
                if channels:
                    text = "📢 <b>چنل‌های اجباری فعلی:</b>\n\n"
                    for ch in channels:
                        text += f"🔸 <code>{ch}</code>\n"
                        markup.add(types.InlineKeyboardButton(f"❌ حذف {ch}", callback_data=f"rmch_{ch}"))
                else:
                    text = "📋 لیست چنل‌ها خالی است.\n\n"
                text += "\nبرای افزودن چنل جدید از دکمه زیر استفاده کنید:"
                markup.add(types.InlineKeyboardButton("➕ افزودن چنل جدید", callback_data="addch_prompt"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id, text, reply_markup=markup)
            
            elif action.startswith("rmch_"):
                ch = call.data[5:]
                if db.remove_forced_channel(ch):
                    cache.invalidate("membership_")
                    _bot.answer_callback_query(call.id, f"✅ چنل {ch} حذف شد")
                    # Refresh لیست
                    call.data = "admin_channels"
                    callback_admin(call)
                else:
                    _bot.answer_callback_query(call.id, "❌ خطا در حذف")
            
            elif action == "addch_prompt":
                _owner_states[call.from_user.id] = {"state": "waiting_channel"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "📝 آیدی چنل را ارسال کنید (با @ شروع شود):\n\nمثال: <code>@mychannel</code>",
                    reply_markup=markup)
            
            elif action == "users":
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
                _bot.edit_message_text(call.message.chat.id, call.message.message_id, text, reply_markup=markup)
            
            elif action == "wc":
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("➕ ایجاد چالش جدید", callback_data="wc_new"))
                markup.add(types.InlineKeyboardButton("📋 چالش‌های فعال", callback_data="wc_list"))
                markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "🏆 <b>مدیریت چالش‌های جام جهانی</b>\n\nیک گزینه را انتخاب کنید:",
                    reply_markup=markup)
            
            elif action == "wc_new":
                _owner_states[call.from_user.id] = {"state": "wc_team1", "data": {}}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_wc"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "🏆 <b>ایجاد چالش جدید</b>\n\n"
                    "📝 مرحله ۱ از ۴:\nنام <b>تیم اول</b> را ارسال کنید:\n\nمثال: <code>ایران</code>",
                    reply_markup=markup)
            
            elif action == "wc_list":
                challenges = db.get_active_challenges()
                if not challenges:
                    text = "📋 هیچ چالش فعالی وجود ندارد."
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc"))
                else:
                    text = "🏆 <b>چالش‌های فعال:</b>\n\n"
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    for c in challenges:
                        text += f"<b>ID {c['id']}:</b> {c['team1']} vs {c['team2']}\n"
                        text += f"⏰ {c['match_time']} | 💎 {c['bet_amount']}\n\n"
                        markup.add(
                            types.InlineKeyboardButton(f"✅ برنده: {c['team1']}", callback_data=f"wcwin_{c['id']}_{c['team1']}"),
                            types.InlineKeyboardButton(f"✅ برنده: {c['team2']}", callback_data=f"wcwin_{c['id']}_{c['team2']}")
                        )
                    markup.add(types.InlineKeyboardButton("🔙 بازگشت", callback_data="admin_wc"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id, text, reply_markup=markup)
            
            elif action.startswith("wcwin_"):
                parts = call.data.split("_", 2)
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
                            try:
                                _bot.send_message(r["user_tg_id"], f"🎉 تبریک! شرط شما درست بود.\n💎 <b>{r['amount']} الماس</b> دریافت کردید.")
                            except: pass
                else:
                    _bot.answer_callback_query(call.id, f"❌ خطا: {results}", show_alert=True)
            
            elif action == "lottery":
                _owner_states[call.from_user.id] = {"state": "lottery_amount"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "🎲 <b>ایجاد قرعه‌کشی گروهی</b>\n\n"
                    "💎 مبلغ جایزه را ارسال کنید (الماس):\n\nمثال: <code>100</code>\n\n"
                    "⚠️ قرعه‌کشی در گروه <code>@amelselfgap</code> ایجاد می‌شود.",
                    reply_markup=markup)
            
            elif action == "transfer":
                _owner_states[call.from_user.id] = {"state": "transfer_user"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "💎 <b>انتقال الماس (از طرف سیستم)</b>\n\n"
                    "📝 یوزرنیم کاربر مقصد را ارسال کنید:\n\nمثال: <code>ali</code>",
                    reply_markup=markup)
            
            elif action == "give":
                _owner_states[call.from_user.id] = {"state": "give_user"}
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ لغو", callback_data="admin_panel"))
                _bot.edit_message_text(call.message.chat.id, call.message.message_id,
                    "💰 <b>دادن الماس به کاربر</b>\n\n"
                    "📝 یوزرنیم کاربر را ارسال کنید:\n\nمثال: <code>ali</code>",
                    reply_markup=markup)
            
            else:
                _bot.answer_callback_query(call.id, "❌ گزینه نامعتبر")
        
        except Exception as e:
            print(f"❌ خطا در callback_admin: {e}")
            try:
                _bot.answer_callback_query(call.id, f"❌ خطا: {str(e)[:100]}", show_alert=True)
            except: pass

    # ══════════════════════════════════════════════════════════════════════════
    # 📨 State handler
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
                    cache.invalidate("membership_")
                    _bot.reply_to(message, f"✅ چنل <b>{text}</b> اضافه شد.", reply_markup=_owner_keyboard())
                else:
                    _bot.reply_to(message, f"⚠️ خطا یا تکراری است.", reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "wc_team1":
                state_data["data"]["team1"] = text
                state_data["state"] = "wc_team2"
                _bot.reply_to(message, f"✅ تیم اول: <b>{text}</b>\n\n📝 مرحله ۲ از ۴:\nنام <b>تیم دوم</b> را ارسال کنید:")
            
            elif state == "wc_team2":
                state_data["data"]["team2"] = text
                state_data["state"] = "wc_time"
                _bot.reply_to(message, f"✅ تیم دوم: <b>{text}</b>\n\n📝 مرحله ۳ از ۴:\n⏰ ساعت بازی را ارسال کنید:\n\nمثال: <code>20:30</code>")
            
            elif state == "wc_time":
                state_data["data"]["time"] = text
                state_data["state"] = "wc_bet"
                _bot.reply_to(message, f"✅ ساعت: <b>{text}</b>\n\n📝 مرحله ۴ از ۴:\n💎 مبلغ شرط (الماس) را ارسال کنید:\n\nمثال: <code>10</code>")
            
            elif state == "wc_bet":
                try:
                    bet_amount = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد. دوباره تلاش کنید:")
                
                data = state_data["data"]
                challenge_id = db.create_world_cup_challenge(data["team1"], data["team2"], data["time"], bet_amount)
                
                group = getattr(config, 'WORLD_CUP_GROUP', '@amelselfgap')
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton(f"🔵 {data['team1']}", callback_data=f"bet_wc_{challenge_id}_{data['team1']}"),
                    types.InlineKeyboardButton(f"🔴 {data['team2']}", callback_data=f"bet_wc_{challenge_id}_{data['team2']}")
                )
                
                try:
                    msg = _bot.send_message(group,
                        f"⚽️ <b>چالش جام جهانی!</b>\n\n"
                        f"🆚 <b>{data['team1']}</b> در برابر <b>{data['team2']}</b>\n"
                        f"⏰ ساعت: <b>{data['time']}</b>\n"
                        f"💎 مبلغ شرط: <b>{bet_amount} الماس</b>\n\n"
                        f"کدام تیم برنده می‌شود؟ شرط ببندید!",
                        reply_markup=markup)
                    db.update_challenge_message(challenge_id, msg.message_id, msg.chat.id)
                    _bot.reply_to(message, 
                        f"✅ چالش با موفقیت ایجاد شد!\n\n"
                        f"🆚 {data['team1']} vs {data['team2']}\n"
                        f"⏰ {data['time']} | 💎 {bet_amount}\n"
                        f"📢 ID چالش: <code>{challenge_id}</code>",
                        reply_markup=_owner_keyboard())
                except Exception as e:
                    _bot.reply_to(message, f"❌ خطا در ارسال به گروه: {e}\nمطمئن شوید ربات در {group} ادمین است.", reply_markup=_owner_keyboard())
                
                _owner_states.pop(message.from_user.id, None)
            
            elif state == "lottery_amount":
                try:
                    prize = int(text)
                except:
                    return _bot.reply_to(message, "❌ مبلغ باید عدد باشد:")
                
                group = getattr(config, 'WORLD_CUP_GROUP', '@amelselfgap')
                duration = getattr(config, 'LOTTERY_DURATION_MINUTES', 5)
                lottery_id = db.create_lottery(0, OWNER_TG_ID, prize, duration)
                
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🎲 شرکت در قرعه‌کشی (۱ الماس)", callback_data=f"join_lottery_{lottery_id}"))
                
                end_time = datetime.datetime.now() + datetime.timedelta(minutes=duration)
                end_time_str = end_time.strftime("%H:%M")
                
                try:
                    msg = _bot.send_message(group,
                        f"🎉 <b>قرعه‌کشی ویژه!</b>\n\n"
                        f"💎 مبلغ جایزه: <b>{prize} الماس</b>\n"
                        f"⏰ زمان پایان: <b>{end_time_str}</b>\n\n"
                        f"برای شرکت، روی دکمه زیر کلیک کنید!\n"
                        f"(هزینه شرکت: ۱ الماس)",
                        reply_markup=markup)
                    db.update_lottery_message(lottery_id, msg.message_id)
                    _bot.reply_to(message, 
                        f"✅ قرعه‌کشی ایجاد شد!\n\n"
                        f"💎 جایزه: {prize} الماس\n"
                        f"⏰ پایان: {end_time_str}\n"
                        f"📢 ID: <code>{lottery_id}</code>",
                        reply_markup=_owner_keyboard())
                    
                    threading.Timer(duration * 60, _finish_lottery, args=[lottery_id, group]).start()
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
                
                to_tg_id = db.get_telegram_id_by_owner(to_account["id"])
                if to_tg_id:
                    try:
                        _bot.send_message(to_tg_id, f"🎁 <b>{amount} الماس</b> از طرف سیستم دریافت کردید!\n💎 موجودی جدید: <b>{new_balance}</b>")
                    except: pass
                
                _bot.reply_to(message, 
                    f"✅ <b>{amount} الماس</b> به <b>{to_account['username']}</b> داده شد.\n💎 موجودی جدید: <b>{new_balance}</b>",
                    reply_markup=_owner_keyboard())
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
                token_price = getattr(config, 'TOKEN_PRICE_TOMAN', 200)
                
                tg_id = db.get_telegram_id_by_owner(account["id"])
                if tg_id:
                    try:
                        _bot.send_message(tg_id, f"🎁 <b>{amount} الماس</b> از طرف مالک دریافت کردید!\n💎 موجودی جدید: <b>{new_balance}</b>")
                    except: pass
                
                _bot.reply_to(message, 
                    f"✅ <b>{amount}</b> الماس به <b>{account['username']}</b> داده شد.\n"
                    f"💎 موجودی جدید: <b>{new_balance}</b> (معادل {new_balance * token_price} تومان)",
                    reply_markup=_owner_keyboard())
                _owner_states.pop(message.from_user.id, None)
        
        except Exception as e:
            print(f"❌ خطا در handle_owner_state: {e}")
            _bot.reply_to(message, f"❌ خطا: {e}", reply_markup=_owner_keyboard())
            _owner_states.pop(message.from_user.id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # 🎲 Callback: شرکت در قرعه‌کشی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("join_lottery_"))
    def callback_join_lottery(call):
        try:
            lottery_id = int(call.data.split("_")[2])
            lottery = db.get_lottery(lottery_id)
            
            if not lottery or lottery["status"] != "active":
                return _bot.answer_callback_query(call.id, "❌ این قرعه‌کشی فعال نیست یا به پایان رسیده.", show_alert=True)
            
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            
            success, msg = db.join_lottery(lottery_id, call.from_user.id, account["id"], 1)
            _bot.answer_callback_query(call.id, msg, show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_join_lottery: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 🏆 Callback: شرط‌بندی جام جهانی
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.callback_query_handler(func=lambda call: call.data.startswith("bet_wc_"))
    def callback_bet_wc(call):
        try:
            parts = call.data.split("_", 3)
            challenge_id = int(parts[2])
            team_choice = parts[3]
            
            # Cache برای challenge
            cache_key = f"challenge_{challenge_id}"
            challenge = cache.get(cache_key)
            if challenge is None:
                challenge = db.get_challenge(challenge_id)
                if challenge:
                    cache.set(cache_key, challenge)
            
            if not challenge or challenge["status"] != "active":
                return _bot.answer_callback_query(call.id, "❌ این چالش فعال نیست.", show_alert=True)
            
            account = _get_account_cached(call.from_user.id)
            if not account:
                return _bot.answer_callback_query(call.id, "❌ ابتدا در پنل وب ثبت‌نام کنید.", show_alert=True)
            
            success, msg = db.place_bet(challenge_id, call.from_user.id, account["id"], team_choice, challenge["bet_amount"])
            _bot.answer_callback_query(call.id, msg, show_alert=True)
        except Exception as e:
            print(f"❌ خطا در callback_bet_wc: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # دستورات متنی قدیمی مالک
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(commands=["addchannel", "removechannel", "give", "users", "wc_create", "wc_winner", "lottery", "transfer"])
    def cmd_text_commands(message):
        if message.from_user.id != OWNER_TG_ID: return
        _bot.reply_to(message, 
            "📢 تمام دستورات مدیریتی به پنل دکمه‌ای منتقل شدند.\n\n"
            "روی دکمه <b>📢 مدیریت</b> کلیک کنید.",
            reply_markup=_owner_keyboard())

    # ══════════════════════════════════════════════════════════════════════════
    # ✅ پیام‌های ناشناخته - بهینه‌شده (بدون require_membership)
    # ══════════════════════════════════════════════════════════════════════════
    @_bot.message_handler(func=lambda m: True, chat_types=['private'])
    def cmd_unknown(message):
        try:
            # ✅ فقط حساب را چک کن، نه عضویت (سرعت بیشتر)
            account = _get_account_cached(message.from_user.id)
            if not account: 
                return _bot.reply_to(message, "⚠️ ابتدا در پنل وب ثبت‌نام کنید.", reply_markup=_user_keyboard())
            
            kb = _owner_keyboard() if message.from_user.id == OWNER_TG_ID else _user_keyboard()
            _bot.reply_to(message, "⚠️ دستور نامعتبر. از دکمه‌های زیر استفاده کنید:", reply_markup=kb)
        except Exception as e:
            print(f"❌ خطا در cmd_unknown: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Polling - بهینه‌شده
    # ══════════════════════════════════════════════════════════════════════════
    def _polling_loop():
        import time as _t
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
                    _t.sleep(10)
                    try: _bot.delete_webhook(drop_pending_updates=True)
                    except: pass
                else:
                    print(f"⚠️ خطای polling: {e}")
                    _t.sleep(3)

    t = threading.Thread(target=_polling_loop, daemon=True)
    t.start()
    print(f"✅ ربات الماس @{BOT_USERNAME} استارت شد (threaded=True, 4 threads)")


# ══════════════════════════════════════════════════════════════════════════════
# پایان قرعه‌کشی
# ══════════════════════════════════════════════════════════════════════════════
def _finish_lottery(lottery_id, group_chat):
    try:
        lottery = db.get_lottery(lottery_id)
        if not lottery or lottery["status"] != "active":
            return
        
        participants = db.get_lottery_participants(lottery_id)
        if not participants:
            if _bot:
                _bot.send_message(group_chat, f"🎲 قرعه‌کشی #{lottery_id} بدون شرکت‌کننده به پایان رسید.")
            return
        
        winner = random.choice(participants)
        success, total_prize = db.finish_lottery(lottery_id, winner["user_tg_id"], winner["owner_id"])
        
        if success and _bot:
            winner_account = db.get_account(winner["owner_id"])
            winner_name = winner_account["username"] if winner_account else str(winner["user_tg_id"])
            _bot.send_message(group_chat, 
                f"🎉 <b>برنده قرعه‌کشی مشخص شد!</b>\n\n"
                f"🏆 برنده: <b>{winner_name}</b>\n"
                f"💎 جایزه: <b>{total_prize} الماس</b>\n"
                f"👥 شرکت‌کنندگان: {len(participants)} نفر")
            
            try:
                _bot.send_message(winner["user_tg_id"], 
                    f"🎉 تبریک! شما برنده قرعه‌کشی شدید!\n💎 <b>{total_prize} الماس</b> به حساب شما واریز شد.")
            except: pass
    except Exception as e:
        print(f"❌ خطا در پایان قرعه‌کشی: {e}")
