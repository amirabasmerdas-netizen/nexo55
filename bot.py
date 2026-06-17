import asyncio
import re
import os
import datetime
import random
import threading
import time
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import FloodWaitError
import database as db
import config
from texts import ENEMY_REPLIES, FRIEND_REPLIES  

# ─── فونت‌ها ───────────────────────────────────────────────────────────────────
FONTS = {
    "0": lambda t: t,
    "1": lambda t: _convert_font(t, "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"),
    "2": lambda t: _convert_font(t, "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"),
    "3": lambda t: _convert_font(t, "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"),
    "4": lambda t: _convert_font(t, "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"),
    "5": lambda t: _convert_font(t, "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"),
    "6": lambda t: _convert_font(t, "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"),
    "7": lambda t: "".join(c + "\u0336" for c in t),
    "8": lambda t: "".join(c + "\u0332" for c in t),
}
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

LINK_PATTERN = re.compile(
    r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|www\.\S+)", re.IGNORECASE
)

# ─── سیستم محدودیت زمانی برای منشی و دوست ────────────────────────────────────
_last_secretary_reply = {}  # {chat_id: timestamp}
_last_friend_reply = {}     # {sender_id: timestamp}
SECRETARY_COOLDOWN = 1000  # 24 ساعت (86400 ثانیه)
FRIEND_COOLDOWN = 10     # 1 ساعت (3600 ثانیه)


def _convert_font(text, chars):
    result = []
    for ch in text:
        if ch in _ALPHA:
            result.append(chars[_ALPHA.index(ch)])
        else:
            result.append(ch)
    return "".join(result)


def _apply_font(owner_id, text):
    font_id = db.get_setting(owner_id, "selected_font", "0")
    fn = FONTS.get(font_id, FONTS["0"])
    return fn(text)


def persian_time():
    iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    now = datetime.datetime.now(iran_tz)
    return f"{now.hour:02d}:{now.minute:02d}"


# ─── BotManager: مدیریت چندین کلاینت همزمان ────────────────────────────────────
class BotManager:
    def __init__(self):
        self._bots = {}
        self._timers = {}

    def is_running(self, owner_id: int) -> bool:
        entry = self._bots.get(owner_id)
        return bool(entry and not entry["task"].done())

    def get_client(self, owner_id: int):
        entry = self._bots.get(owner_id)
        return entry["client"] if entry else None

    def _cancel_timer(self, owner_id: int):
        t = self._timers.pop(owner_id, None)
        if t:
            t.cancel()

    def session_end_time(self, owner_id: int):
        t = self._timers.get(owner_id)
        if t and t.is_alive():
            remaining = t.interval - (time.time() - t._timer_start if hasattr(t, '_timer_start') else 0)
            return max(0, remaining)
        return None

    def start(self, owner_id: int, loop: asyncio.AbstractEventLoop, check_tokens: bool = True) -> bool:
        if self.is_running(owner_id):
            self.stop(owner_id)

        tg_id = db.get_telegram_id_by_owner(owner_id)
        is_owner = (tg_id is not None and tg_id == config.OWNER_TG_ID)

        tokens_deducted = 0
        if config.BOT_TOKEN and check_tokens and not is_owner:
            balance = db.get_token_balance(owner_id)
            if balance < config.TOKENS_PER_SESSION:
                return False
            db.deduct_tokens(owner_id, config.TOKENS_PER_SESSION)
            tokens_deducted = config.TOKENS_PER_SESSION

        entry = {"client": None, "task": None, "stop": False, "is_owner": is_owner,
                 "tokens_deducted": tokens_deducted, "owner_refunded": False}
        self._bots[owner_id] = entry
        task = asyncio.run_coroutine_threadsafe(
            self._run_bot(owner_id), loop
        )
        entry["task"] = task

        if config.BOT_TOKEN and not is_owner:
            self._cancel_timer(owner_id)
            timer = threading.Timer(
                config.SESSION_HOURS * 3600, self.stop, args=[owner_id]
            )
            timer.daemon = True
            timer.start()
            self._timers[owner_id] = timer

        return True

    def stop(self, owner_id: int):
        self._cancel_timer(owner_id)
        entry = self._bots.get(owner_id)
        if not entry:
            return
        entry["stop"] = True
        cl = entry.get("client")
        if cl and cl.is_connected():
            try:
                asyncio.run_coroutine_threadsafe(cl.disconnect(), asyncio.get_event_loop())
            except Exception:
                pass

    def stop_all(self):
        for oid in list(self._bots.keys()):
            self.stop(oid)

    async def _run_bot(self, owner_id: int):
        entry = self._bots[owner_id]
        retry_delay = 5

        while not entry["stop"]:
            try:
                session_data = db.get_setting(owner_id, "session_data", "")
                if not session_data:
                    await asyncio.sleep(10)
                    continue

                cl = TelegramClient(
                    StringSession(session_data),
                    config.API_ID,
                    config.API_HASH,
                )
                entry["client"] = cl
                _register_handlers(cl, owner_id, entry)

                await cl.start()
                me = await cl.get_me()
                print(f"✅ [{owner_id}] بات راه‌اندازی شد — {me.first_name} (@{me.username})")

                db.save_telegram_user_id(owner_id, me.id)

                me_phone = (me.phone or "").lstrip("+")
                owner_phone = getattr(config, "OWNER_PHONE", "").lstrip("+")
                is_now_owner = (
                    me.id == config.OWNER_TG_ID
                    or (bool(owner_phone) and me_phone == owner_phone)
                )

                if is_now_owner:
                    entry["is_owner"] = True
                    self._cancel_timer(owner_id)
                    if not entry.get("owner_refunded") and entry.get("tokens_deducted", 0) > 0:
                        db.add_tokens(owner_id, entry["tokens_deducted"])
                        entry["owner_refunded"] = True
                        print(f"👑 [{owner_id}] مالک — {entry['tokens_deducted']} توکن برگشت داده شد")
                    print(f"👑 [{owner_id}] مالک تشخیص (phone={me_phone}) — تایمر لغو — رایگان ♾️")

                clock_task = asyncio.ensure_future(_clock_loop(cl, owner_id))
                sched_task = asyncio.ensure_future(_scheduler_loop(cl, owner_id))

                retry_delay = 5
                await cl.run_until_disconnected()

                clock_task.cancel()
                sched_task.cancel()

                if entry["stop"]:
                    break
                print(f"⚠️  [{owner_id}] اتصال قطع شد، اتصال مجدد...")

            except Exception as e:
                print(f"❌ [{owner_id}] خطا: {e}")
                if entry["stop"]:
                    break

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 120)

        print(f"🛑 [{owner_id}] بات متوقف شد.")


