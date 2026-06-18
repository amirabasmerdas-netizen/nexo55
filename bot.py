import asyncio
import re
import os
import datetime
import random
import threading
import time
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import FloodWaitError, RPCError
import database as db
import config
from texts import ENEMY_REPLIES, FRIEND_REPLIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _make_font_table(base_upper: int, base_lower: int) -> dict:
    table = {}
    for i, ch in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
        table[ord(ch)] = chr(base_upper + i)
    for i, ch in enumerate('abcdefghijklmnopqrstuvwxyz'):
        table[ord(ch)] = chr(base_lower + i)
    return table


_FONT_TABLES = {
    '1': _make_font_table(0x1D5D4, 0x1D5EE),
    '2': _make_font_table(0x1D608, 0x1D622),
    '3': _make_font_table(0x1D670, 0x1D68A),
    '4': _make_font_table(0xFF21,  0xFF41),
    '5': _make_font_table(0x1D400, 0x1D41A),
    '6': _make_font_table(0x1D4D0, 0x1D4EA),
}

FONTS = {
    '0': lambda t: t,
    '1': lambda t: t.translate(_FONT_TABLES['1']),
    '2': lambda t: t.translate(_FONT_TABLES['2']),
    '3': lambda t: t.translate(_FONT_TABLES['3']),
    '4': lambda t: t.translate(_FONT_TABLES['4']),
    '5': lambda t: t.translate(_FONT_TABLES['5']),
    '6': lambda t: t.translate(_FONT_TABLES['6']),
    '7': lambda t: ''.join(c + '\u0336' for c in t),
    '8': lambda t: ''.join(c + '\u0332' for c in t),
}

LINK_PATTERN = re.compile(r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|www\.\S+)", re.IGNORECASE)

_last_secretary_reply = {}
_last_friend_reply = {}
SECRETARY_COOLDOWN = 86400
FRIEND_COOLDOWN = 3600

_settings_cache = {}
_settings_cache_time = {}
_SETTINGS_TTL = 30


def _gs_cached(owner_id: int, key: str, default=None):
    now = time.time()
    k = f"{owner_id}:{key}"
    if k in _settings_cache and now - _settings_cache_time.get(k, 0) < _SETTINGS_TTL:
        return _settings_cache[k]
    val = db.get_setting(owner_id, key, default)
    _settings_cache[k] = val
    _settings_cache_time[k] = now
    return val


def _ss_cached(owner_id: int, key: str, value):
    db.set_setting(owner_id, key, value)
    k = f"{owner_id}:{key}"
    _settings_cache[k] = str(value)
    _settings_cache_time[k] = time.time()


def _invalidate_settings(owner_id: int):
    keys_to_del = [k for k in _settings_cache if k.startswith(f"{owner_id}:")]
    for k in keys_to_del:
        _settings_cache.pop(k, None)
        _settings_cache_time.pop(k, None)


def persian_time():
    iran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    now = datetime.datetime.now(iran_tz)
    return f"{now.hour:02d}:{now.minute:02d}"


