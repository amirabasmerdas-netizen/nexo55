# ══════════════════════════════════════════════════════════════════════════════
# app.py - نسخه کامل با رفع خطای ثبت‌نام و اصلاح حذف سلف
# ══════════════════════════════════════════════════════════════════════════════

import asyncio
import os
import threading
import time as _time
import logging
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
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

# ─── تنظیم لاگ ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── Flask App ──────────────────────────────────────────────────────────────
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
app = Flask(__name__, template_folder=template_dir)
app.secret_key = config.SECRET_KEY

# ─── Init Database ──────────────────────────────────────────────────────────
db.init_tables()

# ─── Error Handlers ─────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "صفحه یافت نشد"}), 404
    return render_template("panel.html", page="error", error="صفحه یافت نشد"), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"ok": False, "error": f"خطای داخلی سرور: {str(e)}"}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    logger.error(f"❌ خطای غیرمنتظره: {e}")
    return jsonify({"ok": False, "error": f"خطای غیرمنتظره: {str(e)}"}), 500

# ─── Event Loop برای Telethon ───────────────────────────────────────────────
_loop = None
_login_clients = {}
_phone_hashes = {}
_phone_numbers = {}

def get_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
    return _loop

def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=60)

# ─── احراز هویت ────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("owner_id"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "وارد نشده‌اید"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def owner_id() -> int:
    return int(session["owner_id"])

# ─── Keep-Alive ─────────────────────────────────────────────────────────────
@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": config.BOT_NAME}), 200

# ─── صفحه اصلی ──────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account:
        session.pop("owner_id", None)
        return redirect(url_for("login"))
    
    has_session = db.get_session(oid) is not None
    balance = db.get_token_balance(oid)
    
    return render_template(
        "panel.html",
        page="panel",
        username=account["username"],
        owner_id=oid,
        balance=balance,
        has_session=has_session,
    )

# ─── ورود ───────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("owner_id"):
        return redirect(url_for("index"))
    
    if request.method == "POST":
        if request.is_json:
            data = request.json
        else:
            data = request.form
        
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        if not username or not password:
            error_msg = "یوزرنیم و رمز الزامی هستند"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("panel.html", page="panel_login")
        
        oid = db.verify_account(username, password)
        if oid is None:
            error_msg = "یوزرنیم یا رمز اشتباه است"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 401
            return render_template("panel.html", page="panel_login")
        
        session["owner_id"] = oid
        db.init_user_settings(oid)
        
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("index"))
    
    return render_template("panel.html", page="panel_login")

# ─── ورود از مسیر /panel-login ──────────────────────────────────────────────
@app.route("/panel-login", methods=["GET"])
def panel_login_page():
    if session.get("owner_id"):
        return redirect(url_for("index"))
    return render_template("panel.html", page="panel_login")

# ─── API: ورود (alias) ──────────────────────────────────────────────────────
@app.route("/api/panel-login", methods=["POST"])
def api_panel_login():
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    
    if not username or not password:
        return jsonify({"ok": False, "error": "یوزرنیم و رمز الزامی هستند"}), 400
    
    oid = db.verify_account(username, password)
    if oid is None:
        return jsonify({"ok": False, "error": "یوزرنیم یا رمز اشتباه است"}), 401
    
    session["owner_id"] = oid
    db.init_user_settings(oid)
    return jsonify({"ok": True})

