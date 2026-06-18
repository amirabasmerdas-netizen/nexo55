import threading
import logging
import asyncio
import config
import database as db

logger = logging.getLogger(__name__)

_bot = None
_bot_thread = None

try:
    import telebot
    from telebot import types
    TELEBOT_AVAILABLE = True
except ImportError:
    TELEBOT_AVAILABLE = False
    logger.warning("⚠️ telebot ماژول یافت نشد — ربات مدیریت غیرفعال است")

OWNER_TG_ID = config.OWNER_TG_ID


def _owner_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("👤 حساب‌ها", "💎 الماس")
    markup.add("📊 آمار", "🔌 کانال‌های اجباری")
    markup.add("💰 شارژ الماس")
    return markup


def _user_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
    markup.add("⚙️ تنظیمات سلف", "💎 الماس من")
    markup.add("🔗 پنل وب", "📖 راهنما")
    return markup


def _settings_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🟢 سلف روشن", "🔴 سلف خاموش")
    markup.add("🗑️ حذف سلف", "🛡️ امنیت")
    markup.add("🤖 منشی", "⚡ اتوماسیون")
    markup.add("🔤 فونت", "🔙 بازگشت به منو")
    return markup


def _security_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🛡️ ضد حذف", "🔗 ضد لینک")
    markup.add("🔒 قفل پیوی", "⚔️ پاسخ دشمن")
    markup.add("🔙 بازگشت")
    return markup


def _automation_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("👁️ سین خودکار", "❤️ ری‌اکشن")
    markup.add("💾 ذخیره مدیا", "⏰ ساعت نام")
    markup.add("⏰ ساعت بیو", "🔙 بازگشت")
    return markup


def get_user_account(tg_id: int):
    return db.get_account_by_tg_id(tg_id)


def _signup_inline():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ ثبت‌نام رایگان", callback_data="signup"))
    return markup


def _get_loop():
    try:
        return asyncio.get_event_loop()
    except Exception:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def require_membership(message):
    if not config.BOT_TOKEN:
        return True
    from db_cache import get_forced_channels, check_user_membership
    channels = get_forced_channels()
    if not channels:
        return True
    ok, missing = check_user_membership(_bot, message.from_user.id)
    if not ok:
        markup = types.InlineKeyboardMarkup()
        for ch in missing:
            markup.add(types.InlineKeyboardButton(f"عضویت در {ch}", url=f"https://t.me/{ch.lstrip('@')}"))
        markup.add(types.InlineKeyboardButton("✅ عضو شدم", callback_data="check_membership"))
        _bot.send_message(message.chat.id,
            f"⚠️ برای استفاده باید در کانال‌های زیر عضو باشید:\n" + "\n".join(missing),
            reply_markup=markup)
        return False
    return True


