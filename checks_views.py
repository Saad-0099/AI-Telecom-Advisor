"""
Phase 2 validation — proves every view agrees with the base tables
and with each other.

Run AFTER build_views.py:   python checks_views.py

This is the gate for everything downstream: the charts, the rules engine,
and the LLM layers all consume these views, so they must be right first.

The regression guards at the bottom are the important part. They pin the
three confirmed churn drivers, so a badly-tuned threshold fails here rather
than surfacing as a quietly wrong recommendation three phases later.
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

    print("\n=== VIEWS EXIST ===")
    with e.connect() as conn:
        present = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='view'"))}
    expected = {
        "v_customer_profile", "v_churn_features", "v_kpi_summary",
        "v_churn_by_state", "v_revenue_by_state", "v_churn_by_service_calls",
        "v_churn_by_plan", "v_cohort_profile", "v_cohort_risk_matrix",
        "v_revenue_by_period", "v_risk_segments", "v_churn_by_day_usage",
    }
    missing = expected - present
    check("all 12 views are built", not missing,
          f"missing: {sorted(missing)}. Run build_views.py" if missing else "")
    if missing:
        print("\nStopping: later checks would fail confusingly on missing views.")
        return 1

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
        "v_kpi_summary":        "SELECT total_revenue FROM v_kpi_summary",
        "v_customer_profile":   "SELECT SUM(total_charge) FROM v_customer_profile",
        "v_revenue_by_state":   "SELECT SUM(revenue) FROM v_revenue_by_state",
        "v_churn_by_state":     "SELECT SUM(revenue) FROM v_churn_by_state",
        "v_revenue_by_period":  "SELECT SUM(revenue) FROM v_revenue_by_period",
        "v_risk_segments":      "SELECT SUM(revenue) FROM v_risk_segments",
        "v_cohort_profile":     "SELECT SUM(revenue) FROM v_cohort_profile",
        "v_churn_by_plan":      "SELECT SUM(revenue) FROM v_churn_by_plan",
        "v_churn_by_day_usage": "SELECT SUM(revenue) FROM v_churn_by_day_usage",
    }
    for name, sql in paths.items():
        v = q1(e, sql)
        check(f"{name} revenue", abs(v - base) < TOL, f"{v:,.2f} vs base {base:,.2f}")

    print("\n=== CUSTOMER COUNTS RECONCILE ===")
    for name, sql in {
        "v_churn_by_state":         "SELECT SUM(customers) FROM v_churn_by_state",
        "v_risk_segments":          "SELECT SUM(customers) FROM v_risk_segments",
        "v_cohort_profile":         "SELECT SUM(customers) FROM v_cohort_profile",
        "v_cohort_risk_matrix":     "SELECT SUM(customers) FROM v_cohort_risk_matrix",
        "v_churn_by_plan":          "SELECT SUM(customers) FROM v_churn_by_plan",
        "v_churn_by_service_calls": "SELECT SUM(customers) FROM v_churn_by_service_calls",
        "v_churn_by_day_usage":     "SELECT SUM(customers) FROM v_churn_by_day_usage",
    }.items():
        check(f"{name} customers", q1(e, sql) == n)

    print("\n=== CHURN COUNTS RECONCILE ===")
    churned = q1(e, "SELECT SUM(churned) FROM churn_record")
    for name, sql in {
        "v_kpi_summary":        "SELECT churned_customers FROM v_kpi_summary",
        "v_risk_segments":      "SELECT SUM(churned) FROM v_risk_segments",
        "v_cohort_profile":     "SELECT SUM(churned) FROM v_cohort_profile",
        "v_churn_by_state":     "SELECT SUM(churned) FROM v_churn_by_state",
        "v_churn_by_day_usage": "SELECT SUM(churned) FROM v_churn_by_day_usage",
    }.items():
        check(f"{name} churned", q1(e, sql) == churned, f"{churned} total")

    print("\n=== KNOWN DATA FACTS (regression guards) ===")
    # These pin the three confirmed churn drivers. If a threshold is retuned
    # badly, or the ETL changes shape, it fails HERE rather than surfacing as
    # a quietly wrong recommendation in Phase 6.
    check("overall churn rate is 14.49%",
          abs(q1(e, "SELECT churn_rate_pct FROM v_kpi_summary") - 14.49) < 0.01)

    # Driver 1 — service calls
    cliff_lo = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                        FROM v_churn_by_service_calls WHERE service_call_bucket='0-3'""")
    cliff_hi = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                        FROM v_churn_by_service_calls WHERE service_call_bucket='4+'""")
    check("service-call cliff intact (4+ at least 3x higher)",
          cliff_hi > cliff_lo * 3, f"{cliff_lo}% -> {cliff_hi}%")

    # Driver 2 — international plan
    check("intl plan churn is elevated",
          q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                   FROM v_churn_by_plan WHERE intl_plan='Intl plan'""") > 35)

    # Driver 3 — heavy daytime usage
    heavy = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                     FROM v_churn_by_day_usage WHERE day_usage_bucket='heavy'""")
    normal = q1(e, """SELECT ROUND(SUM(churned)*100.0/SUM(customers),2)
                      FROM v_churn_by_day_usage WHERE day_usage_bucket='normal'""")
    check("day-usage cliff intact (heavy at least 4x normal)",
          heavy > normal * 4, f"{normal}% -> {heavy}%")

    # The cliff must be a cliff, not a slope: churn should NOT rise
    # monotonically through the low bands. If it ever does, the '45' cut
    # point is no longer meaningful and the bucketing needs revisiting.
    band_rates = [r[0] for r in _rows(e, """
        SELECT churn_rate_pct FROM v_churn_by_day_usage
        ORDER BY day_charge_band""")]
    monotonic_low = all(band_rates[i] <= band_rates[i + 1]
                        for i in range(min(2, len(band_rates) - 1)))
    check("day-usage is a cliff, not a slope (churn dips before the jump)",
          not monotonic_low,
          f"bands: {band_rates}")

    # Segments must stay ordered by severity, or the rules engine priority
    # order in rules.py no longer matches the data.
    seg = _rows(e, """SELECT severity_rank, churn_rate_pct
                      FROM v_risk_segments ORDER BY severity_rank""")
    ordered = all(seg[i][1] >= seg[i + 1][1] for i in range(len(seg) - 1))
    check("risk segments ordered by severity", ordered,
          " > ".join(f"{r[1]}%" for r in seg))

    check("baseline segment is below 10%",
          q1(e, """SELECT churn_rate_pct FROM v_risk_segments
                   WHERE segment LIKE 'Baseline%'""") < 10)

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
    print(f"ALL {len(results)} CHECKS PASSED — Phase 2 verified.")
    return 0


def _rows(engine, sql: str) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


if __name__ == "__main__":
    sys.exit(run())