# ─── ثبت‌نام ─────────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("owner_id"):
        return redirect(url_for("index"))
    
    if request.method == "POST":
        logger.info("📝 درخواست ثبت‌نام دریافت شد")
        
        if request.is_json:
            data = request.json
            logger.info(f"📝 داده‌های JSON: {data}")
        else:
            data = request.form
            logger.info(f"📝 داده‌های Form: {data}")
        
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        logger.info(f"👤 تلاش برای ثبت‌نام با یوزرنیم: {username}")
        
        # ─── اعتبارسنجی ────────────────────────────────────────────────────
        if not username:
            error_msg = "یوزرنیم الزامی است"
            logger.warning(f"❌ ثبت‌نام ناموفق: {error_msg}")
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("panel.html", page="register")
        
        if not password:
            error_msg = "رمز عبور الزامی است"
            logger.warning(f"❌ ثبت‌نام ناموفق: {error_msg}")
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("panel.html", page="register")
        
        if len(username) < 3:
            error_msg = "یوزرنیم باید حداقل ۳ کاراکتر باشد"
            logger.warning(f"❌ ثبت‌نام ناموفق: {error_msg}")
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("panel.html", page="register")
        
        if len(password) < 6:
            error_msg = "رمز عبور باید حداقل ۶ کاراکتر باشد"
            logger.warning(f"❌ ثبت‌نام ناموفق: {error_msg}")
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("panel.html", page="register")
        
        # ─── بررسی تکراری بودن ────────────────────────────────────────────
        try:
            existing = db.get_account_by_username(username)
            if existing:
                error_msg = "این یوزرنیم قبلاً ثبت شده است"
                logger.warning(f"❌ ثبت‌نام ناموفق: {error_msg}")
                if request.is_json:
                    return jsonify({"ok": False, "error": error_msg}), 409
                return render_template("panel.html", page="register")
        except Exception as e:
            logger.error(f"❌ خطا در بررسی تکراری بودن: {e}")
        
        # ─── ایجاد حساب ────────────────────────────────────────────────────
        try:
            oid = db.create_account(username, password)
            if oid is None:
                error_msg = "خطا در ایجاد حساب. لطفاً دوباره تلاش کنید."
                logger.error(f"❌ ثبت‌نام ناموفق: {error_msg}")
                if request.is_json:
                    return jsonify({"ok": False, "error": error_msg}), 500
                return render_template("panel.html", page="register")
            
            logger.info(f"✅ حساب کاربری با ID {oid} ایجاد شد")
            db.init_user_settings(oid)
            session["owner_id"] = oid
            
            if request.is_json:
                return jsonify({"ok": True, "message": "ثبت‌نام با موفقیت انجام شد"})
            return redirect(url_for("index"))
            
        except Exception as e:
            error_msg = f"خطا در ثبت‌نام: {str(e)}"
            logger.error(f"❌ {error_msg}")
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 500
            return render_template("panel.html", page="register")
    
    return render_template("panel.html", page="register")

