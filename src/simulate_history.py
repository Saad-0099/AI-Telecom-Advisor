"""
Phase 6.5 — simulated monthly history.

WHAT THIS IS
------------
The source CSV is a single snapshot: one row per customer, no dates. This
module derives a monthly panel from it so the platform can show portfolio
structure over time.

WHAT THIS IS NOT
----------------
It is NOT evidence about churn trends. Churn is FLAT by construction here,
because the real data contains no information about how churn moved over
time. Any apparent trend in simulated churn is an artifact, and the
guardrails forbid claiming otherwise.

Forecasting overall churn needs real historical observations. Predicting
WHICH customers will churn is a different problem, solved by the Phase 10
risk model on cross-sectional features. Neither is answered here.

THE BINDING CONSTRAINT
----------------------
Each customer's charges, service calls and churn status sum back to their
CSV row exactly. The simulated layer is a trajectory toward known-real
endpoints, not a replacement for them — so every existing check stays green.

REPRODUCIBILITY
---------------
Everything is seeded (RANDOM_SEED below). Re-running produces identical
output. An unseeded generator would mean figures in a report could never
be recreated.

Run:  python run.py simulate
"""

from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import config as C

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("simulate")

# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
RANDOM_SEED = 42

# A customer is only OBSERVED for their tenure, which maxes out at ~8 months
# in this dataset. Simulating a 12-month window and reporting every month
# produces a ramp-up/ramp-down artifact: the active base climbs from 350 to
# 1512 and collapses to 375, and monthly churn RATE appears to rise from
# 0.29% to 17.33% purely because the denominator shrinks. That reads as a
# churn crisis that does not exist.
#
# The fix is to simulate a wider window and report only the STEADY-STATE
# months in the middle, where the active base is stable. Joins continue
# outside the reported window, so entry and exit balance.
WINDOW_MONTHS = 24                          # simulated
REPORT_MONTHS = 12                          # reported (the stable middle)
WINDOW_END = pd.Timestamp("2025-06-01")
WINDOW_START = WINDOW_END - pd.DateOffset(months=WINDOW_MONTHS - 1)
REPORT_END = pd.Timestamp("2024-12-01")
REPORT_START = REPORT_END - pd.DateOffset(months=REPORT_MONTHS - 1)

DAYS_PER_MONTH = 30.0

# Month-to-month usage variation around each customer's own mean. Small,
# because a customer's calling habits are fairly stable and large random
# jumps look obviously synthetic in a line chart.
USAGE_DRIFT_SD = 0.07

# Fraction of a churner's service calls that land in their final two months.
# Scattering calls uniformly would destroy the causal story: a churner might
# show all four calls AFTER they left. Real escalations cluster before exit.
LATE_CALL_WEIGHT = 0.70

TABLE = "customer_snapshot_simulated"   # name states its own provenance


# ==========================================================================
def month_index() -> pd.DatetimeIndex:
    return pd.date_range(WINDOW_START, WINDOW_END, freq="MS")


def assign_join_months(n: int, tenure_days: np.ndarray, churned: np.ndarray,
                       rng: np.random.Generator) -> np.ndarray:
    """Pick a joining month for each customer.

    Assigned INDEPENDENTLY of tenure. Deriving join dates directly from
    account_length produces a bell-curve acquisition pattern (an artifact
    of the normally-distributed tenure), which looks implausible to anyone
    who has seen a real acquisition chart.
    """
    tenure_months = np.ceil(tenure_days / DAYS_PER_MONTH).astype(int)
    tenure_months = np.clip(tenure_months, 1, WINDOW_MONTHS)

    joins = np.empty(n, dtype=int)
    for i in range(n):
        k = int(tenure_months[i])
        latest = max(WINDOW_MONTHS - k, 0)
        joins[i] = rng.integers(0, latest + 1)
    return joins


def _drift(base: float, k: int, rng: np.random.Generator) -> np.ndarray:
    """k monthly values that vary around `base` and average back to it."""
    if k == 1:
        return np.array([base])
    noise = rng.normal(1.0, USAGE_DRIFT_SD, k)
    noise = np.clip(noise, 0.6, 1.4)
    noise = noise / noise.mean()          # preserve the mean exactly
    return base * noise


def _distribute_calls(total: int, k: int, churned: bool,
                      rng: np.random.Generator) -> np.ndarray:
    """Spread `total` service calls across k months.

    For churners, weight toward the final months so the escalation precedes
    the exit. For non-churners, spread roughly evenly.
    """
    if total == 0:
        return np.zeros(k, dtype=int)
    if k == 1:
        return np.array([total])

    if churned:
        weights = np.full(k, (1 - LATE_CALL_WEIGHT) / max(k - 2, 1))
        weights[-2:] = LATE_CALL_WEIGHT / 2
    else:
        weights = np.full(k, 1.0 / k)
    weights = weights / weights.sum()

    counts = rng.multinomial(total, weights)
    return counts.astype(int)