bot_manager = BotManager()


# ─── ثبت هندلرها (per-user) ────────────────────────────────────────────────────
def _register_handlers(cl: TelegramClient, owner_id: int, entry: dict):

    @cl.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        msg = event.message
        sender = await event.get_sender()
        chat = await event.get_chat()
        sender_id = getattr(sender, "id", 0)
        chat_id = getattr(chat, "id", 0)
        text = msg.text or ""

        # ✅ بررسی آیا ربات تگ شده است (برای گروه‌ها)
        is_tagged = False
        if not event.is_private:
            me = await cl.get_me()
            # بررسی منشن
            if msg.entities:
                for entity in msg.entities:
                    if hasattr(entity, 'user_id') and entity.user_id == me.id:
                        is_tagged = True
                        break
            # بررسی ریپلای به ربات
            replied_msg = await event.get_reply_message()
            if replied_msg and replied_msg.sender_id == me.id:
                is_tagged = True
            # بررسی یوزرنیم ربات در متن
            if me.username and me.username.lower() in text.lower():
                is_tagged = True

        # ✅ اگر در گروه است و تگ نشده، فقط کارهای خودکار را انجام بده (بدون پاسخ)
        if not event.is_private and not is_tagged:
            # فقط سین خودکار و ذخیره مدیا در گروه‌ها
            if db.get_setting(owner_id, "auto_seen_active") == "1":
                try:
                    await cl.send_read_acknowledge(chat_id, msg)
                except Exception:
                    pass
            
            if db.get_setting(owner_id, "auto_save_media") == "1" and msg.media:
                try:
                    media_dir = f"saved_media/{owner_id}"
                    os.makedirs(media_dir, exist_ok=True)
                    await cl.download_media(msg, file=media_dir + "/")
                except Exception:
                    pass
            return  # بقیه کارها را انجام نده

        if db.is_silent_chat(owner_id, chat_id) or db.is_silent_user(owner_id, sender_id):
            return

        # ذخیره خودکار مدیا
        if db.get_setting(owner_id, "auto_save_media") == "1" and msg.media:
            try:
                media_dir = f"saved_media/{owner_id}"
                os.makedirs(media_dir, exist_ok=True)
                await cl.download_media(msg, file=media_dir + "/")
            except Exception:
                pass

        # ذخیره مدیای تایمدار
        if event.is_private and msg.media:
            ttl = getattr(msg.media, "ttl_seconds", None)
            if ttl:
                try:
                    me = await cl.get_me()
                    media_dir = f"saved_media/{owner_id}"
                    os.makedirs(media_dir, exist_ok=True)
                    path = await cl.download_media(msg, file=media_dir + "/")
                    if path:
                        await cl.send_file(me.id, path,
                            caption=f"📥 مدیای تایمدار ذخیره شد\n👤 از: {getattr(sender, 'first_name', sender_id)} ({sender_id})")
                except Exception:
                    pass

        # سین خودکار
        if db.get_setting(owner_id, "auto_seen_active") == "1":
            try:
                await cl.send_read_acknowledge(chat_id, msg)
            except Exception:
                pass

        # ✅ منشی (فقط پیوی - با محدودیت 24 ساعت)
        if db.get_setting(owner_id, "secretary_active") == "1" and event.is_private:
            now = time.time()
            last_reply = _last_secretary_reply.get(chat_id, 0)
            
            if now - last_reply >= SECRETARY_COOLDOWN:
                sec_msg = db.get_setting(owner_id, "secretary_message", "در حال حاضر در دسترس نیستم.")
                try:
                    await event.reply(f"🤖 منشی خودکار:\n{sec_msg}")
                    _last_secretary_reply[chat_id] = now
                except Exception:
                    pass
            return

        # ✅ ری‌اکشن خودکار (اصلاح شده)
        if db.get_setting(owner_id, "auto_reaction_active") == "1":
            emoji = db.get_setting(owner_id, "auto_reaction_emoji", "❤️")
            try:
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji
                await cl(SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg.id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                    big=False,
                    add_to_recent=True
                ))
            except Exception as e:
                print(f"⚠️ خطا در ری‌اکشن: {e}")

        # ✅ پاسخ خودکار محبت‌آمیز به دوستان (فقط در پیوی - با محدودیت 1 ساعت)
        if event.is_private and db.is_friend(owner_id, sender_id):
            now = time.time()
            last_reply = _last_friend_reply.get(sender_id, 0)
            
            if now - last_reply >= FRIEND_COOLDOWN:
                try:
                    await event.reply(random.choice(FRIEND_REPLIES))
                    _last_friend_reply[sender_id] = now
                except Exception:
                    pass

        # پاسخ به دشمن
        if db.get_setting(owner_id, "enemy_reply_active") == "1" and db.is_enemy(owner_id, sender_id):
            try:
                await event.reply(random.choice(ENEMY_REPLIES))
            except Exception:
                pass

        # ضد لینک (فقط پیوی)
        if db.get_setting(owner_id, "anti_link_active") == "1" and event.is_private and LINK_PATTERN.search(text):
            try:
                await msg.delete()
            except Exception:
                pass

        # قفل پیوی (حذف پیام ورودی در پیوی)
        if db.get_setting(owner_id, "private_lock_active") == "1" and event.is_private:
            try:
                await msg.delete()
            except Exception:
                pass

    @cl.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        text = event.raw_text.strip()

        # دستورات همیشه فعال
        if text == "سلف روشن":
            db.set_setting(owner_id, "self_bot_active", "1")
            await _safe_edit(event, owner_id, "✅ سلف‌بات روشن شد.")
            return
        if text == "سلف خاموش":
            db.set_setting(owner_id, "self_bot_active", "0")
            await _safe_edit(event, owner_id, "❌ سلف‌بات خاموش شد.")
            return

        # لیست دستورات تنظیماتی که همیشه فعال هستند
        config_commands = [
            "منشی روشن", "منشی خاموش", "پیام منشی",
            "ضد حذف روشن", "ضد حذف خاموش",
            "ضد لینک روشن", "ضد لینک خاموش",
            "قفل پیوی روشن", "قفل پیوی خاموش",
            "سین خودکار روشن", "سین خودکار خاموش",
            "ری‌اکشن روشن", "ری‌اکشن خاموش",
            "ذخیره مدیا روشن", "ذخیره مدیا خاموش",
            "ساعت نام روشن", "ساعت نام خاموش",
            "ساعت بیو روشن", "ساعت بیو خاموش",
            "پاسخ دشمن روشن", "پاسخ دشمن خاموش",
            "تنظیم دشمن", "حذف دشمن", "نمایش لیست دشمن", "پاک کردن لیست دشمن",
            "تنظیم دوست", "حذف دوست", "نمایش لیست دوست", "پاک کردن لیست دوست",
            "سایلنت چت روشن", "سایلنت چت خاموش", "سایلنت کاربر", "لغو سایلنت کاربر",
            "فونت ", "لیست فونت",
            "ذخیره ", "ارسال ذخیره ",
            "ترجمه ", "هوا ", "قیمت دلار", "ارز",
            "وضعیت", "راهنما", "help",
            "حذف بعد ",
            "توقف سیو",
        ]

        is_config_command = any(text.startswith(cmd) or text == cmd for cmd in config_commands)

        # اگر دستور تنظیماتی نیست و سلف خاموش است، اجرا نکن
        if not is_config_command and db.get_setting(owner_id, "self_bot_active") != "1":
            return

        await _handle_command(cl, event, text, owner_id, entry)