# ─── API: ثبت‌نام (alias) ────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def api_register():
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    
    if not username:
        return jsonify({"ok": False, "error": "یوزرنیم الزامی است"}), 400
    if not password:
        return jsonify({"ok": False, "error": "رمز عبور الزامی است"}), 400
    if len(username) < 3:
        return jsonify({"ok": False, "error": "یوزرنیم باید حداقل ۳ کاراکتر باشد"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400
    
    try:
        existing = db.get_account_by_username(username)
        if existing:
            return jsonify({"ok": False, "error": "این یوزرنیم قبلاً ثبت شده است"}), 409
    except Exception as e:
        logger.error(f"❌ خطا در بررسی تکراری بودن: {e}")
    
    try:
        oid = db.create_account(username, password)
        if oid is None:
            return jsonify({"ok": False, "error": "خطا در ایجاد حساب. لطفاً دوباره تلاش کنید."}), 500
        
        db.init_user_settings(oid)
        session["owner_id"] = oid
        logger.info(f"✅ حساب کاربری جدید با API: {username} (ID: {oid})")
        return jsonify({"ok": True, "message": "ثبت‌نام با موفقیت انجام شد"})
    except Exception as e:
        logger.error(f"❌ خطا در api_register: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ─── خروج ───────────────────────────────────────────────────────────────────
@app.route("/logout")
def logout():
    # فقط session پنل پاک می‌شود — session تلگرام دست‌نخورده می‌ماند
    session.pop("owner_id", None)
    return redirect(url_for("login"))

# ─── صفحه اتصال تلگرام ──────────────────────────────────────────────────────
@app.route("/tg-login")
@login_required
def tg_login_page():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account:
        session.pop("owner_id", None)
        return redirect(url_for("login"))
    
    return render_template("panel.html", page="tg_login", username=account["username"])

# ─── API: ارسال کد تأیید ────────────────────────────────────────────────────
@app.route("/api/login/send_code", methods=["POST"])
@login_required
def send_code():
    oid = owner_id()
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    phone = data.get("phone", "").strip()
    
    if not phone:
        return jsonify({"ok": False, "error": "شماره تلفن الزامی است"}), 400
    if not config.API_ID or not config.API_HASH:
        return jsonify({"ok": False, "error": "API_ID و API_HASH تنظیم نشده‌اند"}), 400

    async def _send():
        cl = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
        await cl.connect()
        result = await cl.send_code_request(phone)
        partial_sess = cl.session.save()
        await cl.disconnect()
        
        _phone_hashes[oid] = result.phone_code_hash
        _phone_numbers[oid] = phone
        _login_clients[oid] = partial_sess
        
        return {"ok": True}

    try:
        return jsonify(run_async(_send()))
    except FloodWaitError as e:
        return jsonify({"ok": False, "error": f"محدودیت: {e.seconds} ثانیه صبر کنید"}), 429
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ─── API: تأیید کد ──────────────────────────────────────────────────────────
@app.route("/api/login/verify_code", methods=["POST"])
@login_required
def verify_code():
    oid = owner_id()
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    code = data.get("code", "").strip()
    
    if not code:
        return jsonify({"ok": False, "error": "کد الزامی است"}), 400

    phone = _phone_numbers.get(oid)
    ph = _phone_hashes.get(oid)
    partial_sess = _login_clients.get(oid)

    if not phone or not ph or not partial_sess:
        return jsonify({"ok": False, "error": "ابتدا کد ارسال کنید"}), 400

    async def _verify():
        cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
        await cl.connect()
        await cl.sign_in(phone=phone, code=code, phone_code_hash=ph)
        me = await cl.get_me()
        sess = cl.session.save()
        await cl.disconnect()
        
        _login_clients.pop(oid, None)
        _phone_hashes.pop(oid, None)
        _phone_numbers.pop(oid, None)
        
        db.save_session(oid, sess, phone)
        db.save_telegram_user_id(oid, me.id)
        db.set_setting(oid, "logged_in", "1")
        
        return {"ok": True, "tg_id": me.id, "first_name": me.first_name}

    try:
        result = run_async(_verify())
        return jsonify(result)
    except SessionPasswordNeededError:
        return jsonify({"ok": False, "need_2fa": True}), 200
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        return jsonify({"ok": False, "error": "کد اشتباه یا منقضی شده"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ─── API: تأیید رمز دومرحله‌ای ──────────────────────────────────────────────
@app.route("/api/login/verify_2fa", methods=["POST"])
@login_required
def verify_2fa():
    oid = owner_id()
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    password = data.get("password", "").strip()
    
    if not password:
        return jsonify({"ok": False, "error": "رمز دو مرحله‌ای الزامی است"}), 400

    phone = _phone_numbers.get(oid)
    partial_sess = _login_clients.get(oid)

    if not partial_sess:
        return jsonify({"ok": False, "error": "ابتدا کد تأیید را وارد کنید"}), 400

    async def _verify():
        cl = TelegramClient(StringSession(partial_sess), config.API_ID, config.API_HASH)
        await cl.connect()
        await cl.sign_in(password=password)
        me = await cl.get_me()
        sess = cl.session.save()
        await cl.disconnect()
        
        _login_clients.pop(oid, None)
        _phone_hashes.pop(oid, None)
        _phone_numbers.pop(oid, None)
        
        db.save_session(oid, sess, phone)
        db.save_telegram_user_id(oid, me.id)
        db.set_setting(oid, "logged_in", "1")
        
        return {"ok": True, "tg_id": me.id, "first_name": me.first_name}

    try:
        result = run_async(_verify())
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ─── API: خروج از سشن تلگرام ───────────────────────────────────────────────
@app.route("/api/logout_session", methods=["POST"])
@login_required
def tg_logout():
    oid = owner_id()
    
    try:
        from bot import bot_manager
        bot_manager.stop(oid)
    except:
        pass
    
    db.delete_session(oid)
    db.set_setting(oid, "logged_in", "0")
    return jsonify({"ok": True})

# ─── API: حذف سلف (فقط خود سلف) ────────────────────────────────────────────
@app.route("/api/remove_session", methods=["POST"])
@login_required
def remove_session():
    """
    حذف سلف کاربر - فقط سشن این کاربر حذف می‌شود
    سایر سشن‌ها و کاربران تحت تأثیر قرار نمی‌گیرند
    """
    oid = owner_id()
    
    try:
        logger.info(f"🗑️ درخواست حذف سلف برای کاربر {oid}")
        
        # 1. توقف سلف‌بات این کاربر
        try:
            from bot import bot_manager
            bot_manager.stop(oid)
            logger.info(f"✅ سلف‌بات کاربر {oid} متوقف شد")
        except Exception as e:
            logger.warning(f"⚠️ خطا در توقف سلف‌بات: {e}")
        
        # 2. حذف سشن این کاربر از دیتابیس
        db.delete_session(oid)
        logger.info(f"✅ سشن کاربر {oid} حذف شد")
        
        # 3. غیرفعال کردن وضعیت لاگین این کاربر
        db.set_setting(oid, "logged_in", "0")
        logger.info(f"✅ وضعیت لاگین کاربر {oid} غیرفعال شد")
        
        # 4. غیرفعال کردن سلف این کاربر
        db.set_setting(oid, "self_bot_active", "0")
        logger.info(f"✅ سلف کاربر {oid} غیرفعال شد")
        
        return jsonify({
            "ok": True,
            "message": "✅ سلف با موفقیت حذف شد!\nشما از حساب تلگرام خارج شدید."
        })
        
    except Exception as e:
        logger.error(f"❌ خطا در حذف سلف کاربر {oid}: {e}")
        return jsonify({
            "ok": False,
            "error": f"خطا در حذف سلف: {str(e)}"
        }), 500

# ─── API: وضعیت سشن ─────────────────────────────────────────────────────────
@app.route("/api/session/status")
@login_required
def session_status():
    oid = owner_id()
    is_active = db.get_session(oid) is not None
    
    try:
        from bot import bot_manager
        is_running = bot_manager.is_running(oid)
    except:
        is_running = False
    
    return jsonify({
        "active": is_active,
        "running": is_running
    })

# ─── API: وضعیت حذف سلف ────────────────────────────────────────────────────
@app.route("/api/session/remove_status")
@login_required
def remove_status():
    """بررسی اینکه آیا کاربر می‌تواند سلف را حذف کند"""
    oid = owner_id()
    has_session = db.get_session(oid) is not None
    is_running = False
    
    try:
        from bot import bot_manager
        is_running = bot_manager.is_running(oid)
    except:
        pass
    
    return jsonify({
        "ok": True,
        "has_session": has_session,
        "is_running": is_running,
        "can_remove": has_session or is_running
    })

# ─── API: اطلاعات کاربر ─────────────────────────────────────────────────────
@app.route("/api/me")
@login_required
def api_me():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account:
        return jsonify({"ok": False, "error": "حساب یافت نشد"}), 404
    
    return jsonify({
        "ok": True,
        "id": account["id"],
        "username": account["username"],
        "balance": db.get_token_balance(oid),
        "has_session": db.get_session(oid) is not None,
        "telegram_id": account.get("telegram_user_id"),
    })

# ─── API: موجودی ────────────────────────────────────────────────────────────
@app.route("/api/balance")
@login_required
def api_balance():
    oid = owner_id()
    return jsonify({
        "ok": True,
        "balance": db.get_token_balance(oid)
    })

# ─── API: تنظیمات ───────────────────────────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    oid = owner_id()
    keys = [
        "self_bot_active", "secretary_active", "anti_delete_active",
        "anti_link_active", "auto_seen_active", "auto_reaction_active",
        "private_lock_active", "enemy_reply_active", "auto_save_media",
        "clock_name_active", "clock_bio_active", "selected_font",
        "secretary_message", "auto_reaction_emoji",
    ]
    return jsonify({k: db.get_setting(oid, k) for k in keys})

@app.route("/api/settings", methods=["POST"])
@login_required
def update_settings():
    oid = owner_id()
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    allowed = [
        "secretary_message", "auto_reaction_emoji", "selected_font",
        "secretary_active", "anti_delete_active", "anti_link_active",
        "auto_seen_active", "auto_reaction_active", "private_lock_active",
        "clock_name_active", "clock_bio_active",
    ]
    for k in allowed:
        if k in data:
            db.set_setting(oid, k, str(data[k]))
    return jsonify({"ok": True})

@app.route("/api/toggle/<key>", methods=["POST"])
@login_required
def toggle(key):
    allowed = [
        "self_bot_active", "secretary_active", "anti_delete_active",
        "anti_link_active", "auto_seen_active", "auto_reaction_active",
        "private_lock_active", "auto_save_media", "clock_name_active", "clock_bio_active",
    ]
    if key not in allowed:
        return jsonify({"ok": False, "error": "کلید مجاز نیست"}), 400
    
    current = db.get_setting(owner_id(), key, "0")
    new_val = "0" if current == "1" else "1"
    db.set_setting(owner_id(), key, new_val)
    return jsonify({"ok": True, "active": new_val == "1"})

# ─── API: روشن کردن سلف ─────────────────────────────────────────────────────
@app.route("/api/start", methods=["POST"])
@login_required
def start_bot_api():
    oid = owner_id()
    
    session_data = db.get_session(oid)
    if not session_data:
        return jsonify({
            "ok": False,
            "error": "ابتدا باید وارد حساب تلگرام شوید"
        }), 400
    
    try:
        from bot import bot_manager
        ok = bot_manager.start(oid, get_loop(), check_tokens=True)
    except Exception as e:
        return jsonify({"ok": False, "error": f"خطا در استارت سلف: {e}"}), 500
    
    if ok:
        db.set_setting(oid, "self_bot_active", "1")
        account = db.get_account(oid)
        is_owner = (account and account.get("telegram_user_id") == config.OWNER_TG_ID)
        
        if is_owner:
            msg = "✅ سلف روشن شد — دسترسی رایگان مالک ♾️"
        else:
            price = getattr(config, 'TOKENS_PER_SESSION', 2)
            hours = getattr(config, 'SESSION_HOURS', 2)
            msg = f"✅ سلف روشن شد — {price} الماس کسر شد — {hours} ساعت فعال است"
        
        return jsonify({"ok": True, "message": msg})
    else:
        balance = db.get_token_balance(oid)
        price = getattr(config, 'TOKENS_PER_SESSION', 2)
        return jsonify({
            "ok": False,
            "error": f"الماس کافی ندارید! موجودی: {balance} — برای روشن کردن {price} الماس لازم است.",
        })

# ─── API: خاموش کردن سلف ────────────────────────────────────────────────────
@app.route("/api/stop", methods=["POST"])
@login_required
def stop_bot_api():
    oid = owner_id()
    
    try:
        from bot import bot_manager
        bot_manager.stop(oid)
    except:
        pass
    
    db.set_setting(oid, "self_bot_active", "0")
    return jsonify({"ok": True})

# ─── API: انتقال الماس ──────────────────────────────────────────────────────
@app.route("/api/transfer", methods=["POST"])
@login_required
def api_transfer():
    oid = owner_id()
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    username = data.get("username", "").strip().lstrip("@")
    try:
        amount = int(data.get("amount", 0))
    except:
        return jsonify({"ok": False, "error": "مبلغ نامعتبر است"}), 400
    
    if amount <= 0:
        return jsonify({"ok": False, "error": "مبلغ باید بیشتر از 0 باشد"}), 400
    
    to_account = db.get_account_by_username(username)
    if not to_account:
        return jsonify({"ok": False, "error": f"کاربر '{username}' یافت نشد"}), 404
    
    if to_account["id"] == oid:
        return jsonify({"ok": False, "error": "نمی‌توانید به خودتان الماس انتقال دهید"}), 400
    
    success, msg = db.transfer_diamonds(oid, to_account["id"], amount)
    if success:
        return jsonify({
            "ok": True,
            "message": msg,
            "new_balance": db.get_token_balance(oid)
        })
    else:
        return jsonify({"ok": False, "error": msg}), 500

# ─── API: هدیه روزانه ───────────────────────────────────────────────────────
@app.route("/api/daily", methods=["POST"])
@login_required
def claim_daily():
    oid = owner_id()
    success, message = db.claim_daily_token(oid)
    return jsonify({
        "ok": success,
        "message": message,
        "balance": db.get_token_balance(oid) if success else None
    })

# ─── API: آمار (مالک) ───────────────────────────────────────────────────────
@app.route("/api/admin/stats")
@login_required
def admin_stats():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account or account.get("telegram_user_id") != config.OWNER_TG_ID:
        return jsonify({"ok": False, "error": "فقط مالک"}), 403
    
    accounts = db.get_all_accounts()
    total_users = len(accounts)
    total_balance = sum(db.get_token_balance(a["id"]) for a in accounts) if accounts else 0
    
    return jsonify({
        "ok": True,
        "total_users": total_users,
        "total_balance": total_balance,
    })

# ─── API: لیست کاربران (مالک) ───────────────────────────────────────────────
@app.route("/api/admin/users")
@login_required
def admin_users():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account or account.get("telegram_user_id") != config.OWNER_TG_ID:
        return jsonify({"ok": False, "error": "فقط مالک"}), 403
    
    accounts = db.get_all_accounts()[:50]
    user_list = []
    for acc in accounts:
        user_list.append({
            "id": acc["id"],
            "username": acc["username"],
            "balance": db.get_token_balance(acc["id"]),
            "has_session": db.get_session(acc["id"]) is not None,
            "created_at": acc.get("created_at"),
        })
    
    return jsonify({
        "ok": True,
        "users": user_list
    })

# ─── API: دادن الماس (مالک) ─────────────────────────────────────────────────
@app.route("/api/admin/give", methods=["POST"])
@login_required
def admin_give():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account or account.get("telegram_user_id") != config.OWNER_TG_ID:
        return jsonify({"ok": False, "error": "فقط مالک"}), 403
    
    if request.is_json:
        data = request.json
    else:
        data = request.form or {}
    
    username = data.get("username", "").strip().lstrip("@")
    
    try:
        amount = int(data.get("amount", 0))
    except:
        return jsonify({"ok": False, "error": "مبلغ نامعتبر"}), 400
    
    if amount <= 0:
        return jsonify({"ok": False, "error": "مبلغ باید بیشتر از 0 باشد"}), 400
    
    to_account = db.get_account_by_username(username)
    if not to_account:
        return jsonify({"ok": False, "error": f"کاربر '{username}' یافت نشد"}), 404
    
    db.add_tokens(to_account["id"], amount)
    
    return jsonify({
        "ok": True, 
        "message": f"✅ {amount} الماس به {username} داده شد",
        "new_balance": db.get_token_balance(to_account["id"])
    })

# ─── API: چنل‌های اجباری ───────────────────────────────────────────────────
@app.route("/api/forced_channels", methods=["GET"])
@login_required
def get_forced_channels():
    return jsonify(cache.get_forced_channels())

@app.route("/api/forced_channels", methods=["POST"])
@login_required
def add_forced_channel():
    data = request.json or {}
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"ok": False, "error": "یوزرنیم کانال الزامی است"}), 400
    if cache.add_forced_channel(username):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "خطا یا کانال تکراری است"})

@app.route("/api/forced_channels/<username>", methods=["DELETE"])
@login_required
def remove_forced_channel(username):
    if cache.remove_forced_channel(username):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "کانال یافت نشد"})

