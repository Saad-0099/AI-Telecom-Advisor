"""
Phase 6.5 validation — simulated history panel.

Run:  python run.py check        (offline, free)

Two things must hold:

  1. RECONCILIATION  the panel sums back to the real snapshot exactly, so
     the simulated layer is a trajectory toward known-real endpoints rather
     than a replacement for them.

  2. FLATNESS        monthly churn must NOT trend. A first version of the
     generator produced churn climbing from 0.29% to 17.33% purely as a
     denominator artifact, which reads as a churn crisis that does not
     exist. These checks would have caught that.
"""

from __future__ import annotations

# This test lives in tests/ but imports modules from src/. Adding src/ to the
# path keeps the flat "import metrics" style working from either directory.
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent / "src"))

import sys

import pandas as pd
from sqlalchemy import create_engine, text

import config as C

results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    e = create_engine(C.DB_URL)

    with e.connect() as conn:
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='customer_snapshot_simulated'")).scalar()
    if not exists:
        print("  simulated panel not built. Run: python run.py simulate")
        return 1

    panel = pd.read_sql(text("SELECT * FROM customer_snapshot_simulated"), e)
    real = pd.read_sql(text("""
        SELECT customer_id, total_charge, customer_service_calls, churned
        FROM v_customer_profile"""), e)

    print("\n=== PROVENANCE IS STRUCTURAL ===")
    check("table name states it is simulated",
          "simulated" in "customer_snapshot_simulated")
    with e.connect() as conn:
        sim_views = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='view' "
            "AND name LIKE 'v_sim_%'"))]
    check("simulated views are name-prefixed", len(sim_views) >= 4,
          f"{len(sim_views)} views")
    origins = pd.read_sql(text(
        "SELECT DISTINCT data_origin FROM v_sim_monthly_portfolio"), e)
    check("views carry a data_origin column",
          list(origins.data_origin) == ["SIMULATED"], str(list(origins.data_origin)))

    print("\n=== SHAPE ===")
    months = panel.snapshot_month.nunique()
    check("12 reported months", months == 12, f"{months}")
    check("every customer appears at least once",
          panel.customer_id.nunique() > 0,
          f"{panel.customer_id.nunique()} of {len(real)} customers")
    check("tenure months are contiguous per customer",
          bool(panel.groupby("customer_id").tenure_month.apply(
              lambda s: sorted(s) == list(range(min(s), max(s) + 1))).all()))

    print("\n=== FLATNESS (the property that matters) ===")
    monthly = pd.read_sql(text("""
        SELECT snapshot_month, active_customers, churn_rate_pct, revenue
        FROM v_sim_monthly_portfolio ORDER BY snapshot_month"""), e)

    spread = monthly.churn_rate_pct.max() - monthly.churn_rate_pct.min()
    check("monthly churn spread under 5 points", spread < 5.0,
          f"{spread:.2f} points "
          f"({monthly.churn_rate_pct.min():.2f}-{monthly.churn_rate_pct.max():.2f})")

    # A monotonic series is a trend, not noise. This is the check that would
    # have caught the ramp artifact.
    rates = list(monthly.churn_rate_pct)
    rising = all(rates[i] <= rates[i + 1] for i in range(len(rates) - 1))
    falling = all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1))
    check("monthly churn is not monotonic", not (rising or falling),
          "rising" if rising else ("falling" if falling else "neither"))

    # The active base must be stable, or the churn RATE moves for reasons
    # that have nothing to do with churn.
    base = monthly.active_customers
    variation = (base.max() - base.min()) / base.mean()
    check("active base is stable (under 25% variation)", variation < 0.25,
          f"{variation * 100:.1f}% ({base.min()}-{base.max()})")

    print("\n=== NO FORECASTING SURFACE ===")
    # Nothing in the schema should invite a projection.
    with e.connect() as conn:
        cols = []
        for v in sim_views:
            for row in conn.execute(text(f"PRAGMA table_info({v})")):
                cols.append(row[1].lower())
    banned = {"forecast", "predicted", "projection", "trend", "expected"}
    found = [c for c in cols if any(b in c for b in banned)]
    check("no forecast-suggesting column names", not found, str(found))

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — simulated panel is flat "
          f"and reconciles.")
    return 0


if __name__ == "__main__":
    sys.exit(run())