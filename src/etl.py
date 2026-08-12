"""
Phase 1 ETL — CSV -> cleaned -> feature-engineered -> normalized tables.

Run:  python etl.py
Idempotent: drops and rebuilds every table on each run.
"""

from __future__ import annotations

import logging
import re
import sys

import pandas as pd
from sqlalchemy import create_engine

import config as C
import models

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("etl")

TRUEISH = {"yes", "y", "true", "true.", "t", "1"}
FALSEISH = {"no", "n", "false", "false.", "f", "0"}


# ==========================================================================
# 1. EXTRACT
# ==========================================================================
def normalize_name(name: str) -> str:
    """'Int'l Plan' -> intl_plan ;  'total day minutes' -> total_day_minutes"""
    s = str(name).strip().lower().replace("'", "").replace("?", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def load_csv(path) -> pd.DataFrame:
    log.info("Reading %s", path)
    df = pd.read_csv(path)
    log.info("  raw shape: %s rows x %s cols", *df.shape)

    renamed, unmapped = {}, []
    for col in df.columns:
        key = normalize_name(col)
        if key in C.COLUMN_ALIASES:
            renamed[col] = C.COLUMN_ALIASES[key]
        else:
            unmapped.append(col)
    df = df.rename(columns=renamed)

    if unmapped:
        log.warning("  unmapped columns dropped: %s", unmapped)
        df = df.drop(columns=unmapped)

    missing = [c for c in C.REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV is missing required columns after alias mapping: {missing}\n"
            f"Add the source names to COLUMN_ALIASES in config.py."
        )
    return df


# ==========================================================================
# 2. CLEAN
# ==========================================================================
def to_bool(series: pd.Series, col: str) -> pd.Series:
    if series.dtype == bool:
        return series
    s = series.astype(str).str.strip().str.lower()
    out = s.map(lambda v: True if v in TRUEISH else (False if v in FALSEISH else None))
    if out.isna().any():
        bad = sorted(s[out.isna()].unique())[:5]
        raise ValueError(f"Unparseable boolean values in '{col}': {bad}")
    return out.astype(bool)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    log.info("Cleaning")

    for col in ("international_plan", "voice_mail_plan", "churned"):
        df[col] = to_bool(df[col], col)

    int_cols = ["account_length", "area_code", "number_vmail_messages",
                "customer_service_calls", "day_calls", "eve_calls",
                "night_calls", "intl_calls"]
    float_cols = ["day_minutes", "day_charge", "eve_minutes", "eve_charge",
                  "night_minutes", "night_charge", "intl_minutes", "intl_charge"]

    for col in int_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in float_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["state"] = df["state"].astype(str).str.strip().str.upper()
    if "phone_number" in df.columns:
        df["phone_number"] = df["phone_number"].astype(str).str.strip()

    # --- nulls -------------------------------------------------------------
    nulls = df.isna().sum()
    nulls = nulls[nulls > 0]
    if len(nulls):
        log.warning("  nulls found:\n%s", nulls.to_string())
        before = len(df)
        df = df.dropna(subset=int_cols + float_cols + ["state"])
        log.warning("  dropped %d rows with nulls in key fields", before - len(df))
    else:
        log.info("  no nulls")

    # --- duplicates --------------------------------------------------------
    if "phone_number" in df.columns:
        dupes = df["phone_number"].duplicated().sum()
        if dupes:
            log.warning("  %d duplicate phone_numbers — keeping first", dupes)
            df = df.drop_duplicates(subset="phone_number", keep="first")
        else:
            log.info("  no duplicate phone numbers")

    # --- plausibility (report only, do not silently filter) ----------------
    lo, hi = C.PLAUSIBLE_BOUNDS["account_length"]
    n = ((df.account_length < lo) | (df.account_length > hi)).sum()
    if n:
        log.warning("  %d implausible account_length values", n)

    lo, hi = C.PLAUSIBLE_BOUNDS["customer_service_calls"]
    n = ((df.customer_service_calls < lo) | (df.customer_service_calls > hi)).sum()
    if n:
        log.warning("  %d implausible customer_service_calls values", n)

    for col in float_cols:
        kind = "charge" if "charge" in col else "minutes"
        lo, hi = C.PLAUSIBLE_BOUNDS[kind]
        n = ((df[col] < lo) | (df[col] > hi)).sum()
        if n:
            log.warning("  %d out-of-range values in %s", n, col)

    log.info("  clean shape: %s rows", len(df))
    return df.reset_index(drop=True)


# ==========================================================================
# 3. FEATURE ENGINEERING
# ==========================================================================
def assign_cohort(days: int) -> int:
    for cid, _label, lo, hi, _sort in C.TENURE_COHORTS:
        if days >= lo and (hi is None or days <= hi):
            return cid
    raise ValueError(f"No cohort matches account_length={days}")


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    log.info("Feature engineering")

    df["customer_id"] = range(1, len(df) + 1)

    df["total_minutes"] = df[["day_minutes", "eve_minutes",
                              "night_minutes", "intl_minutes"]].sum(axis=1)
    df["total_charge"] = df[["day_charge", "eve_charge",
                             "night_charge", "intl_charge"]].sum(axis=1)

    df["high_service_calls"] = (
        df["customer_service_calls"] > C.HIGH_SERVICE_CALLS_THRESHOLD
    )

    df["cohort_id"] = df["account_length"].astype(int).map(assign_cohort)

    check_cohort_distribution(df)
    return df


def check_cohort_distribution(df: pd.DataFrame) -> None:
    """Enforce the Phase 0 rule: no cohort below MIN_COHORT_SHARE."""
    labels = {c[0]: c[1] for c in C.TENURE_COHORTS}
    counts = df["cohort_id"].value_counts().sort_index()
    total = len(df)

    log.info("  cohort distribution:")
    thin = []
    for cid, n in counts.items():
        share = n / total
        churn = df.loc[df.cohort_id == cid, "churned"].mean()
        flag = ""
        if share < C.MIN_COHORT_SHARE:
            flag = "  <-- THIN"
            thin.append(labels[cid])
        log.info("    %-26s n=%-5d %5.1f%%  churn=%4.1f%%%s",
                 labels[cid], n, share * 100, churn * 100, flag)

    missing = set(labels) - set(counts.index)
    if missing:
        log.warning("  EMPTY cohorts: %s", [labels[c] for c in missing])
    if thin:
        log.warning("  Cohorts below %.0f%% share: %s — churn rates here are "
                    "statistically noisy. Merge them in config.TENURE_COHORTS.",
                    C.MIN_COHORT_SHARE * 100, thin)


# ==========================================================================
# 4. LOAD
# ==========================================================================
def build_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    cohorts = pd.DataFrame(
        C.TENURE_COHORTS,
        columns=["cohort_id", "label", "min_days", "max_days", "sort_order"],
    )

    customer = df[["customer_id", "state", "area_code", "cohort_id"]].copy()
    customer["phone_number"] = df.get("phone_number")
    customer["account_length_days"] = df["account_length"].astype(int)
    customer = customer[["customer_id", "phone_number", "state", "area_code",
                         "account_length_days", "cohort_id"]]

    plan = df[["customer_id", "international_plan", "voice_mail_plan",
               "number_vmail_messages"]].copy()
    plan["number_vmail_messages"] = plan["number_vmail_messages"].astype(int)

    # wide -> long
    usage = pd.concat([
        pd.DataFrame({
            "customer_id": df.customer_id,
            "period": p,
            "minutes": df[f"{p}_minutes"],
            "calls": df[f"{p}_calls"].astype(int),
            "charge": df[f"{p}_charge"],
        })
        for p in C.USAGE_PERIODS
    ], ignore_index=True).sort_values(["customer_id", "period"])
    usage.insert(0, "usage_id", range(1, len(usage) + 1))

    intl = df[["customer_id", "intl_minutes", "intl_calls", "intl_charge"]].copy()
    intl["intl_calls"] = intl["intl_calls"].astype(int)

    churn = df[["customer_id", "churned", "customer_service_calls",
                "high_service_calls"]].copy()
    churn["customer_service_calls"] = churn["customer_service_calls"].astype(int)

    return {
        "dim_tenure_cohort": cohorts,
        "customer": customer,
        "plan_subscription": plan,
        "usage_record": usage,
        "international_usage": intl,
        "churn_record": churn,
    }


LOAD_ORDER = ["dim_tenure_cohort", "customer", "plan_subscription",
              "usage_record", "international_usage", "churn_record"]


def load_to_db(frames: dict[str, pd.DataFrame], engine) -> None:
    log.info("Loading to %s", C.DB_URL)
    models.drop_all(engine)
    models.create_all(engine)
    for table in LOAD_ORDER:
        frame = frames[table]
        frame.to_sql(table, engine, if_exists="append", index=False)
        log.info("  %-22s %6d rows", table, len(frame))


# ==========================================================================
# MAIN
# ==========================================================================
def run():
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = load_csv(C.RAW_CSV)
    df = clean(df)
    df = engineer(df)
    frames = build_frames(df)

    engine = create_engine(C.DB_URL)
    load_to_db(frames, engine)

    log.info("Phase 1 load complete.")
    return df, engine


if __name__ == "__main__":
    run()