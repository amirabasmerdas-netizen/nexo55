import asyncio
import os
import threading
import time as _time
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
    return render_template("error.html", error="صفحه یافت نشد"), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"ok": False, "error": f"خطای داخلی سرور: {str(e)}"}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    print(f"❌ خطای غیرمنتظره: {e}")
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
            return render_template("login.html", error=error_msg)
        
        oid = db.verify_account(username, password)
        if oid is None:
            error_msg = "یوزرنیم یا رمز اشتباه است"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 401
            return render_template("login.html", error=error_msg)
        
        session["owner_id"] = oid
        db.init_user_settings(oid)
        
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("index"))
    
    return render_template("login.html")


# ─── ثبت‌نام ─────────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
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
            return render_template("register.html", error=error_msg)
        
        if len(username) < 3:
            error_msg = "یوزرنیم باید حداقل ۳ کاراکتر باشد"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("register.html", error=error_msg)
        
        if len(password) < 6:
            error_msg = "رمز باید حداقل ۶ کاراکتر باشد"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 400
            return render_template("register.html", error=error_msg)
        
        oid = db.create_account(username, password)
        if oid is None:
            error_msg = "این یوزرنیم قبلاً ثبت شده"
            if request.is_json:
                return jsonify({"ok": False, "error": error_msg}), 409
            return render_template("register.html", error=error_msg)
        
        db.init_user_settings(oid)
        session["owner_id"] = oid
        
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("index"))
    
    return render_template("register.html")


# ─── خروج ───────────────────────────────────────────────────────────────────
@app.route("/logout")
def logout():
    oid = session.get("owner_id")
    if oid:
        try:
            db.delete_session(oid)
        except:
            pass
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
    
    return render_template("tg_login.html", username=account["username"])


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
        
        # ذخیره سشن در دیتابیس
        db.save_session(oid, sess, phone)
        db.save_telegram_user_id(oid, me.id)
        
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
    return jsonify({"ok": True})


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
    
    success = db.transfer_tokens(oid, to_account["telegram_user_id"], amount)
    if success:
        return jsonify({
            "ok": True,
            "message": f"✅ {amount} الماس به {username} انتقال یافت",
            "new_balance": db.get_token_balance(oid)
        })
    else:
        return jsonify({"ok": False, "error": "خطا در انتقال الماس"}), 500


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
    total_balance = sum(a.get("balance", 0) for a in accounts) if accounts else 0
    
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
    return jsonify({
        "ok": True,
        "users": accounts
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


# ─── اجرا ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # استارت ربات تلگرام
    from telegram_bot import start_token_bot
    start_token_bot()
    
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
    
    app.run(host="0.0.0.0", port=config.PORT, debug=False, threaded=True)