def _apply_font(owner_id, text):
    font_id = _gs_cached(owner_id, "selected_font", "0")
    return FONTS.get(font_id, FONTS["0"])(text)


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
        entry = {
            "client": None, "task": None, "stop": False,
            "is_owner": is_owner, "tokens_deducted": tokens_deducted, "owner_refunded": False,
        }
        self._bots[owner_id] = entry
        task = asyncio.run_coroutine_threadsafe(self._run_bot(owner_id), loop)
        entry["task"] = task
        if config.BOT_TOKEN and not is_owner:
            self._cancel_timer(owner_id)
            timer = threading.Timer(config.SESSION_HOURS * 3600, self._session_expired, args=[owner_id])
            timer.daemon = True
            timer.start()
            self._timers[owner_id] = timer
        return True

    def _session_expired(self, owner_id: int):
        logger.info(f"⏰ [{owner_id}] جلسه سلف‌بات به پایان رسید!")
        self.stop(owner_id)
        db.set_setting(owner_id, "self_bot_active", "0")

    def stop(self, owner_id: int):
        self._cancel_timer(owner_id)
        entry = self._bots.get(owner_id)
        if not entry:
            return
        entry["stop"] = True
        cl = entry.get("client")
        if cl and cl.is_connected():
            try:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(cl.disconnect(), loop)
            except Exception:
                pass

    def stop_all(self):
        for oid in list(self._bots.keys()):
            self.stop(oid)

    async def _run_bot(self, owner_id: int):
        entry = self._bots[owner_id]
        retry_delay = 3
        consecutive_failures = 0
        while not entry["stop"]:
            try:
                session_data = db.get_session(owner_id) or ""
                if not session_data:
                    await asyncio.sleep(10)
                    continue
                cl = TelegramClient(
                    StringSession(session_data),
                    config.API_ID, config.API_HASH,
                    connection_retries=3,
                    retry_delay=2,
                    timeout=20,
                    auto_reconnect=True,
                    flood_sleep_threshold=0,
                    device_model="AMEL SELF55",
                    system_version="1.0.0",
                    app_version="1.2.0",
                    receive_updates=True,
                )
                entry["client"] = cl
                _register_handlers(cl, owner_id, entry)
                try:
                    await cl.connect()
                    if not cl.is_connected():
                        await asyncio.sleep(3)
                        continue
                except Exception as e:
                    error_msg = str(e).lower()
                    if any(x in error_msg for x in ["invalid", "auth", "not found", "unauthorized"]):
                        db.delete_session(owner_id)
                        db.set_setting(owner_id, "logged_in", "0")
                        entry["stop"] = True
                        break
                    consecutive_failures += 1
                    if consecutive_failures > 5:
                        entry["stop"] = True
                        break
                    await asyncio.sleep(min(consecutive_failures * 5, 30))
                    continue
                consecutive_failures = 0
                try:
                    me = await cl.get_me()
                    if not me:
                        await asyncio.sleep(5)
                        continue
                except Exception:
                    await asyncio.sleep(5)
                    continue
                logger.info(f"✅ [{owner_id}] سلف‌بات راه‌اندازی شد — {me.first_name} (@{me.username})")
                db.save_telegram_user_id(owner_id, me.id)
                me_phone = (me.phone or "").lstrip("+")
                owner_phone = getattr(config, "OWNER_PHONE", "").lstrip("+")
                is_now_owner = (
                    me.id == config.OWNER_TG_ID
                    or (bool(owner_phone) and me_phone == owner_phone)
                    or me.username == getattr(config, "OWNER_USERNAME", "")
                )
                if is_now_owner:
                    entry["is_owner"] = True
                    self._cancel_timer(owner_id)
                    if not entry.get("owner_refunded") and entry.get("tokens_deducted", 0) > 0:
                        db.add_tokens(owner_id, entry["tokens_deducted"])
                        entry["owner_refunded"] = True
                clock_task = asyncio.ensure_future(_clock_loop(cl, owner_id))
                sched_task = asyncio.ensure_future(_scheduler_loop(cl, owner_id))
                keep_alive_task = asyncio.ensure_future(_keep_alive_loop(cl, owner_id))
                retry_delay = 3
                try:
                    await cl.run_until_disconnected()
                except Exception as e:
                    logger.error(f"❌ [{owner_id}] {e}")
                clock_task.cancel()
                sched_task.cancel()
                keep_alive_task.cancel()
                if entry["stop"]:
                    break
                logger.warning(f"⚠️ [{owner_id}] اتصال قطع شد، بازاتصال...")
            except Exception as e:
                logger.error(f"❌ [{owner_id}] خطا: {e}")
                if entry["stop"]:
                    break
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 30)
        logger.info(f"🛑 [{owner_id}] بات متوقف شد.")


bot_manager = BotManager()


async def _keep_alive_loop(cl, owner_id):
    while True:
        try:
            if cl and cl.is_connected():
                await cl.get_me()
        except Exception:
            pass
        await asyncio.sleep(30)


