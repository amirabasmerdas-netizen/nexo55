import os
import secrets
import warnings
from dotenv import load_dotenv
import re

load_dotenv()

# ─── تلگرام ──────────────────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ─── سرور ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    warnings.warn(
        "⚠️  SECRET_KEY در محیط تنظیم نشده — یک کلید تصادفی موقت ساخته شد. "
        "پس از راه‌اندازی مجدد، تمام session‌های کاربران باطل می‌شوند. "
        "حتماً SECRET_KEY را در متغیرهای محیطی تنظیم کنید.",
        RuntimeWarning,
        stacklevel=2,
    )

PORT = int(os.environ.get("PORT", 5000))
SITE_URL = os.environ.get("SITE_URL", "")

# ─── مالک ──────────────────────────────────────────────────────────────────
OWNER_TG_ID = int(os.environ.get("OWNER_TG_ID", "8296865861"))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "amele55")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "").lstrip("+")

# ─── دیتابیس پایدار (Supabase PostgreSQL) ──────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ✅ استخراج صحیح SUPABASE_URL از DATABASE_URL
if DATABASE_URL:
    match = re.search(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DATABASE_URL)
    if match:
        user, password, host, port, dbname = match.groups()
        project_id = user.split('.')[-1] if '.' in user else user
        SUPABASE_URL = f"https://{project_id}.supabase.co"
        print(f"✅ استخراج SUPABASE_URL: {SUPABASE_URL}")
    else:
        SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
else:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE_PREFIX = os.environ.get("SUPABASE_TABLE_PREFIX", "amel_")

# ─── دیتابیس موقت ──────────────────────────────────────────────────────────
CACHE_DB_PATH = os.environ.get("CACHE_DB_PATH", "cache.db")

# ─── سیستم ──────────────────────────────────────────────────────────────────
BOT_NAME = "AMEL SELF55"
BOT_VERSION = "1.2.0"
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")

# ─── سیستم توکن ──────────────────────────────────────────────────────────────
TOKENS_PER_SESSION = 2
SESSION_HOURS = 2
DAILY_TOKEN_GIFT = 0.5
REFERRAL_TOKENS = 12
WELCOME_TOKENS = 10
TOKEN_PRICE_TOMAN = 200

# ─── اسپانسرها ───────────────────────────────────────────────────────────────
SPONSORS = [
    {"username": "pesar777", "name": "اسپانسر اول"},
    {"username": "ISOLODEVIL", "name": "اسپانسر دوم"},
]

# ─── کش تنظیمات ──────────────────────────────────────────────────────────────
CACHE_TTL = 60