# ─── API: قرعه‌کشی (مالک) ──────────────────────────────────────────────────
@app.route("/api/lottery", methods=["POST"])
@login_required
def create_lottery():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account or account.get("telegram_user_id") != config.OWNER_TG_ID:
        return jsonify({"ok": False, "error": "فقط مالک"}), 403
    
    data = request.json or {}
    prize = data.get("prize", 0)
    
    if prize <= 0:
        return jsonify({"ok": False, "error": "مبلغ جایزه باید بیشتر از 0 باشد"}), 400
    
    lottery_id = db.create_lottery(0, config.OWNER_TG_ID, prize, 2, prize)
    
    if lottery_id:
        return jsonify({
            "ok": True,
            "lottery_id": lottery_id,
            "message": f"✅ قرعه‌کشی با جایزه {prize} الماس ایجاد شد"
        })
    else:
        return jsonify({
            "ok": False,
            "error": "خطا در ایجاد قرعه‌کشی"
        }), 500

# ─── API: دریافت لیست قرعه‌کشی‌ها (مالک) ──────────────────────────────────
@app.route("/api/lotteries", methods=["GET"])
@login_required
def get_lotteries():
    oid = owner_id()
    account = db.get_account(oid)
    
    if not account or account.get("telegram_user_id") != config.OWNER_TG_ID:
        return jsonify({"ok": False, "error": "فقط مالک"}), 403
    
    active_lotteries = []
    try:
        pass
    except:
        pass
    
    return jsonify({
        "ok": True,
        "lotteries": active_lotteries
    })

# ─── اجرا ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # استارت ربات تلگرام
    try:
        from telegram_bot import start_token_bot
        start_token_bot()
        print("✅ ربات تلگرام استارت شد")
    except Exception as e:
        print(f"❌ خطا در استارت ربات تلگرام: {e}")
    
    # استارت خودکار سلف‌بات‌های فعال
    loop = get_loop()
    active_sessions = db.get_all_logged_in_users()
    
    started_count = 0
    for oid in active_sessions:
        try:
            session_data = db.get_session(oid)
            self_active = db.get_setting(oid, "self_bot_active", "0")
            
            if session_data and self_active == "1":
                from bot import bot_manager
                bot_manager.start(oid, loop, check_tokens=False)
                started_count += 1
                print(f"🚀 سلف‌بات کاربر {oid} استارت شد")
        except Exception as e:
            print(f"❌ خطا در استارت کاربر {oid}: {e}")
    
    print(f"✅ {started_count} سلف‌بات فعال شد")
    
    # اجرای Flask
    app.run(host="0.0.0.0", port=config.PORT, debug=False, threaded=True)
