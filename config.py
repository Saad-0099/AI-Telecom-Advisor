"""
Telecom Decision Intelligence Platform — Phase 1 configuration.

DESIGN CONSTRAINT (Phase 0): the source data is a SINGLE SNAPSHOT.
There is no calendar time dimension. Tenure cohort is the ordered axis.
Do not add date columns to any table.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

RAW_CSV = DATA_DIR / "telecom_churn.csv"
DB_PATH = DATA_DIR / "telecom.db"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# SQLite for development. To move to Postgres/MySQL, change only this line:
#   postgresql+psycopg2://user:pass@host:5432/telecom
#   mysql+pymysql://user:pass@host:3306/telecom
DB_URL = f"sqlite:///{DB_PATH}"

# --------------------------------------------------------------------------
# Tenure cohorts  (account_length is in DAYS)
# --------------------------------------------------------------------------
# (cohort_id, label, min_days, max_days | None = open-ended, sort_order)
# The original 181d+ bucket held only 2.6% of customers and was merged
# into Mature per the MIN_COHORT_SHARE rule below.
TENURE_COHORTS = [
    (1, "New (0-60d)",            0,   60,   1),
    (2, "Early (61-100d)",        61,  100,  2),
    (3, "Established (101-140d)", 101, 140,  3),
    (4, "Mature (141d+)",         141, None, 4),
]

# Warn if any cohort holds less than this share of customers — its churn
# rate will be too noisy to narrate reliably in Phase 4.
MIN_COHORT_SHARE = 0.08

# --------------------------------------------------------------------------
# Feature engineering thresholds
# --------------------------------------------------------------------------
# Confirmed against this dataset: churn jumps 10.3% -> 45.8% at 4+ calls.
HIGH_SERVICE_CALLS_THRESHOLD = 3   # flag is "> 3", i.e. 4 or more

# --------------------------------------------------------------------------
# Data quality bounds (sanity checks, not hard filters)
# --------------------------------------------------------------------------
PLAUSIBLE_BOUNDS = {
    "account_length": (0, 1000),
    "customer_service_calls": (0, 20),
    "minutes": (0, 1000),
    "calls": (0, 500),
    "charge": (0, 200),
}

# --------------------------------------------------------------------------
# CSV column aliases
# --------------------------------------------------------------------------
# Keys are NORMALIZED source names (lowercase, apostrophes stripped,
# non-alphanumerics -> underscore). Values are canonical internal names.
COLUMN_ALIASES = {
    # identity
    "state": "state",
    "area_code": "area_code",
    "phone": "phone_number",
    "phone_number": "phone_number",
    "account_length": "account_length",
    "accountlength": "account_length",
    # plans
    "intl_plan": "international_plan",
    "international_plan": "international_plan",
    "vmail_plan": "voice_mail_plan",
    "voice_mail_plan": "voice_mail_plan",
    "voicemail_plan": "voice_mail_plan",
    "vmail_message": "number_vmail_messages",
    "number_vmail_messages": "number_vmail_messages",
    "number_vmail_message": "number_vmail_messages",
    # day
    "day_mins": "day_minutes",
    "total_day_minutes": "day_minutes",
    "day_calls": "day_calls",
    "total_day_calls": "day_calls",
    "day_charge": "day_charge",
    "total_day_charge": "day_charge",
    # evening
    "eve_mins": "eve_minutes",
    "total_eve_minutes": "eve_minutes",
    "eve_calls": "eve_calls",
    "total_eve_calls": "eve_calls",
    "eve_charge": "eve_charge",
    "total_eve_charge": "eve_charge",
    # night
    "night_mins": "night_minutes",
    "total_night_minutes": "night_minutes",
    "night_calls": "night_calls",
    "total_night_calls": "night_calls",
    "night_charge": "night_charge",
    "total_night_charge": "night_charge",
    # international
    "intl_mins": "intl_minutes",
    "total_intl_minutes": "intl_minutes",
    "intl_calls": "intl_calls",
    "total_intl_calls": "intl_calls",
    "intl_charge": "intl_charge",
    "total_intl_charge": "intl_charge",
    # service + target
    "custserv_calls": "customer_service_calls",
    "customer_service_calls": "customer_service_calls",
    "churn": "churned",
    "churn_": "churned",
}

REQUIRED_COLUMNS = [
    "state", "account_length", "area_code",
    "international_plan", "voice_mail_plan", "number_vmail_messages",
    "day_minutes", "day_calls", "day_charge",
    "eve_minutes", "eve_calls", "eve_charge",
    "night_minutes", "night_calls", "night_charge",
    "intl_minutes", "intl_calls", "intl_charge",
    "customer_service_calls", "churned",
]

USAGE_PERIODS = ["day", "eve", "night"]