import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "amel_self55_secret_key_change_me")
PORT = int(os.environ.get("PORT", 5000))

# ✅ اتصال به Supabase PostgreSQL
DATABASE_URL = os.environ.get("DATABASE_URL", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "8296865861"))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "amele55")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "").lstrip("+")

_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
SITE_URL = os.environ.get("SITE_URL", f"https://{_render_host}" if _render_host else "")

BOT_NAME = "AMEL SELF55"
BOT_VERSION = "2.0.0"

WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")

# ─── سیستم الماس ──────────────────────────────────────────────────────────────
TOKENS_PER_SESSION = 2
SESSION_HOURS = 2
DAILY_TOKEN_GIFT = 1
REFERRAL_TOKENS = 12
WELCOME_TOKENS = 10
TOKEN_PRICE_TOMAN = 200

# ─── اسپانسرها ───────────────────────────────────────────────────────────────
SPONSORS = [
    {"username": "pesar777", "name": "اسپانسر اول"},
    {"username": "ISOLODEVIL", "name": "اسپانسر دوم"},
]

# ─── تنظیمات قرعه‌کشی و شرط‌بندی ──────────────────────────────────────────────
LOTTERY_DURATION_MINUTES = 5
WORLD_CUP_GROUP = "@amelselfgap"