# ==========================================================================
def build_panel(source: pd.DataFrame) -> pd.DataFrame:
    """Expand the snapshot into a monthly panel."""
    rng = np.random.default_rng(RANDOM_SEED)
    months = month_index()

    tenure = source["account_length_days"].to_numpy()
    join_idx = assign_join_months(
        len(source), tenure, source["churned"].to_numpy().astype(bool), rng)
    tenure_months = np.clip(np.ceil(tenure / DAYS_PER_MONTH).astype(int),
                            1, WINDOW_MONTHS)

    usage_cols = ["day_minutes", "day_charge", "eve_minutes", "eve_charge",
                  "night_minutes", "night_charge", "intl_minutes",
                  "intl_charge"]

    rows = []
    for i, cust in source.reset_index(drop=True).iterrows():
        k = int(tenure_months[i])
        start = int(join_idx[i])
        churned = bool(cust["churned"])

        # Monthly usage: each column drifts around the customer's own mean.
        monthly = {col: _drift(float(cust[col]) / k, k, rng)
                   for col in usage_cols}

        calls = _distribute_calls(int(cust["customer_service_calls"]), k,
                                  churned, rng)

        for m in range(k):
            is_final = (m == k - 1)
            month = months[start + m]
            row = {
                "customer_id": int(cust["customer_id"]),
                "snapshot_month": month.date().isoformat(),
                "month_index": start + m,
                "tenure_month": m + 1,
                "is_active": 1,
                # Churn lands in the final month, and only for churners.
                "churned_this_month": int(churned and is_final),
                "service_calls_this_month": int(calls[m]),
                "service_calls_cumulative": int(calls[: m + 1].sum()),
                "is_final_month": int(is_final),
            }
            for col in usage_cols:
                row[col] = round(float(monthly[col][m]), 4)
            row["total_charge"] = round(
                sum(row[c] for c in usage_cols if c.endswith("charge")), 4)
            rows.append(row)

    return pd.DataFrame(rows)


# ==========================================================================
def load_source() -> pd.DataFrame:
    """Real per-customer values, from the already-built tables."""
    engine = create_engine(C.DB_URL)
    return pd.read_sql(text("""
        SELECT customer_id, account_length_days,
               day_minutes, day_charge, eve_minutes, eve_charge,
               night_minutes, night_charge, intl_minutes, intl_charge,
               customer_service_calls, churned
        FROM v_customer_profile
        ORDER BY customer_id"""), engine)


def reconcile(source: pd.DataFrame, panel: pd.DataFrame) -> list[str]:
    """The simulated panel must sum back to the real snapshot."""
    problems = []

    final = panel[panel.is_final_month == 1]
    if len(final) != len(source):
        problems.append(f"final-month rows {len(final)} != customers {len(source)}")

    agg = panel.groupby("customer_id").agg(
        sim_charge=("total_charge", "sum"),
        sim_calls=("service_calls_this_month", "sum"),
        sim_churn=("churned_this_month", "sum"))
    merged = source.set_index("customer_id").join(agg)

    real_charge = merged[["day_charge", "eve_charge", "night_charge",
                          "intl_charge"]].sum(axis=1)
    drift = (merged.sim_charge - real_charge).abs()
    if drift.max() > 0.02:
        problems.append(f"charge drift up to {drift.max():.4f} "
                        f"on {int((drift > 0.02).sum())} customers")

    call_diff = (merged.sim_calls - merged.customer_service_calls).abs()
    if call_diff.max() > 0:
        problems.append(f"service calls do not reconcile on "
                        f"{int((call_diff > 0).sum())} customers")

    churn_diff = (merged.sim_churn - merged.churned.astype(int)).abs()
    if churn_diff.max() > 0:
        problems.append(f"churn flag does not reconcile on "
                        f"{int((churn_diff > 0).sum())} customers")

    return problems


def write(panel: pd.DataFrame) -> None:
    engine = create_engine(C.DB_URL)
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
    panel.to_sql(TABLE, engine, index=False)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_sim_month "
                          f"ON {TABLE}(snapshot_month)"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_sim_customer "
                          f"ON {TABLE}(customer_id)"))


# ==========================================================================
def run() -> int:
    log.info("Simulating %d months: %s to %s (seed=%d)",
             WINDOW_MONTHS, WINDOW_START.date(), WINDOW_END.date(),
             RANDOM_SEED)

    source = load_source()
    log.info("  source: %d customers", len(source))

    panel = build_panel(source)
    log.info("  panel:  %d rows (%.1f months per customer)",
             len(panel), len(panel) / len(source))

    # Reconciliation runs on the FULL panel: every customer's charges and
    # service calls must sum back to their real values, including months
    # outside the reported window.
    problems = reconcile(source, panel)
    if problems:
        log.error("RECONCILIATION FAILED:")
        for p in problems:
            log.error("  - %s", p)
        return 1
    log.info("  reconciles to the real snapshot exactly")

    # Report only the steady-state middle of the simulated window.
    reported = panel[
        (panel.snapshot_month >= REPORT_START.date().isoformat())
        & (panel.snapshot_month <= REPORT_END.date().isoformat())].copy()
    log.info("  reported window: %s to %s (%d rows, %.0f%% of panel)",
             REPORT_START.date(), REPORT_END.date(), len(reported),
             100 * len(reported) / len(panel))

    write(reported)
    log.info("  written to %s", TABLE)

    monthly = reported.groupby("snapshot_month").agg(
        active=("customer_id", "size"),
        churned=("churned_this_month", "sum"),
        revenue=("total_charge", "sum"))
    monthly["churn_pct"] = (monthly.churned / monthly.active * 100).round(2)
    log.info("\n%s", monthly.to_string())

    spread = monthly.churn_pct.max() - monthly.churn_pct.min()
    log.info("\nMonthly churn spread: %.2f points (max %.2f, min %.2f)",
             spread, monthly.churn_pct.max(), monthly.churn_pct.min())
    log.info("NOTE: churn is FLAT by construction. Any month-to-month "
             "variation is sampling noise from when customers joined and "
             "exited, NOT a real trend. This panel cannot support a "
             "forecast; see Phase 10 for per-customer risk scoring.")
    return 0


if __name__ == "__main__":
    sys.exit(run())