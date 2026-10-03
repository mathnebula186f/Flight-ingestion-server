import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = timezone(timedelta(hours=5, minutes=30))

AIRPORTS = ["DEL", "BLR", "HYD", "GOI", "IXC", "BOM", "MAA", "CCU", "AMD", "COK"]
DEFAULT_DAYS_AHEAD = list(range(1, 31))

DELAY_SECONDS = 3                # between the two tab fetches, and between searches
MAX_CONSECUTIVE_FAILURES = 5     # stop the run: likely blocked by Google
MAX_FAILURE_RATE = 0.2           # above this the run exits non-zero

LOG_DIR = ROOT / "logs"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    return url