async def _clock_loop(cl, owner_id):
    while True:
        try:
            name_active = _gs_cached(owner_id, "clock_name_active") == "1"
            bio_active = _gs_cached(owner_id, "clock_bio_active") == "1"
            if name_active or bio_active:
                t = persian_time()
                fn = FONTS.get(_gs_cached(owner_id, "selected_font", "0"), FONTS["0"])
                clock_str = fn(t)
                if name_active:
                    try:
                        me = await cl.get_me()
                        base_name = (me.first_name or "").split(" ⏰")[0].strip()
                        await cl(UpdateProfileRequest(first_name=f"{base_name} ⏰{clock_str}"))
                    except FloodWaitError as e:
                        await asyncio.sleep(min(e.seconds, 60))
                    except Exception:
                        pass
                if bio_active:
                    try:
                        await cl(UpdateProfileRequest(about=f"⏰ {clock_str}"))
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(60)


async def _scheduler_loop(cl, owner_id):
    while True:
        try:
            pending = db.get_pending_scheduled(owner_id)
            for msg in pending:
                try:
                    await cl.send_message(msg["chat_id"], msg["message"])
                    db.mark_scheduled_sent(msg["id"])
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(30)


def _register_handlers(cl: TelegramClient, owner_id: int, entry: dict):

    @cl.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        try:
            if _gs_cached(owner_id, "self_bot_active") != "1":
                return
            msg = event.message
            sender = await event.get_sender()
            chat = await event.get_chat()
            sender_id = getattr(sender, "id", 0)
            chat_id = getattr(chat, "id", 0)
            text = msg.text or ""

            is_tagged = False
            if not event.is_private:
                me = await cl.get_me()
                if msg.entities:
                    for entity in msg.entities:
                        if hasattr(entity, 'user_id') and entity.user_id == me.id:
                            is_tagged = True
                            break
                if not is_tagged:
                    replied_msg = await event.get_reply_message()
                    if replied_msg and replied_msg.sender_id == me.id:
                        is_tagged = True
                if not is_tagged and me.username and me.username.lower() in text.lower():
                    is_tagged = True

            if not event.is_private and not is_tagged:
                tasks = []
                if _gs_cached(owner_id, "auto_seen_active") == "1":
                    tasks.append(cl.send_read_acknowledge(chat_id, msg))
                if _gs_cached(owner_id, "auto_save_media") == "1" and msg.media:
                    media_dir = f"saved_media/{owner_id}"
                    os.makedirs(media_dir, exist_ok=True)
                    tasks.append(cl.download_media(msg, file=media_dir + "/"))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if _gs_cached(owner_id, "enemy_reply_active") == "1" and db.is_enemy(owner_id, sender_id):
                    try:
                        await event.reply(random.choice(ENEMY_REPLIES))
                    except Exception:
                        pass
                if _gs_cached(owner_id, "auto_reaction_active") == "1":
                    emoji = _gs_cached(owner_id, "auto_reaction_emoji", "❤️")
                    try:
                        from telethon.tl.functions.messages import SendReactionRequest
                        from telethon.tl.types import ReactionEmoji
                        await cl(SendReactionRequest(peer=chat_id, msg_id=msg.id,
                            reaction=[ReactionEmoji(emoticon=emoji)], big=False, add_to_recent=True))
                    except Exception:
                        pass
                return

            if db.is_silent_chat(owner_id, chat_id) or db.is_silent_user(owner_id, sender_id):
                return

            tasks = []
            if _gs_cached(owner_id, "auto_seen_active") == "1":
                tasks.append(cl.send_read_acknowledge(chat_id, msg))
            if _gs_cached(owner_id, "auto_save_media") == "1" and msg.media:
                media_dir = f"saved_media/{owner_id}"
                os.makedirs(media_dir, exist_ok=True)
                tasks.append(cl.download_media(msg, file=media_dir + "/"))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

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
                                caption=f"📥 مدیای تایمدار\n👤 از: {getattr(sender, 'first_name', sender_id)}")
                    except Exception:
                        pass

            if _gs_cached(owner_id, "secretary_active") == "1" and event.is_private:
                now = time.time()
                if now - _last_secretary_reply.get(chat_id, 0) >= SECRETARY_COOLDOWN:
                    sec_msg = _gs_cached(owner_id, "secretary_message", "در حال حاضر در دسترس نیستم.")
                    try:
                        await event.reply(f"🤖 منشی خودکار:\n{sec_msg}")
                        _last_secretary_reply[chat_id] = now
                    except Exception:
                        pass
                return

            if _gs_cached(owner_id, "auto_reaction_active") == "1":
                emoji = _gs_cached(owner_id, "auto_reaction_emoji", "❤️")
                try:
                    from telethon.tl.functions.messages import SendReactionRequest
                    from telethon.tl.types import ReactionEmoji
                    await cl(SendReactionRequest(peer=chat_id, msg_id=msg.id,
                        reaction=[ReactionEmoji(emoticon=emoji)], big=False, add_to_recent=True))
                except Exception:
                    pass

            if event.is_private and db.is_friend(owner_id, sender_id):
                now = time.time()
                if now - _last_friend_reply.get(sender_id, 0) >= FRIEND_COOLDOWN:
                    try:
                        await event.reply(random.choice(FRIEND_REPLIES))
                        _last_friend_reply[sender_id] = now
                    except Exception:
                        pass

            if _gs_cached(owner_id, "enemy_reply_active") == "1" and db.is_enemy(owner_id, sender_id):
                try:
                    await event.reply(random.choice(ENEMY_REPLIES))
                except Exception:
                    pass

            if _gs_cached(owner_id, "anti_link_active") == "1" and event.is_private and LINK_PATTERN.search(text):
                try:
                    await msg.delete()
                except Exception:
                    pass

            if _gs_cached(owner_id, "private_lock_active") == "1" and event.is_private:
                try:
                    await msg.delete()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"❌ on_incoming [{owner_id}]: {e}")

    @cl.on(events.MessageDeleted())
    async def on_deleted(event):
        try:
            if _gs_cached(owner_id, "anti_delete_active") != "1":
                return
            for msg_id in event.deleted_ids:
                db.log_deleted_message(owner_id, event.chat_id, None, "ناشناس", f"پیام {msg_id} حذف شد")
        except Exception:
            pass

    @cl.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        try:
            text = event.raw_text.strip()
            config_cmds = [
                "سلف روشن", "سلف خاموش", "منشی روشن", "منشی خاموش",
                "ضد حذف روشن", "ضد حذف خاموش", "ضد لینک روشن", "ضد لینک خاموش",
                "قفل پیوی روشن", "قفل پیوی خاموش", "سین خودکار روشن", "سین خودکار خاموش",
                "ری‌اکشن روشن", "ری‌اکشن خاموش", "ذخیره مدیا روشن", "ذخیره مدیا خاموش",
                "ساعت نام روشن", "ساعت نام خاموش", "ساعت بیو روشن", "ساعت بیو خاموش",
                "پاسخ دشمن روشن", "پاسخ دشمن خاموش", "تنظیم دشمن", "حذف دشمن",
                "نمایش لیست دشمن", "پاک کردن لیست دشمن", "تنظیم دوست", "حذف دوست",
                "نمایش لیست دوست", "پاک کردن لیست دوست", "سایلنت چت روشن", "سایلنت چت خاموش",
                "فونت ", "لیست فونت", "ذخیره ", "ارسال ذخیره ", "ترجمه ", "هوا ",
                "قیمت دلار", "ارز", "وضعیت", "راهنما", "help", "حذف بعد ", "توقف سیو",
                "اسپم ", "توقف اسپم", "سیو کانال ",
            ]
            is_cmd = any(text.startswith(c) or text == c for c in config_cmds)
            if not is_cmd and _gs_cached(owner_id, "self_bot_active") != "1":
                return
            await _handle_command(cl, event, text, owner_id, entry)
        except Exception as e:
            logger.error(f"❌ on_outgoing [{owner_id}]: {e}")