def _register_handlers(bot):

    @bot.message_handler(commands=["start"])
    def cmd_start(message):
        try:
            if not require_membership(message): return
            tg_id = message.from_user.id
            account = get_user_account(tg_id)
            is_owner = (tg_id == OWNER_TG_ID)

            if account:
                has_session = db.get_session(account["id"]) is not None
                balance = db.get_token_balance(account["id"])
                kb = _owner_keyboard() if is_owner else _user_keyboard()
                bot.send_message(message.chat.id,
                    f"🤖 <b>AMEL SELF55 v{config.BOT_VERSION}</b>\n\n"
                    f"👤 یوزرنیم: <code>{account['username']}</code>\n"
                    f"💎 الماس: <b>{balance}</b>\n"
                    f"🔌 سلف: {'✅ متصل' if has_session else '❌ متصل نشده'}\n\n"
                    f"{'👑 خوش آمدید مالک عزیز!' if is_owner else ''}",
                    reply_markup=kb, parse_mode="HTML")
            else:
                args = message.text.split() if message.text else []
                ref_code = args[1] if len(args) > 1 else None
                bot.send_message(message.chat.id,
                    f"🤖 <b>AMEL SELF55</b>\n\nبه سلف‌بات پیشرفته خوش آمدید!\n\n"
                    f"برای شروع، ثبت‌نام کنید:",
                    reply_markup=_signup_inline(), parse_mode="HTML")
                if ref_code:
                    bot.register_next_step_handler(
                        bot.send_message(message.chat.id, "👤 یوزرنیم دلخواه را وارد کنید:"),
                        lambda m: _process_username(m, ref_code=ref_code))
        except Exception as e:
            logger.error(f"❌ cmd_start error: {e}")

    @bot.callback_query_handler(func=lambda c: c.data == "signup")
    def callback_signup(call):
        try:
            if not require_membership(call.message): return
            account = get_user_account(call.from_user.id)
            if account:
                bot.answer_callback_query(call.id, "✅ قبلاً ثبت‌نام کرده‌اید!")
                bot.send_message(call.message.chat.id, "✅ قبلاً ثبت‌نام کرده‌اید.", reply_markup=_user_keyboard())
                return
            bot.answer_callback_query(call.id)
            msg = bot.send_message(call.message.chat.id,
                "👤 <b>ثبت‌نام</b>\n\nیوزرنیم دلخواه را وارد کنید (حداقل ۳ کاراکتر):",
                parse_mode="HTML")
            bot.register_next_step_handler(msg, _process_username)
        except Exception as e:
            logger.error(f"❌ callback_signup error: {e}")

    @bot.callback_query_handler(func=lambda c: c.data == "check_membership")
    def callback_check_membership(call):
        try:
            from db_cache import check_user_membership
            ok, missing = check_user_membership(bot, call.from_user.id)
            if ok:
                bot.answer_callback_query(call.id, "✅ عضویت تأیید شد!")
                bot.send_message(call.message.chat.id, "✅ عضویت تأیید شد!\n/start را بزنید.")
            else:
                bot.answer_callback_query(call.id, "❌ هنوز عضو نشده‌اید", show_alert=True)
        except Exception as e:
            logger.error(f"❌ callback_check_membership error: {e}")

    def _process_username(message, ref_code=None):
        if message.text and message.text.startswith("/"):
            return
        username = message.text.strip().lower() if message.text else ""
        if len(username) < 3:
            msg = bot.send_message(message.chat.id, "❌ یوزرنیم باید حداقل ۳ کاراکتر باشد. مجدداً وارد کنید:")
            bot.register_next_step_handler(msg, lambda m: _process_username(m, ref_code))
            return
        if db.get_account_by_username(username):
            msg = bot.send_message(message.chat.id, "❌ این یوزرنیم قبلاً استفاده شده. یوزرنیم دیگری وارد کنید:")
            bot.register_next_step_handler(msg, lambda m: _process_username(m, ref_code))
            return
        msg = bot.send_message(message.chat.id,
            f"✅ یوزرنیم: <code>{username}</code>\n\n🔐 حالا رمز عبور را وارد کنید (حداقل ۶ کاراکتر):",
            parse_mode="HTML")
        bot.register_next_step_handler(msg, lambda m: _process_password(m, username, ref_code))

    def _process_password(message, username, ref_code=None):
        if message.text and message.text.startswith("/"):
            return
        password = message.text.strip() if message.text else ""
        if len(password) < 6:
            msg = bot.send_message(message.chat.id, "❌ رمز باید حداقل ۶ کاراکتر باشد. مجدداً وارد کنید:")
            bot.register_next_step_handler(msg, lambda m: _process_password(m, username, ref_code))
            return
        try:
            oid = db.create_account(username, password)
            if oid is None:
                bot.send_message(message.chat.id, "❌ خطا در ایجاد حساب. دوباره تلاش کنید.")
                return
            db.init_user_settings(oid)
            db.save_telegram_user_id(oid, message.from_user.id)
            db.add_tokens(oid, config.WELCOME_TOKENS)
            if ref_code:
                try:
                    ref_account = db.get_account_by_username(ref_code)
                    if ref_account and ref_account["id"] != oid:
                        db.process_referral(ref_account["id"], message.from_user.id)
                        db.add_tokens(ref_account["id"], config.REFERRAL_TOKENS)
                except Exception:
                    pass
            bot.send_message(message.chat.id,
                f"✅ <b>ثبت‌نام موفق!</b>\n\n"
                f"👤 یوزرنیم: <code>{username}</code>\n"
                f"💎 الماس اولیه: {config.WELCOME_TOKENS}\n\n"
                f"{'🔗 پنل وب: ' + config.SITE_URL if config.SITE_URL else ''}\n\n"
                f"برای اتصال به تلگرام از پنل وب اقدام کنید.",
                reply_markup=_user_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ _process_password error: {e}")
            bot.send_message(message.chat.id, f"❌ خطا در ثبت‌نام: {e}")

    @bot.message_handler(func=lambda m: m.text == "💎 الماس من", chat_types=['private'])
    def cmd_balance(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            balance = db.get_token_balance(account["id"])
            refs = db.get_referral_count(account["id"])
            site = f"\n\n🔗 پنل: {config.SITE_URL}" if config.SITE_URL else ""
            bot.reply_to(message,
                f"💎 <b>الماس‌های شما</b>\n\n"
                f"💰 موجودی: <b>{balance}</b> الماس\n"
                f"👥 رفرال‌ها: <b>{refs}</b> نفر\n\n"
                f"⚡ هر {config.TOKENS_PER_SESSION} الماس = {config.SESSION_HOURS} ساعت سلف\n"
                f"🎁 هدیه روزانه: {config.DAILY_TOKEN_GIFT} الماس{site}",
                reply_markup=_user_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ cmd_balance error: {e}")

    @bot.message_handler(func=lambda m: m.text == "🟢 سلف روشن", chat_types=['private'])
    def cmd_start_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.", reply_markup=_user_keyboard())
            session_data = db.get_session(account["id"])
            if not session_data:
                bot.reply_to(message, "❌ سشن تلگرام پیدا نشد.\nاز پنل وب وارد شوید.", reply_markup=_user_keyboard(), parse_mode="HTML")
                return
            from bot import bot_manager
            loop = _get_loop()
            ok = bot_manager.start(account["id"], loop, check_tokens=True)
            if ok:
                db.set_setting(account["id"], "self_bot_active", "1")
                bot.reply_to(message, "✅ سلف‌بات روشن شد!", reply_markup=_settings_keyboard())
            else:
                balance = db.get_token_balance(account["id"])
                bot.reply_to(message,
                    f"❌ الماس کافی ندارید!\n💎 موجودی: {balance}\n⚡ نیاز: {config.TOKENS_PER_SESSION} الماس",
                    reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ cmd_start_bot error: {e}")
            bot.reply_to(message, f"⚠️ خطا: {e}", reply_markup=_settings_keyboard())

    @bot.message_handler(func=lambda m: m.text == "🔴 سلف خاموش", chat_types=['private'])
    def cmd_stop_bot(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            from bot import bot_manager
            bot_manager.stop(account["id"])
            db.set_setting(account["id"], "self_bot_active", "0")
            bot.reply_to(message, "❌ سلف‌بات خاموش شد.", reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ cmd_stop_bot error: {e}")

    @bot.message_handler(func=lambda m: m.text == "⚙️ تنظیمات سلف", chat_types=['private'])
    def cmd_settings(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            bot.reply_to(message, "⚙️ تنظیمات سلف:", reply_markup=_settings_keyboard())
        except Exception as e:
            logger.error(f"❌ cmd_settings error: {e}")

    @bot.message_handler(func=lambda m: m.text == "🗑️ حذف سلف", chat_types=['private'])
    def cmd_remove_self(message):
        try:
            if not require_membership(message): return
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ بله", callback_data=f"confirm_remove_self_{account['id']}"),
                types.InlineKeyboardButton("❌ لغو", callback_data="cancel_remove_self")
            )
            bot.reply_to(message,
                "⚠️ آیا مطمئنید؟ سلف حذف و از تلگرام خارج می‌شوید.",
                reply_markup=markup)
        except Exception as e:
            logger.error(f"❌ cmd_remove_self error: {e}")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_remove_self_"))
    def callback_confirm_remove_self(call):
        try:
            oid = int(call.data.split("_")[-1])
            account = get_user_account(call.from_user.id)
            if not account or account["id"] != oid:
                return bot.answer_callback_query(call.id, "❌ خطا", show_alert=True)
            try:
                from bot import bot_manager
                bot_manager.stop(oid)
            except Exception:
                pass
            db.delete_session(oid)
            db.set_setting(oid, "logged_in", "0")
            db.set_setting(oid, "self_bot_active", "0")
            bot.answer_callback_query(call.id, "✅ سلف حذف شد!")
            bot.send_message(call.message.chat.id,
                "🗑️ سلف حذف شد. برای اتصال مجدد از پنل وب اقدام کنید.",
                reply_markup=_user_keyboard())
        except Exception as e:
            logger.error(f"❌ callback_confirm_remove_self error: {e}")

    @bot.callback_query_handler(func=lambda c: c.data == "cancel_remove_self")
    def callback_cancel_remove_self(call):
        bot.answer_callback_query(call.id, "لغو شد.")
        bot.send_message(call.message.chat.id, "❌ لغو شد.", reply_markup=_settings_keyboard())

    @bot.message_handler(func=lambda m: m.text == "🔙 بازگشت", chat_types=['private'])
    def cmd_back(message):
        bot.reply_to(message, "🔙", reply_markup=_settings_keyboard())

    @bot.message_handler(func=lambda m: m.text == "🔙 بازگشت به منو", chat_types=['private'])
    def cmd_back_to_menu(message):
        is_owner = (message.from_user.id == OWNER_TG_ID)
        bot.reply_to(message, "🔙", reply_markup=_owner_keyboard() if is_owner else _user_keyboard())

    @bot.message_handler(func=lambda m: m.text == "🛡️ امنیت", chat_types=['private'])
    def cmd_security(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return
            s = account["id"]
            text = (
                f"🛡️ <b>امنیت</b>\n\n"
                f"ضد حذف: {'✅' if db.get_setting(s,'anti_delete_active')=='1' else '❌'}\n"
                f"ضد لینک: {'✅' if db.get_setting(s,'anti_link_active')=='1' else '❌'}\n"
                f"قفل پیوی: {'✅' if db.get_setting(s,'private_lock_active')=='1' else '❌'}\n"
                f"پاسخ دشمن: {'✅' if db.get_setting(s,'enemy_reply_active')=='1' else '❌'}"
            )
            bot.reply_to(message, text, reply_markup=_security_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ cmd_security error: {e}")

    def _toggle_setting(message, key, label, keyboard):
        account = get_user_account(message.from_user.id)
        if not account: return
        cur = db.get_setting(account["id"], key, "0")
        nv = "0" if cur == "1" else "1"
        db.set_setting(account["id"], key, nv)
        bot.reply_to(message, f"{label} {'روشن' if nv=='1' else 'خاموش'} شد!", reply_markup=keyboard())

    @bot.message_handler(func=lambda m: m.text.startswith("🛡️ ضد حذف"), chat_types=['private'])
    def t_anti_delete(m): _toggle_setting(m, "anti_delete_active", "🛡️ ضد حذف", _security_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("🔗 ضد لینک"), chat_types=['private'])
    def t_anti_link(m): _toggle_setting(m, "anti_link_active", "🔗 ضد لینک", _security_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("🔒 قفل پیوی"), chat_types=['private'])
    def t_priv_lock(m): _toggle_setting(m, "private_lock_active", "🔒 قفل پیوی", _security_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("⚔️ پاسخ دشمن"), chat_types=['private'])
    def t_enemy_reply(m): _toggle_setting(m, "enemy_reply_active", "⚔️ پاسخ دشمن", _security_keyboard)

    @bot.message_handler(func=lambda m: m.text == "⚡ اتوماسیون", chat_types=['private'])
    def cmd_automation(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return
            s = account["id"]
            text = (
                f"⚡ <b>اتوماسیون</b>\n\n"
                f"سین خودکار: {'✅' if db.get_setting(s,'auto_seen_active')=='1' else '❌'}\n"
                f"ری‌اکشن: {'✅' if db.get_setting(s,'auto_reaction_active')=='1' else '❌'}\n"
                f"ذخیره مدیا: {'✅' if db.get_setting(s,'auto_save_media')=='1' else '❌'}\n"
                f"ساعت نام: {'✅' if db.get_setting(s,'clock_name_active')=='1' else '❌'}\n"
                f"ساعت بیو: {'✅' if db.get_setting(s,'clock_bio_active')=='1' else '❌'}"
            )
            bot.reply_to(message, text, reply_markup=_automation_keyboard(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ cmd_automation error: {e}")

    @bot.message_handler(func=lambda m: m.text.startswith("👁️ سین خودکار"), chat_types=['private'])
    def t_auto_seen(m): _toggle_setting(m, "auto_seen_active", "👁️ سین خودکار", _automation_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("❤️ ری‌اکشن"), chat_types=['private'])
    def t_reaction(m): _toggle_setting(m, "auto_reaction_active", "❤️ ری‌اکشن", _automation_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("💾 ذخیره مدیا"), chat_types=['private'])
    def t_auto_save(m): _toggle_setting(m, "auto_save_media", "💾 ذخیره مدیا", _automation_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("⏰ ساعت نام"), chat_types=['private'])
    def t_clock_name(m): _toggle_setting(m, "clock_name_active", "⏰ ساعت نام", _automation_keyboard)

    @bot.message_handler(func=lambda m: m.text.startswith("⏰ ساعت بیو"), chat_types=['private'])
    def t_clock_bio(m): _toggle_setting(m, "clock_bio_active", "⏰ ساعت بیو", _automation_keyboard)

    @bot.message_handler(func=lambda m: m.text == "🤖 منشی", chat_types=['private'])
    def cmd_secretary(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account: return
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add("🤖 منشی روشن", "🤖 منشی خاموش")
            markup.add("✏️ تغییر پیام منشی", "🔙 بازگشت")
            bot.reply_to(message, "🤖 منشی:", reply_markup=markup)
        except Exception as e:
            logger.error(f"❌ cmd_secretary error: {e}")

    @bot.message_handler(func=lambda m: m.text == "🤖 منشی روشن", chat_types=['private'])
    def sec_on(m): _toggle_setting(m, "secretary_active", "🤖 منشی", _settings_keyboard)

    @bot.message_handler(func=lambda m: m.text == "🤖 منشی خاموش", chat_types=['private'])
    def sec_off(m): _toggle_setting(m, "secretary_active", "🤖 منشی", _settings_keyboard)

    @bot.message_handler(func=lambda m: m.text == "✏️ تغییر پیام منشی", chat_types=['private'])
    def cmd_change_sec_msg(message):
        account = get_user_account(message.from_user.id)
        if not account: return
        msg = bot.reply_to(message, "✏️ پیام جدید منشی را وارد کنید:")
        bot.register_next_step_handler(msg, lambda m: _save_sec_msg(m, account["id"]))

    def _save_sec_msg(message, oid):
        if message.text:
            db.set_setting(oid, "secretary_message", message.text)
            bot.reply_to(message, "✅ پیام منشی ذخیره شد!", reply_markup=_settings_keyboard())

    @bot.message_handler(func=lambda m: m.text == "🔤 فونت", chat_types=['private'])
    def cmd_font(message):
        markup = types.InlineKeyboardMarkup(row_width=3)
        fonts = [("۰ عادی","0"),("۱ Bold","1"),("۲ Italic","2"),("۳ Mono","3"),("۴ Full","4"),("۵ Serif","5"),("۶ Script","6"),("۷ Strike","7"),("۸ Under","8")]
        for label, val in fonts:
            markup.add(types.InlineKeyboardButton(label, callback_data=f"setfont_{val}"))
        bot.reply_to(message, "🔤 فونت را انتخاب کنید:", reply_markup=markup)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("setfont_"))
    def callback_setfont(call):
        try:
            account = get_user_account(call.from_user.id)
            if not account: return
            val = call.data.split("_")[1]
            db.set_setting(account["id"], "selected_font", val)
            bot.answer_callback_query(call.id, f"✅ فونت {val} انتخاب شد!")
        except Exception as e:
            logger.error(f"❌ callback_setfont error: {e}")

    @bot.message_handler(func=lambda m: m.text == "📊 آمار", chat_types=['private'])
    def cmd_stats(message):
        if message.from_user.id != OWNER_TG_ID:
            return bot.reply_to(message, "❌ فقط مالک می‌تواند این دستور را استفاده کند.")
        try:
            accounts = db.get_all_accounts()
            sessions = sum(1 for a in accounts if db.get_session(a["id"]) is not None)
            bot.reply_to(message,
                f"📊 <b>آمار کلی</b>\n\n"
                f"👥 کل کاربران: {len(accounts)}\n"
                f"🔌 دارای سشن: {sessions}",
                parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {e}")

    @bot.message_handler(func=lambda m: m.text == "👤 حساب‌ها", chat_types=['private'])
    def cmd_accounts(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        try:
            accounts = db.get_all_accounts()[:20]
            lines = [f"👥 <b>حساب‌ها ({len(accounts)} نفر)</b>\n"]
            for a in accounts:
                bal = db.get_token_balance(a["id"])
                lines.append(f"• <code>{a['username']}</code> — 💎{bal}")
            bot.reply_to(message, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {e}")

    @bot.message_handler(func=lambda m: m.text == "💎 الماس", chat_types=['private'])
    def cmd_diamonds_owner(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        msg = bot.reply_to(message, "💰 برای شارژ الماس وارد کنید:\n<code>یوزرنیم مقدار</code>", parse_mode="HTML")
        bot.register_next_step_handler(msg, _process_give_diamonds)

    def _process_give_diamonds(message):
        try:
            parts = message.text.split()
            if len(parts) < 2:
                bot.reply_to(message, "❌ فرمت: یوزرنیم مقدار")
                return
            username, amount = parts[0], int(parts[1])
            account = db.get_account_by_username(username.lstrip("@"))
            if not account:
                bot.reply_to(message, f"❌ کاربر '{username}' یافت نشد")
                return
            db.add_tokens(account["id"], amount)
            bot.reply_to(message, f"✅ {amount} الماس به {username} داده شد\n💎 موجودی: {db.get_token_balance(account['id'])}")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {e}")

    @bot.message_handler(func=lambda m: m.text == "🔗 پنل وب", chat_types=['private'])
    def cmd_panel_link(message):
        try:
            account = get_user_account(message.from_user.id)
            if not account:
                return bot.reply_to(message, "⚠️ ابتدا ثبت‌نام کنید.")
            site = config.SITE_URL or "https://your-site.com"
            bot.reply_to(message, f"🔗 <b>پنل وب</b>\n\n{site}\n\nبرای ورود از یوزرنیم و رمز خود استفاده کنید.", parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ cmd_panel_link error: {e}")

    @bot.message_handler(func=lambda m: m.text == "📖 راهنما", chat_types=['private'])
    def cmd_help(message):
        bot.reply_to(message,
            "📖 <b>راهنما</b>\n\n"
            "سلف روشن/خاموش — کنترل سلف\n"
            "الماس من — مدیریت الماس\n"
            "تنظیمات سلف — امنیت، اتوماسیون\n"
            "پنل وب — مدیریت پیشرفته\n\n"
            "📲 دستورات در تلگرام:\n"
            "سلف روشن/خاموش\n"
            "وضعیت\n"
            "راهنما",
            reply_markup=_user_keyboard(), parse_mode="HTML")

    @bot.message_handler(func=lambda m: m.text == "💰 شارژ الماس", chat_types=['private'])
    def cmd_charge(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        bot.reply_to(message, "💰 فرمت: یوزرنیم مقدار\nمثال: <code>@username 50</code>", parse_mode="HTML")

    @bot.message_handler(func=lambda m: m.text == "🔌 کانال‌های اجباری", chat_types=['private'])
    def cmd_forced_channels(message):
        if message.from_user.id != OWNER_TG_ID:
            return
        try:
            from db_cache import get_forced_channels, add_forced_channel, remove_forced_channel
            channels = get_forced_channels()
            markup = types.InlineKeyboardMarkup()
            if channels:
                for ch in channels:
                    markup.add(types.InlineKeyboardButton(f"❌ حذف {ch}", callback_data=f"rmch_{ch}"))
            markup.add(types.InlineKeyboardButton("➕ افزودن", callback_data="addch"))
            ch_list = ''.join('• ' + c + '\n' for c in channels) if channels else 'هیچ کانالی ثبت نشده'
            bot.reply_to(message,
                f"🔌 <b>کانال‌های اجباری</b>\n\n{ch_list}",
                reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {e}")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("rmch_"))
    def callback_remove_channel(call):
        if call.from_user.id != OWNER_TG_ID: return
        try:
            from db_cache import remove_forced_channel
            ch = call.data[5:]
            remove_forced_channel(ch)
            bot.answer_callback_query(call.id, f"✅ {ch} حذف شد!")
            bot.edit_message_text(f"✅ {ch} حذف شد!", call.message.chat.id, call.message.message_id)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ {e}", show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data == "addch")
    def callback_add_channel(call):
        if call.from_user.id != OWNER_TG_ID: return
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "👉 یوزرنیم کانال را وارد کنید (با @):")
        bot.register_next_step_handler(msg, _process_add_channel)

    def _process_add_channel(message):
        if message.from_user.id != OWNER_TG_ID: return
        try:
            from db_cache import add_forced_channel
            ch = message.text.strip()
            if add_forced_channel(ch):
                bot.reply_to(message, f"✅ {ch} اضافه شد!")
            else:
                bot.reply_to(message, "❌ خطا یا کانال تکراری")
        except Exception as e:
            bot.reply_to(message, f"❌ {e}")


def start_token_bot():
    global _bot, _bot_thread
    if not TELEBOT_AVAILABLE:
        logger.warning("⚠️ telebot در دسترس نیست — ربات مدیریت اجرا نمی‌شود")
        return
    if not config.BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN تنظیم نشده — ربات مدیریت اجرا نمی‌شود")
        return
    try:
        _bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True)
        _register_handlers(_bot)
        _bot_thread = threading.Thread(target=_bot_polling, daemon=True)
        _bot_thread.start()
        logger.info("✅ ربات تلگرام شروع به کار کرد")
    except Exception as e:
        logger.error(f"❌ خطا در شروع ربات: {e}")


def _bot_polling():
    import time
    while True:
        try:
            if _bot:
                _bot.infinity_polling(timeout=30, long_polling_timeout=15, logger_level=logging.WARNING)
        except Exception as e:
            logger.error(f"❌ polling خطا: {e}")
            time.sleep(5)
