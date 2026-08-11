"""
Phase 2 validation — proves every view agrees with the base tables
and with each other.

Run AFTER build_views.py:   python checks_views.py

This is the gate for Phase 3: Power BI numbers must match these, so these
must be right first.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

import config as C

TOL = 0.02
results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def q1(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def run():
    e = create_engine(C.DB_URL)

    print("\n=== GRAIN ===")
    n = q1(e, "SELECT COUNT(*) FROM customer")
    check("v_customer_profile is 1 row per customer",
          q1(e, "SELECT COUNT(*) FROM v_customer_profile") == n, f"{n} customers")
    check("v_churn_features is 1 row per customer",
          q1(e, "SELECT COUNT(*) FROM v_churn_features") == n)
    check("v_kpi_summary is exactly 1 row",
          q1(e, "SELECT COUNT(*) FROM v_kpi_summary") == 1)

    print("\n=== REVENUE RECONCILES ACROSS EVERY PATH ===")
    base = q1(e, """SELECT (SELECT SUM(charge) FROM usage_record)
                         + (SELECT SUM(intl_charge) FROM international_usage)""")
    paths = {
        "v_kpi_summary":       "SELECT total_revenue FROM v_kpi_summary",
        "v_customer_profile":  "SELECT SUM(total_charge) FROM v_customer_profile",
        "v_revenue_by_state":  "SELECT SUM(revenue) FROM v_revenue_by_state",
        "v_churn_by_state":    "SELECT SUM(revenue) FROM v_churn_by_state",
        "v_revenue_by_period": "SELECT SUM(revenue) FROM v_revenue_by_period",
        "v_risk_segments":     "SELECT SUM(revenue) FROM v_risk_segments",
        "v_cohort_profile":    "SELECT SUM(revenue) FROM v_cohort_profile",
        "v_churn_by_plan":     "SELECT SUM(revenue) FROM v_churn_by_plan",
    }
    for name, sql in paths.items():
        v = q1(e, sql)
        check(f"{name} revenue", abs(v - base) < TOL, f"{v:,.2f} vs base {base:,.2f}")

    print("\n=== CUSTOMER COUNTS RECONCILE ===")
    for name, sql in {
        "v_churn_by_state":      "SELECT SUM(customers) FROM v_churn_by_state",
        "v_risk_segments":       "SELECT SUM(customers) FROM v_risk_segments",
        "v_cohort_profile":      "SELECT SUM(customers) FROM v_cohort_profile",
        "v_cohort_risk_matrix":  "SELECT SUM(customers) FROM v_cohort_risk_matrix",
        "v_churn_by_plan":       "SELECT SUM(customers) FROM v_churn_by_plan",
        "v_churn_by_service_calls": "SELECT SUM(customers) FROM v_churn_by_service_calls",
    }.items():
        check(f"{name} customers", q1(e, sql) == n)

    print("\n=== CHURN COUNTS RECONCILE ===")
    churned = q1(e, "SELECT SUM(churned) FROM churn_record")
    for name, sql in {
        "v_kpi_summary":     "SELECT churned_customers FROM v_kpi_summary",
        "v_risk_segments":   "SELECT SUM(churned) FROM v_risk_segments",
        "v_cohort_profile":  "SELECT SUM(churned) FROM v_cohort_profile",
        "v_churn_by_state":  "SELECT SUM(churned) FROM v_churn_by_state",
    }.items():
        check(f"{name} churned", q1(e, sql) == churned, f"{churned} total")

    print("\n=== KNOWN DATA FACTS (regression guards) ===")
    check("overall churn rate is 14.49%",
          abs(q1(e, "SELECT churn_rate_pct FROM v_kpi_summary") - 14.49) < 0.01)
    cliff_lo = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                        FROM v_churn_by_service_calls WHERE service_call_bucket='0-3'""")
    cliff_hi = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                        FROM v_churn_by_service_calls WHERE service_call_bucket='4+'""")
    check("service-call cliff intact (4+ at least 3x higher)",
          cliff_hi > cliff_lo * 3, f"{cliff_lo}% -> {cliff_hi}%")
    check("intl plan churn is elevated",
          q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                   FROM v_churn_by_plan WHERE intl_plan='Intl plan'""") > 35)

    print("\n=== NO TIME DIMENSION IN VIEWS ===")
    with e.connect() as conn:
        views = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='view'"))]
        # Token match, not substring: 'revenue_at_risk' must not trip on '_at',
        # and 'day_charge' must not trip on 'day'.
        temporal = {"date", "month", "year", "quarter", "timestamp", "week"}
        bad = []
        for v in views:
            for row in conn.execute(text(f"PRAGMA table_info({v})")):
                col = row[1].lower()
                tokens = set(col.split("_"))
                if (tokens & temporal) or col.endswith("_at"):
                    bad.append(f"{v}.{row[1]}")
    check("no temporal columns in any view", not bad, str(bad))

    failed = [nm for ok, nm in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — Phase 2 complete, Phase 3 unblocked.")
    return 0


if __name__ == "__main__":
    sys.exit(run())