async def _handle_command(cl, event, text, owner_id, entry):
    msg = event.message

    def gs(key, default=None):
        return _gs_cached(owner_id, key, default)

    def ss(key, value):
        _ss_cached(owner_id, key, value)

    async def edit(t):
        await _safe_edit(event, owner_id, t)

    if text == "سلف روشن":
        ss("self_bot_active", "1")
        await edit("✅ سلف‌بات روشن شد.")
    elif text == "سلف خاموش":
        ss("self_bot_active", "0")
        await edit("❌ سلف‌بات خاموش شد.")
    elif text == "منشی روشن":
        ss("secretary_active", "1")
        await edit("🤖 منشی روشن شد.")
    elif text == "منشی خاموش":
        ss("secretary_active", "0")
        await edit("🤖 منشی خاموش شد.")
    elif text.startswith("پیام منشی "):
        ss("secretary_message", text[len("پیام منشی "):].strip())
        await edit("✅ پیام منشی تنظیم شد.")
    elif text == "ضد حذف روشن":
        ss("anti_delete_active", "1")
        await edit("🛡️ ضد حذف روشن شد.")
    elif text == "ضد حذف خاموش":
        ss("anti_delete_active", "0")
        await edit("🛡️ ضد حذف خاموش شد.")
    elif text == "ضد لینک روشن":
        ss("anti_link_active", "1")
        await edit("🔗 ضد لینک روشن شد.")
    elif text == "ضد لینک خاموش":
        ss("anti_link_active", "0")
        await edit("🔗 ضد لینک خاموش شد.")
    elif text == "قفل پیوی روشن":
        ss("private_lock_active", "1")
        await edit("🔒 قفل پیوی روشن شد.")
    elif text == "قفل پیوی خاموش":
        ss("private_lock_active", "0")
        await edit("🔓 قفل پیوی خاموش شد.")
    elif text == "سین خودکار روشن":
        ss("auto_seen_active", "1")
        await edit("👁️ سین خودکار روشن شد.")
    elif text == "سین خودکار خاموش":
        ss("auto_seen_active", "0")
        await edit("👁️ سین خودکار خاموش شد.")
    elif text == "ری‌اکشن روشن":
        ss("auto_reaction_active", "1")
        await edit("❤️ ری‌اکشن روشن شد.")
    elif text == "ری‌اکشن خاموش":
        ss("auto_reaction_active", "0")
        await edit("❤️ ری‌اکشن خاموش شد.")
    elif text.startswith("ری‌اکشن "):
        emoji = text[len("ری‌اکشن "):].strip()
        ss("auto_reaction_emoji", emoji)
        await edit(f"✅ ری‌اکشن: {emoji}")
    elif text == "ذخیره مدیا روشن":
        os.makedirs(f"saved_media/{owner_id}", exist_ok=True)
        ss("auto_save_media", "1")
        await edit("💾 ذخیره مدیا روشن شد.")
    elif text == "ذخیره مدیا خاموش":
        ss("auto_save_media", "0")
        await edit("💾 ذخیره مدیا خاموش شد.")
    elif text == "ساعت نام روشن":
        ss("clock_name_active", "1")
        await edit("⏰ ساعت نام روشن شد.")
    elif text == "ساعت نام خاموش":
        ss("clock_name_active", "0")
        await edit("⏰ ساعت نام خاموش شد.")
    elif text == "ساعت بیو روشن":
        ss("clock_bio_active", "1")
        await edit("⏰ ساعت بیو روشن شد.")
    elif text == "ساعت بیو خاموش":
        ss("clock_bio_active", "0")
        await edit("⏰ ساعت بیو خاموش شد.")
    elif text == "پاسخ دشمن روشن":
        ss("enemy_reply_active", "1")
        await edit("⚔️ پاسخ دشمن روشن شد.")
    elif text == "پاسخ دشمن خاموش":
        ss("enemy_reply_active", "0")
        await edit("⚔️ پاسخ دشمن خاموش شد.")
    elif text.startswith("تنظیم دشمن"):
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
    elif text == "سایلنت چت روشن":
        chat = await event.get_chat()
        db.add_silent_chat(owner_id, chat.id)
        await edit("🔇 این چت سایلنت شد.")
    elif text == "سایلنت چت خاموش":
        chat = await event.get_chat()
        db.remove_silent_chat(owner_id, chat.id)
        await edit("🔔 سایلنت برداشته شد.")
    elif text.startswith("سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.add_silent_user(owner_id, uid)
        await edit(f"🔇 کاربر {uid} سایلنت شد.")
    elif text.startswith("لغو سایلنت کاربر "):
        uid = int(text.split()[-1])
        db.remove_silent_user(owner_id, uid)
        await edit(f"🔔 سایلنت کاربر {uid} برداشته شد.")
    elif text.startswith("فونت "):
        parts = text.split()
        if len(parts) >= 2:
            last = parts[-1]
            if last.isdigit() and last in FONTS:
                ss("selected_font", last)
                if len(parts) > 2:
                    txt = " ".join(parts[1:-1])
                    converted = FONTS[last](txt)
                    await edit(f"🔤 {converted}\n✅ فونت {last} اعمال شد.")
                else:
                    await edit(f"🔤 فونت {last} انتخاب شد.")
            else:
                await edit("❗ شماره فونت باید ۰ تا ۸ باشد.")
    elif text == "لیست فونت":
        samples = ["متن عادی", "𝗕𝗼𝗹𝗱", "𝘐𝘵𝘢𝘭𝘪𝘤", "𝙼𝚘𝚗𝚘", "Ｆｕｌｌ", "𝐒𝐞𝐫𝐟", "𝒮𝒸𝓇𝓅𝓉", "S̶t̶r̶i̶k̶e̶", "U̲n̲d̲e̲r̲"]
        lines = ["📝 لیست فونت‌ها:\n"]
        for i, s in enumerate(samples):
            lines.append(f"فونت {i} — {s}")
        await edit("\n".join(lines) + "\n\nمثال: فونت امیر 3")
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
        ss("spam_active", "0")
        await edit("🛑 اسپم متوقف شد.")
    elif text.startswith("سیو کانال "):
        parts = text.split()
        channel_input = parts[2] if len(parts) >= 3 else None
        limit = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 100
        if not channel_input:
            await edit("❗ فرمت: سیو کانال [لینک یا آیدی] [تعداد]")
        else:
            await edit(f"⏳ در حال پردازش کانال، تا {limit} مدیا...")
            asyncio.ensure_future(_save_channel_media(cl, channel_input, limit, owner_id))
    elif text == "توقف سیو":
        ss("channel_save_active", "0")
        await edit("🛑 سیو متوقف شد.")
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
    elif text.startswith("هوا "):
        await edit(await _get_weather(text[len("هوا "):].strip()))
    elif text in ("قیمت دلار", "ارز"):
        await edit(await _get_currency())
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
    elif text in ("راهنما", "help"):
        await edit(_help_text())
    elif text.startswith("ارسال زمان‌بندی "):
        m = re.match(r"^ارسال زمان‌بندی (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.+)$", text, re.DOTALL)
        if m:
            chat = await event.get_chat()
            db.add_scheduled_message(owner_id, chat.id, m.group(2), m.group(1) + ":00")
            await edit(f"📅 پیام در {m.group(1)} ارسال خواهد شد.")
        else:
            await edit("❗ فرمت: ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن")


async def _safe_edit(event, owner_id, text):
    try:
        fn = FONTS.get(_gs_cached(owner_id, "selected_font", "0"), FONTS["0"])
        await event.edit(fn(text))
    except FloodWaitError as e:
        wait = min(e.seconds + 1, 30)
        await asyncio.sleep(wait)
        try:
            await event.edit(fn(text))
        except Exception:
            pass
    except Exception:
        pass


async def _resolve_target(event, parts):
    replied = await event.get_reply_message()
    if replied:
        sender = await replied.get_sender()
        if sender:
            return {"id": sender.id, "username": getattr(sender, "username", None),
                    "name": getattr(sender, "first_name", str(sender.id))}
    for p in parts[1:]:
        if p.lstrip("-").isdigit():
            return {"id": int(p), "username": None, "name": p}
    return None


async def _do_spam(cl, owner_id, chat_id, text, count):
    delay = float(_gs_cached(owner_id, "spam_delay", "2"))
    for _ in range(count):
        if _gs_cached(owner_id, "spam_active") != "1":
            break
        try:
            await cl.send_message(chat_id, text)
            await asyncio.sleep(delay)
        except FloodWaitError as e:
            await asyncio.sleep(min(e.seconds + 1, 30))
        except Exception:
            break
    _ss_cached(owner_id, "spam_active", "0")


async def _save_channel_media(cl, channel_input, limit, owner_id):
    _ss_cached(owner_id, "channel_save_active", "1")
    media_dir = f"saved_media/{owner_id}"
    os.makedirs(media_dir, exist_ok=True)
    try:
        me = await cl.get_me()
        if channel_input.startswith("https://t.me/"):
            channel_input = channel_input.replace("https://t.me/", "")
        if channel_input.startswith("@"):
            channel_input = channel_input[1:]
        saved = 0
        async for msg in cl.iter_messages(channel_input, limit=limit):
            if _gs_cached(owner_id, "channel_save_active") != "1":
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
                except Exception:
                    pass
        _ss_cached(owner_id, "channel_save_active", "0")
        await cl.send_message(me.id, f"✅ سیو کانال کامل شد — {saved} فایل ذخیره شد.")
    except Exception as e:
        _ss_cached(owner_id, "channel_save_active", "0")
        logger.error(f"❌ _save_channel_media: {e}")


async def _translate(text: str) -> str:
    import urllib.parse
    import aiohttp
    try:
        encoded = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fa&dt=t&q={encoded}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return "".join(part[0] for part in data[0] if part[0])
    except Exception:
        return "❌ خطا در ترجمه"


async def _get_weather(city: str) -> str:
    import aiohttp
    try:
        api_key = getattr(config, "WEATHER_API_KEY", "")
        if not api_key:
            return "⚠️ WEATHER_API_KEY تنظیم نشده"
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric&lang=fa"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data.get("cod") != 200:
                    return f"❌ شهر '{city}' یافت نشد"
                temp = data["main"]["temp"]
                desc = data["weather"][0]["description"]
                return f"⛅ {city}: {temp}°C — {desc}"
    except Exception:
        return "❌ خطا در دریافت آب‌وهوا"


async def _get_currency() -> str:
    import aiohttp
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                rates = data.get("rates", {})
                irr = rates.get("IRR", 0)
                eur = rates.get("EUR", 0)
                gbp = rates.get("GBP", 0)
                return f"💲 نرخ ارز:\n🇺🇸 دلار: {irr:,.0f} ریال\n🇪🇺 یورو: {1/eur:.4f} دلار\n🇬🇧 پوند: {1/gbp:.4f} دلار"
    except Exception:
        return "❌ خطا در دریافت نرخ ارز"


def _help_text():
    return (
        f"📖 راهنمای {config.BOT_NAME} v{config.BOT_VERSION}\n\n"
        "⚙️ اصلی:\n"
        "سلف روشن/خاموش\nوضعیت\nراهنما\n\n"
        "👥 لیست‌ها:\n"
        "تنظیم دشمن — ریپلای\nحذف دشمن — ریپلای/آیدی\n"
        "نمایش لیست دشمن\nپاک کردن لیست دشمن\n"
        "تنظیم دوست — ریپلای\nحذف دوست\n\n"
        "🤖 منشی:\n"
        "منشی روشن/خاموش\nپیام منشی [متن]\n\n"
        "🛡️ امنیت:\n"
        "ضد حذف روشن/خاموش\nضد لینک روشن/خاموش\n"
        "قفل پیوی روشن/خاموش\nپاسخ دشمن روشن/خاموش\n\n"
        "⚡ اتوماسیون:\n"
        "سین خودکار روشن/خاموش\nری‌اکشن روشن/خاموش\n"
        "ری‌اکشن [ایموجی]\nذخیره مدیا روشن/خاموش\n"
        "ساعت نام روشن/خاموش\nساعت بیو روشن/خاموش\n\n"
        "🔤 فونت:\n"
        "فونت [متن] [شماره]\nلیست فونت\n\n"
        "💣 اسپم:\n"
        "اسپم [تعداد] [متن]\nتوقف اسپم\n\n"
        "🌐 ابزار:\n"
        "ترجمه [متن]\nهوا [شهر]\nارز\n\n"
        "💾 پیام:\n"
        "ذخیره [1-10] — ریپلای\nارسال ذخیره [1-10]\n"
        "حذف بعد [ثانیه]\n"
        "ارسال زمان‌بندی [YYYY-MM-DD HH:MM] متن\n\n"
        "📥 سیو مدیا:\n"
        "سیو کانال [@user] [تعداد]\nتوقف سیو"
    )
