"""
Phase 1 validation — proves the database faithfully represents the CSV.

Run AFTER etl.py:   python checks.py
Exit code 0 = all checks passed. Non-zero = something is wrong.

This is the gate for Phase 2: do not build metric views until this is green.
"""

from __future__ import annotations

import sys

import pandas as pd
from sqlalchemy import create_engine, text

import config as C
from etl import load_csv, clean

TOL = 0.01   # currency tolerance

results: list[tuple[bool, str, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def q1(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def run():
    engine = create_engine(C.DB_URL)
    src = clean(load_csv(C.RAW_CSV))

    src["total_charge"] = src[["day_charge", "eve_charge",
                               "night_charge", "intl_charge"]].sum(axis=1)

    print("\n=== ROW COUNTS ===")
    n = len(src)
    check("customer rowcount", q1(engine, "SELECT COUNT(*) FROM customer") == n,
          f"expected {n}")
    check("plan_subscription rowcount",
          q1(engine, "SELECT COUNT(*) FROM plan_subscription") == n)
    check("international_usage rowcount",
          q1(engine, "SELECT COUNT(*) FROM international_usage") == n)
    check("churn_record rowcount",
          q1(engine, "SELECT COUNT(*) FROM churn_record") == n)
    check("usage_record rowcount = 3 x customers",
          q1(engine, "SELECT COUNT(*) FROM usage_record") == n * 3,
          f"expected {n * 3}")

    print("\n=== REFERENTIAL INTEGRITY ===")
    orphan = q1(engine, """
        SELECT COUNT(*) FROM usage_record u
        LEFT JOIN customer c ON u.customer_id = c.customer_id
        WHERE c.customer_id IS NULL""")
    check("no orphan usage_record rows", orphan == 0, f"{orphan} orphans")

    bad_cohort = q1(engine, """
        SELECT COUNT(*) FROM customer c
        LEFT JOIN dim_tenure_cohort d ON c.cohort_id = d.cohort_id
        WHERE d.cohort_id IS NULL""")
    check("every customer has a valid cohort", bad_cohort == 0)

    missing_periods = q1(engine, """
        SELECT COUNT(*) FROM (
            SELECT customer_id FROM usage_record
            GROUP BY customer_id HAVING COUNT(DISTINCT period) <> 3)""")
    check("every customer has 3 usage periods", missing_periods == 0)

    print("\n=== FINANCIAL RECONCILIATION ===")
    src_rev = float(src["total_charge"].sum())
    db_rev = q1(engine, """
        SELECT (SELECT SUM(charge) FROM usage_record)
             + (SELECT SUM(intl_charge) FROM international_usage)""")
    check("total revenue matches CSV", abs(src_rev - db_rev) < TOL,
          f"csv={src_rev:,.2f} db={db_rev:,.2f} diff={src_rev - db_rev:,.4f}")

    for period in C.USAGE_PERIODS:
        s = float(src[f"{period}_charge"].sum())
        d = q1(engine, f"SELECT SUM(charge) FROM usage_record WHERE period='{period}'")
        check(f"{period} charge matches", abs(s - d) < TOL, f"{s:,.2f} vs {d:,.2f}")

    s = float(src["intl_charge"].sum())
    d = q1(engine, "SELECT SUM(intl_charge) FROM international_usage")
    check("intl charge matches", abs(s - d) < TOL, f"{s:,.2f} vs {d:,.2f}")

    print("\n=== TARGET & FLAGS ===")
    s_churn = int(src["churned"].sum())
    d_churn = q1(engine, "SELECT SUM(churned) FROM churn_record")
    check("churned count matches", s_churn == d_churn, f"{s_churn} vs {d_churn}")

    s_hsc = int((src["customer_service_calls"] > C.HIGH_SERVICE_CALLS_THRESHOLD).sum())
    d_hsc = q1(engine, "SELECT SUM(high_service_calls) FROM churn_record")
    check("high_service_calls flag matches", s_hsc == d_hsc, f"{s_hsc} vs {d_hsc}")

    s_intl = int(src["international_plan"].sum())
    d_intl = q1(engine, "SELECT SUM(international_plan) FROM plan_subscription")
    check("international_plan count matches", s_intl == d_intl, f"{s_intl} vs {d_intl}")

    print("\n=== NO TIME DIMENSION (Phase 0 constraint) ===")
    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
        date_cols = []
        for t in tables:
            for row in conn.execute(text(f"PRAGMA table_info({t})")):
                col = row[1].lower()
                if any(k in col for k in ("date", "time", "month", "year", "_at")):
                    date_cols.append(f"{t}.{row[1]}")
    check("no date/time columns exist", not date_cols, str(date_cols))

    print("\n=== COHORT DISTRIBUTION ===")
    dist = pd.read_sql("""
        SELECT d.label, d.sort_order, COUNT(*) AS n,
               ROUND(AVG(CAST(ch.churned AS FLOAT)) * 100, 1) AS churn_pct
        FROM customer c
        JOIN dim_tenure_cohort d ON c.cohort_id = d.cohort_id
        JOIN churn_record ch ON ch.customer_id = c.customer_id
        GROUP BY d.label, d.sort_order ORDER BY d.sort_order""", engine)
    dist["share_pct"] = (dist.n / dist.n.sum() * 100).round(1)
    print(dist[["label", "n", "share_pct", "churn_pct"]].to_string(index=False))
    check("all cohorts >= min share",
          bool((dist.share_pct >= C.MIN_COHORT_SHARE * 100).all()))

    failed = [n for ok, n, _ in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — Phase 1 complete, Phase 2 unblocked.")
    return 0


if __name__ == "__main__":
    sys.exit(run())