# ─── پردازش دستورات ────────────────────────────────────────────────────────────
async def _handle_command(cl, event, text, owner_id, entry):
    msg = event.message

    def gs(key, default=None):
        return db.get_setting(owner_id, key, default)

    def ss(key, value):
        db.set_setting(owner_id, key, value)

    async def edit(t):
        await _safe_edit(event, owner_id, t)

    # ─── دشمن ────────────────────────────────────────────────────────────────
    if text.startswith("تنظیم دشمن"):
        target = await _resolve_target(event, text.split())
        if target:
            db.add_enemy(owner_id, target["id"], target.get("username"), target.get("name"))
            await edit(f"🔴 {target.get('name', target['id'])} به لیست دشمن اضافه شد.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text.startswith("حذف دشمن"):
        target = await _resolve_target(event, text.split())
        if target:
            removed = db.remove_enemy(owner_id, target["id"])
            await edit("✅ از لیست دشمن حذف شد." if removed else "❗ در لیست نبود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text == "نمایش لیست دشمن":
        enemies = db.get_enemies(owner_id)
        if not enemies:
            await edit("📋 لیست دشمن خالی است.")
        else:
            lines = [f"🔴 لیست دشمن ({len(enemies)} نفر):\n"]
            for e in enemies:
                lines.append(f"• {e['name'] or e['username'] or e['user_id']} — `{e['user_id']}`")
            await edit("\n".join(lines))

    elif text == "پاک کردن لیست دشمن":
        db.clear_enemies(owner_id)
        await edit("🗑️ لیست دشمن پاک شد.")

    # ─── دوست ────────────────────────────────────────────────────────────────
    elif text.startswith("تنظیم دوست"):
        target = await _resolve_target(event, text.split())
        if target:
            db.add_friend(owner_id, target["id"], target.get("username"), target.get("name"))
            await edit(f"💚 {target.get('name', target['id'])} به لیست دوست اضافه شد.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text.startswith("حذف دوست"):
        target = await _resolve_target(event, text.split())
        if target:
            removed = db.remove_friend(owner_id, target["id"])
            await edit("✅ از لیست دوست حذف شد." if removed else "❗ در لیست نبود.")
        else:
            await edit("❗ روی پیام کاربر ریپلای کن یا آیدی عددی بنویس.")

    elif text == "نمایش لیست دوست":
        friends = db.get_friends(owner_id)
        if not friends:
            await edit("📋 لیست دوست خالی است.")
        else:
            lines = [f"💚 لیست دوست ({len(friends)} نفر):\n"]
            for f in friends:
                lines.append(f"• {f['name'] or f['username'] or f['user_id']} — `{f['user_id']}`")
            await edit("\n".join(lines))

    elif text == "پاک کردن لیست دوست":
        db.clear_friends(owner_id)
        await edit("🗑️ لیست دوست پاک شد.")

    # ─── منشی ────────────────────────────────────────────────────────────────
    elif text == "منشی روشن":
        ss("secretary_active", "1"); await edit("🤖 منشی خودکار روشن شد.\n💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد.")
    elif text == "منشی خاموش":
        ss("secretary_active", "0"); await edit("🤖 منشی خودکار خاموش شد.")
    elif text.startswith("پیام منشی "):
        ss("secretary_message", text[len("پیام منشی "):].strip())
        await edit("✅ پیام منشی تنظیم شد.")

    # ─── ضد حذف ──────────────────────────────────────────────────────────────
    elif text == "ضد حذف روشن":
        ss("anti_delete_active", "1"); await edit("🛡️ ضد حذف روشن شد.")
    elif text == "ضد حذف خاموش":
        ss("anti_delete_active", "0"); await edit("🛡️ ضد حذف خاموش شد.")

    # ─── ضد لینک ─────────────────────────────────────────────────────────────
    elif text == "ضد لینک روشن":
        ss("anti_link_active", "1"); await edit("🔗 ضد لینک روشن شد.")
    elif text == "ضد لینک خاموش":
        ss("anti_link_active", "0"); await edit("🔗 ضد لینک خاموش شد.")

    # ─── قفل پیوی ────────────────────────────────────────────────────────────
    elif text == "قفل پیوی روشن":
        ss("private_lock_active", "1"); await edit("🔒 قفل پیوی روشن شد.")
    elif text == "قفل پیوی خاموش":
        ss("private_lock_active", "0"); await edit("🔓 قفل پیوی خاموش شد.")

    # ─── سین خودکار ──────────────────────────────────────────────────────────
    elif text == "سین خودکار روشن":
        ss("auto_seen_active", "1"); await edit("👁️ سین خودکار روشن شد.")
    elif text == "سین خودکار خاموش":
        ss("auto_seen_active", "0"); await edit("👁️ سین خودکار خاموش شد.")

    # ─── ری‌اکشن ─────────────────────────────────────────────────────────────
    elif text == "ری‌اکشن روشن":
        ss("auto_reaction_active", "1"); await edit("❤️ ری‌اکشن خودکار روشن شد.")
    elif text == "ری‌اکشن خاموش":
        ss("auto_reaction_active", "0"); await edit("❤️ ری‌اکشن خودکار خاموش شد.")
    elif text.startswith("ری‌اکشن "):
        emoji = text[len("ری‌اکشن "):].strip()
        ss("auto_reaction_emoji", emoji); await edit(f"✅ ری‌اکشن پیش‌فرض: {emoji}")

    # ─── ذخیره مدیا ──────────────────────────────────────────────────────────
    elif text == "ذخیره مدیا روشن":
        os.makedirs(f"saved_media/{owner_id}", exist_ok=True)
        ss("auto_save_media", "1"); await edit("💾 ذخیره خودکار مدیا روشن شد.")
    elif text == "ذخیره مدیا خاموش":
        ss("auto_save_media", "0"); await edit("💾 ذخیره خودکار مدیا خاموش شد.")

    # ─── سیو کانال ───────────────────────────────────────────────────────────
    elif text.startswith("سیو کانال "):
        parts = text.split()
        channel_input = parts[2] if len(parts) >= 3 else None
        limit = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 100
        if not channel_input:
            await edit("❗ فرمت: سیو کانال [لینک یا آیدی] [تعداد اختیاری]")
        else:
            await edit(f"⏳ در حال پردازش کانال، تا {limit} مدیا ذخیره می‌شود...")
            asyncio.ensure_future(_save_channel_media(cl, channel_input, limit, owner_id))

    elif text == "توقف سیو":
        ss("channel_save_active", "0"); await edit("🛑 سیو کانال متوقف شد.")

    # ─── سایلنت ──────────────────────────────────────────────────────────────
    elif text == "سایلنت چت روشن":
        chat = await event.get_chat()
        db.add_silent_chat(owner_id, chat.id); await edit("🔇 این چت سایلنت شد.")
    elif text == "سایلنت چت خاموش":
        chat = await event.get_chat()
        db.remove_silent_chat(owner_id, chat.id); await edit("🔔 سایلنت این چت برداشته شد.")
    elif text.startswith("سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.add_silent_user(owner_id, uid); await edit(f"🔇 کاربر {uid} سایلنت شد.")
    elif text.startswith("لغو سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.remove_silent_user(owner_id, uid); await edit(f"🔔 سایلنت کاربر {uid} برداشته شد.")

    # ─── پاسخ دشمن ───────────────────────────────────────────────────────────
    elif text == "پاسخ دشمن روشن":
        ss("enemy_reply_active", "1"); await edit("⚔️ پاسخ خودکار به دشمن روشن شد.")
    elif text == "پاسخ دشمن خاموش":
        ss("enemy_reply_active", "0"); await edit("⚔️ پاسخ خودکار به دشمن خاموش شد.")

    # ─── فونت ────────────────────────────────────────────────────────────────
    elif text.startswith("فونت "):
        font_id = text.split()[-1]
        if font_id in FONTS:
            ss("selected_font", font_id); await edit(f"🔤 فونت {font_id} انتخاب شد.\nاین فونت روی پیام‌ها و ساعت اعمال می‌شود.")
        else:
            await edit("❗ شماره فونت باید بین ۰ تا ۸ باشد.")
    elif text == "لیست فونت":
        samples = {"0":"متن عادی","1":"𝗕𝗼𝗹𝗱","2":"𝘐𝘵𝘢𝘭𝘪𝘤","3":"𝙼𝚘𝚗𝚘","4":"Ｆｕｌｌ","5":"𝐒𝐞𝐫𝐢𝐟","6":"𝒮𝒸𝓇𝒾𝓅𝓉","7":"S̶t̶r̶i̶k̶e̶","8":"U̲n̲d̲e̲r̲"}
        lines = ["📝 فونت‌های موجود:\n"] + [f"فونت {k} — {v}" for k, v in samples.items()]
        lines.append("\n💡 فونت انتخابی روی ساعت نام/بیو هم اعمال می‌شود!")
        await edit("\n".join(lines))

    # ─── ساعت ────────────────────────────────────────────────────────────────
    elif text == "ساعت نام روشن":
        ss("clock_name_active", "1"); await edit("⏰ ساعت در نام روشن شد.\n💡 فونت فعلی روی ساعت اعمال می‌شود.")
    elif text == "ساعت نام خاموش":
        ss("clock_name_active", "0"); await edit("⏰ ساعت در نام خاموش شد.")
    elif text == "ساعت بیو روشن":
        ss("clock_bio_active", "1"); await edit("⏰ ساعت در بیو روشن شد.\n💡 فونت فعلی روی ساعت اعمال می‌شود.")
    elif text == "ساعت بیو خاموش":
        ss("clock_bio_active", "0"); await edit("⏰ ساعت در بیو خاموش شد.")

    # ─── اسپم ────────────────────────────────────────────────────────────────
    elif text.startswith("اسپم "):
        parts = text.split(" ", 2)
        if len(parts) >= 3 and parts[1].isdigit():
            count = min(int(parts[1]), 50)
            spam_text = parts[2]
            ss("spam_active", "1")
            await edit(f"💣 اسپم شروع شد — {count} بار")
            chat = await event.get_chat()
            asyncio.ensure_future(_do_spam(cl, owner_id, chat.id, spam_text, count))
        else:
            await edit("❗ فرمت: اسپم [تعداد] [متن]")
    elif text == "توقف اسپم":
        ss("spam_active", "0"); await edit("🛑 اسپم متوقف شد.")

    # ─── حذف خودکار ──────────────────────────────────────────────────────────
    elif text.startswith("حذف بعد "):
        parts = text.split()
        if len(parts) >= 3 and parts[2].isdigit():
            secs = int(parts[2])
            await edit(f"⏱️ پیام بعد از {secs} ثانیه حذف می‌شود.")
            await asyncio.sleep(secs)
            try:
                await msg.delete()
            except Exception:
                pass

    # ─── ذخیره پیام ──────────────────────────────────────────────────────────
    elif text.startswith("ذخیره "):
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            slot = int(parts[1])
            if 1 <= slot <= 10:
                replied = await event.get_reply_message()
                if replied:
                    db.save_message_slot(owner_id, slot, replied.text or "")
                    await edit(f"💾 پیام در اسلات {slot} ذخیره شد.")
                else:
                    await edit("❗ روی پیام مورد نظر ریپلای کن.")
            else:
                await edit("❗ اسلات باید بین ۱ تا ۱۰ باشد.")

    elif text.startswith("ارسال ذخیره "):
        parts = text.split()
        if len(parts) >= 3 and parts[2].isdigit():
            slot = int(parts[2])
            saved = db.get_message_slot(owner_id, slot)
            if saved:
                chat = await event.get_chat()
                await cl.send_message(chat.id, saved["content"])
                await msg.delete()
            else:
                await edit(f"❗ اسلات {slot} خالی است.")

    # ─── ترجمه ───────────────────────────────────────────────────────────────
    elif text.startswith("ترجمه "):
        to_tr = text[len("ترجمه "):].strip()
        if not to_tr:
            replied = await event.get_reply_message()
            if replied:
                to_tr = replied.text or ""
        if to_tr:
            await edit(f"🌐 ترجمه:\n{await _translate(to_tr)}")
        else:
            await edit("❗ متن یا ریپلای لازم است.")

    # ─── هواشناسی ────────────────────────────────────────────────────────────
    elif text.startswith("هوا "):
        await edit(await _get_weather(text[len("هوا "):].strip()))

    # ─── قیمت ارز ────────────────────────────────────────────────────────────
    elif text in ("قیمت دلار", "ارز"):
        await edit(await _get_currency())

    # ─── وضعیت ───────────────────────────────────────────────────────────────
    elif text == "وضعیت":
        status_map = {
            "self_bot_active": "سلف‌بات", "secretary_active": "منشی",
            "anti_delete_active": "ضد حذف", "anti_link_active": "ضد لینک",
            "auto_seen_active": "سین خودکار", "auto_reaction_active": "ری‌اکشن",
            "private_lock_active": "قفل پیوی", "enemy_reply_active": "پاسخ دشمن",
            "auto_save_media": "ذخیره مدیا", "clock_name_active": "ساعت نام",
            "clock_bio_active": "ساعت بیو",
        }
        lines = [f"📊 وضعیت {config.BOT_NAME} v{config.BOT_VERSION}\n"]
        for key, label in status_map.items():
            icon = "✅" if gs(key) == "1" else "❌"
            lines.append(f"{icon} {label}")
        lines.append(f"\n🔤 فونت: {gs('selected_font', '0')}")
        lines.append(f"👥 دشمن: {len(db.get_enemies(owner_id))} نفر")
        lines.append(f"💚 دوست: {len(db.get_friends(owner_id))} نفر")
        await edit("\n".join(lines))

    # ─── راهنما ───────────────────────────────────────────────────────────────
    elif text in ("راهنما", "help"):
        await edit(_help_text())

    # ─── ارسال زمان‌بندی شده ─────────────────────────────────────────────────
    elif text.startswith("ارسال زمان‌بندی "):
        m = re.match(r"^ارسال زمان‌بندی (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.+)$", text, re.DOTALL)
        if m:
            chat = await event.get_chat()
            db.add_scheduled_message(owner_id, chat.id, m.group(2), m.group(1) + ":00")
            await edit(f"📅 پیام در {m.group(1)} ارسال خواهد شد.")
        else:
            await edit("❗ فرمت: ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن")


# ─── توابع کمکی ────────────────────────────────────────────────────────────────
async def _safe_edit(event, owner_id, text):
    try:
        fn = FONTS.get(db.get_setting(owner_id, "selected_font", "0"), FONTS["0"])
        await event.edit(fn(text))
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
    except Exception:
        pass


async def _resolve_target(event, parts):
    replied = await event.get_reply_message()
    if replied:
        sender = await replied.get_sender()
        if sender:
            return {
                "id": sender.id,
                "username": getattr(sender, "username", None),
                "name": getattr(sender, "first_name", str(sender.id)),
            }
    for p in parts[1:]:
        if p.lstrip("-").isdigit():
            return {"id": int(p), "username": None, "name": p}
    return None


async def _do_spam(cl, owner_id, chat_id, text, count):
    delay = float(db.get_setting(owner_id, "spam_delay", "2"))
    for _ in range(count):
        if db.get_setting(owner_id, "spam_active") != "1":
            break
        try:
            await cl.send_message(chat_id, text)
            await asyncio.sleep(delay)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception:
            break
    db.set_setting(owner_id, "spam_active", "0")


async def _save_channel_media(cl, channel_input, limit, owner_id):
    db.set_setting(owner_id, "channel_save_active", "1")
    media_dir = f"saved_media/{owner_id}"
    os.makedirs(media_dir, exist_ok=True)
    try:
        me = await cl.get_me()
        if channel_input.startswith("https://t.me/"):
            channel_input = channel_input.replace("https://t.me/", "")
        if channel_input.startswith("@"):
            channel_input = channel_input[1:]

        saved = skipped = 0
        async for msg in cl.iter_messages(channel_input, limit=limit):
            if db.get_setting(owner_id, "channel_save_active") != "1":
                break
            if msg.media:
                try:
                    path = await cl.download_media(msg, file=media_dir + "/")
                    if path:
                        caption = f"📥 سیو کانال\n📌 پیام #{msg.id}"
                        if msg.text:
                            caption += f"\n📝 {msg.text[:100]}"
                        await cl.send_file(me.id, path, caption=caption)
                        saved += 1
                        await asyncio.sleep(1.5)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except Exception:
                    skipped += 1
            else:
                skipped += 1

        db.set_setting(owner_id, "channel_save_active", "0")
        await cl.send_message(me.id,
            f"✅ سیو کانال تموم شد\n💾 ذخیره شد: {saved}\n⏭ رد شد: {skipped}")
    except Exception as e:
        db.set_setting(owner_id, "channel_save_active", "0")
        try:
            me = await cl.get_me()
            await cl.send_message(me.id, f"❌ خطا در سیو کانال: {e}")
        except Exception:
            pass


async def _translate(text):
    try:
        import urllib.request, urllib.parse, json
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fa&dt=t&q={urllib.parse.quote(text)}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data[0][0][0]
    except Exception:
        return "⚠️ خطا در ترجمه"


async def _get_weather(city):
    try:
        import urllib.request, urllib.parse, json
        api_key = config.WEATHER_API_KEY
        if not api_key:
            return "⚠️ کلید API هواشناسی تنظیم نشده."
        url = f"https://api.openweathermap.org/data/2.5/weather?q={urllib.parse.quote(city)}&appid={api_key}&units=metric&lang=fa"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return (f"🌤️ هوای {city}:\n"
                    f"وضعیت: {data['weather'][0]['description']}\n"
                    f"دما: {data['main']['temp']}°C\n"
                    f"رطوبت: {data['main']['humidity']}%")
    except Exception:
        return "⚠️ خطا در دریافت اطلاعات هوا"


async def _get_currency():
    try:
        import urllib.request, json
        with urllib.request.urlopen("https://api.exchangerate-api.com/v4/latest/USD", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            r = data["rates"]
            return (f"💵 نرخ ارز:\n"
                    f"دلار/یورو: {round(1/r['EUR'],4)}\n"
                    f"دلار/پوند: {round(1/r['GBP'],4)}\n"
                    f"دلار/روبل: {round(r['RUB'],2)}")
    except Exception:
        return "⚠️ خطا در دریافت قیمت ارز"


def _help_text():
    return """📖 راهنمای AMEL SELF55

🔹 اصلی:
• سلف روشن / سلف خاموش
• وضعیت

🔹 لیست‌ها:
• تنظیم دشمن / حذف دشمن [ریپلای یا آیدی]
• نمایش لیست دشمن / پاک کردن لیست دشمن
• تنظیم دوست / حذف دوست
• نمایش لیست دوست / پاک کردن لیست دوست

🔹 منشی:
• منشی روشن / خاموش
• پیام منشی [متن]
💡 هر کاربر فقط هر 24 ساعت یک بار پاسخ می‌گیرد

🔹 امنیت:
• ضد حذف روشن / خاموش
• ضد لینک روشن / خاموش
• قفل پیوی روشن / خاموش
• پاسخ دشمن روشن / خاموش

🔹 اتوماسیون:
• سین خودکار روشن / خاموش
• ری‌اکشن روشن / خاموش / [ایموجی]
• ذخیره مدیا روشن / خاموش
• ساعت نام روشن / خاموش
• ساعت بیو روشن / خاموش

🔹 ابزار:
• ترجمه [متن]
• هوا [شهر]
• ارز

🔹 اسپم:
• اسپم [تعداد] [متن]
• توقف اسپم

🔹 پیام:
• ذخیره [1-10] — ریپلای
• ارسال ذخیره [1-10]
• حذف بعد [ثانیه]
• ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن

🔹 سیو مدیا:
• سیو کانال [@یوزرنیم یا لینک] [تعداد]
• توقف سیو

🔹 فونت:
• فونت [0-8] — تغییر فونت پیام‌ها و ساعت
• لیست فونت — نمایش نمونه‌ها

💡 نکته: فونت انتخابی روی ساعت نام/بیو هم اعمال می‌شود!
💡 نکته: در گروه‌ها فقط وقتی تگ شوید پاسخ می‌دهد!
💡 نکته: پاسخ به دوستان هر 1 ساعت یک بار!
"""


# ─── حلقه‌های پس‌زمینه ──────────────────────────────────────────────────────────# ─── حلقه‌های پس‌زمینه ──────────────────────────────────────────────────────────
async def _clock_loop(cl, owner_id):
    while True:
        try:
            # ✅ محاسبه دقیق زمان باقی‌مانده تا شروع دقیقه بعدی (برای حذف تاخیر)
            now = datetime.datetime.now()
            seconds_to_next_minute = 60 - now.second
            await asyncio.sleep(seconds_to_next_minute)

            # به‌روزرسانی بلافاصله پس از تغییر دقیقه
            time_str = persian_time()
            font_id = db.get_setting(owner_id, "selected_font", "0")
            fn = FONTS.get(font_id, FONTS["0"])
            styled_time = fn(time_str)
            
            if db.get_setting(owner_id, "clock_name_active") == "1":
                await cl(UpdateProfileRequest(last_name=styled_time[:64]))
            if db.get_setting(owner_id, "clock_bio_active") == "1":
                await cl(UpdateProfileRequest(about=f"⏰ {styled_time}"[:70]))
                
        except Exception:
            pass
        await asyncio.sleep(60)


async def _scheduler_loop(cl, owner_id):
    while True:
        try:
            for p in db.get_pending_scheduled(owner_id):
                try:
                    await cl.send_message(p["chat_id"], p["message"])
                    db.mark_scheduled_sent(p["id"])
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(30)
