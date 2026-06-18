#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, get_loop, logger
import config
import database as db

if __name__ == "__main__":
    logger.info(f"🚀 {config.BOT_NAME} v{config.BOT_VERSION} در حال راه‌اندازی...")

    try:
        from telegram_bot import start_token_bot
        start_token_bot()
        logger.info("✅ ربات تلگرام استارت شد")
    except Exception as e:
        logger.error(f"❌ خطا در استارت ربات تلگرام: {e}")

    loop = get_loop()
    try:
        from bot import bot_manager
        logged_in_users = db.get_all_logged_in_users()
        for uid in logged_in_users:
            sess = db.get_session(uid)
            if sess:
                try:
                    bot_manager.start(uid, loop, check_tokens=False)
                    logger.info(f"✅ سلف‌بات کاربر {uid} راه‌اندازی شد")
                except Exception as e:
                    logger.error(f"❌ خطا در راه‌اندازی سلف {uid}: {e}")
    except Exception as e:
        logger.error(f"❌ خطا در راه‌اندازی سلف‌بات‌ها: {e}")

    logger.info(f"🌐 پنل وب روی پورت {config.PORT}")
    app.run(host="0.0.0.0", port=config.PORT, debug=False, threaded=True)
