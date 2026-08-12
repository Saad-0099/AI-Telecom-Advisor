"""
Phase 2 — metric access layer.

Every function returns a plain dict/list of primitives, ready to be
serialised to JSON by FastAPI *or* injected into an LLM prompt in Phase 4.

Each payload carries a `meta` block stating the snapshot constraint. That
block is not decoration: it travels with the numbers into the prompt so the
model is always told, in-band, what it may and may not claim. In live
testing the model was observed citing it directly.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

import config as C

_engine = None

SNAPSHOT_META = {
    "grain": "single snapshot",
    "time_dimension": None,
    "comparisons_allowed": "between segments and cohorts only",
    "comparisons_forbidden": "across time (no prior period exists)",
    "currency_note": "charges are period charges, not monthly recurring revenue",
    "service_call_note": "churn is a cliff at 4+ calls, not a linear slope",
    "cohort_note": "churn is flat across tenure cohorts; tenure is not a churn driver",
    "day_usage_note": (
        "day_charge >= 45 (about 265 daytime minutes) is a THIRD independent "
        "driver, also a cliff: it raises churn from 5.0% to 59.0% among "
        "customers with no other risk factor, and raises it further in every "
        "combination of the other two drivers"
    ),
    "confirmed_drivers": [
        "4+ customer service calls",
        "international plan subscription",
        "day_charge >= 45 (heavy daytime usage)",
    ],
}


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(C.DB_URL)
    return _engine


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    df = pd.read_sql(text(sql), engine(), params=params or {})
    return df.to_dict(orient="records")


def _payload(metric: str, data, **extra) -> dict:
    return {"metric": metric, "meta": SNAPSHOT_META, "data": data, **extra}


# ==========================================================================
# KPI
# ==========================================================================
def kpi_summary() -> dict:
    row = _rows("SELECT * FROM v_kpi_summary")[0]
    return _payload("kpi_summary", row)


# ==========================================================================
# Geography
# ==========================================================================
def churn_by_state(min_customers: int = 0, limit: int | None = None,
                   order: str = "churn_rate_pct") -> dict:
    allowed = {"churn_rate_pct", "revenue", "customers", "arpu", "state"}
    if order not in allowed:
        raise ValueError(f"order must be one of {sorted(allowed)}")
    sql = f"""SELECT * FROM v_churn_by_state
              WHERE customers >= :minc
              ORDER BY {order} DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _payload("churn_by_state", _rows(sql, {"minc": min_customers}),
                    filters={"min_customers": min_customers, "order": order})


def revenue_by_state(limit: int | None = None) -> dict:
    sql = "SELECT * FROM v_revenue_by_state ORDER BY revenue DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _payload("revenue_by_state", _rows(sql))


# ==========================================================================
# Drivers — three confirmed, each an independent cliff
# ==========================================================================
def churn_by_service_calls() -> dict:
    """Driver 1. Churn is flat 0-3 calls, then jumps sharply at 4+."""
    detail = _rows("SELECT * FROM v_churn_by_service_calls "
                   "ORDER BY customer_service_calls")
    buckets = _rows("""
        SELECT service_call_bucket,
               SUM(customers) AS customers,
               SUM(churned)   AS churned,
               ROUND(SUM(churned) * 100.0 / SUM(customers), 2) AS churn_rate_pct
        FROM v_churn_by_service_calls
        GROUP BY service_call_bucket ORDER BY service_call_bucket""")
    return _payload("churn_by_service_calls", detail, buckets=buckets)


def churn_by_plan() -> dict:
    """Driver 2. International plan subscribers churn several times higher."""
    return _payload("churn_by_plan",
                    _rows("SELECT * FROM v_churn_by_plan "
                          "ORDER BY churn_rate_pct DESC"))


def churn_by_day_usage() -> dict:
    """Driver 3. Banded, because it is a cliff not a slope.

    Churn actually DIPS from 11.6% to 8.1% before exploding at day_charge 45.
    A linear model of this variable would find almost nothing.
    """
    detail = _rows("SELECT * FROM v_churn_by_day_usage "
                   "ORDER BY day_charge_band")
    buckets = _rows("""
        SELECT day_usage_bucket,
               SUM(customers) AS customers,
               SUM(churned)   AS churned,
               ROUND(SUM(churned) * 100.0 / SUM(customers), 2) AS churn_rate_pct
        FROM v_churn_by_day_usage
        GROUP BY day_usage_bucket ORDER BY day_usage_bucket""")
    return _payload("churn_by_day_usage", detail, buckets=buckets)


def risk_segments() -> dict:
    """Segments from all three drivers. Ordered by severity, not by rate,
    so the ranking stays stable even if a small segment moves."""
    return _payload("risk_segments",
                    _rows("SELECT * FROM v_risk_segments "
                          "ORDER BY severity_rank"))


# ==========================================================================
# Cohort axis
# ==========================================================================
def cohort_profile() -> dict:
    return _payload("cohort_profile",
                    _rows("SELECT * FROM v_cohort_profile ORDER BY cohort_sort"))


def cohort_risk_matrix() -> dict:
    return _payload("cohort_risk_matrix",
                    _rows("SELECT * FROM v_cohort_risk_matrix "
                          "ORDER BY cohort_sort, service_call_bucket"))


# ==========================================================================
# Usage
# ==========================================================================
def revenue_by_period() -> dict:
    return _payload("revenue_by_period",
                    _rows("SELECT * FROM v_revenue_by_period "
                          "ORDER BY revenue DESC"))


# ==========================================================================
# Customer level
# ==========================================================================
def customer(customer_id: int) -> dict | None:
    rows = _rows("SELECT * FROM v_customer_profile WHERE customer_id = :cid",
                 {"cid": customer_id})
    return rows[0] if rows else None


def high_risk_customers(limit: int = 100, min_factors: int = 1) -> dict:
    """risk_factor_count now ranges 0-3, not 0-2."""
    rows = _rows("""
        SELECT * FROM v_churn_features
        WHERE risk_factor_count >= :mf
        ORDER BY risk_factor_count DESC, total_charge DESC
        LIMIT :lim""", {"mf": min_factors, "lim": limit})
    return _payload("high_risk_customers", rows,
                    filters={"min_risk_factors": min_factors, "limit": limit})


# ==========================================================================
# Bundle — everything an LLM narration call might need, in one payload.
# ==========================================================================
def full_briefing() -> dict:
    """Single consolidated payload for the Phase 4 narration layer."""
    return {
        "meta": SNAPSHOT_META,
        "kpi": kpi_summary()["data"],
        "risk_segments": risk_segments()["data"],
        "service_calls": churn_by_service_calls()["buckets"],
        "plans": churn_by_plan()["data"],
        "day_usage": churn_by_day_usage()["data"],
        "cohorts": cohort_profile()["data"],
        "cohort_risk_matrix": cohort_risk_matrix()["data"],
        "revenue_by_period": revenue_by_period()["data"],
        "top_states_by_revenue": revenue_by_state(limit=10)["data"],
        "top_states_by_churn": churn_by_state(min_customers=50,
                                              limit=10)["data"],
    }

# ==========================================================================
# SIMULATED history (Phase 6.5)
# ==========================================================================
SIMULATED_META = {
    "data_origin": "SIMULATED",
    "warning": (
        "This history is GENERATED from a single snapshot, not observed. "
        "It is suitable for showing portfolio structure over time and for "
        "tenure-lifecycle views."
    ),
    "churn_caveat": (
        "Churn is FLAT by construction. Month-to-month variation is "
        "sampling noise, NOT a trend. This panel cannot support a churn "
        "forecast."
    ),
    "disclosure_required": (
        "Any statement using this data must say the history is simulated."
    ),
}


def _sim_payload(metric: str, data) -> dict:
    return {"metric": metric, "meta": {**SNAPSHOT_META, **SIMULATED_META},
            "data": data}


def sim_monthly_portfolio() -> dict:
    return _sim_payload("sim_monthly_portfolio",
                        _rows("SELECT * FROM v_sim_monthly_portfolio "
                              "ORDER BY snapshot_month"))


def sim_monthly_revenue() -> dict:
    return _sim_payload("sim_monthly_revenue",
                        _rows("SELECT * FROM v_sim_monthly_revenue "
                              "ORDER BY snapshot_month"))


def sim_tenure_curve() -> dict:
    """The legitimate use of the panel: a lifecycle view, not a trend."""
    return _sim_payload("sim_tenure_curve",
                        _rows("SELECT * FROM v_sim_tenure_curve "
                              "ORDER BY tenure_month"))


REGISTRY = {
    "kpi_summary": kpi_summary,
    "churn_by_state": churn_by_state,
    "revenue_by_state": revenue_by_state,
    "churn_by_service_calls": churn_by_service_calls,
    "churn_by_plan": churn_by_plan,
    "churn_by_day_usage": churn_by_day_usage,
    "risk_segments": risk_segments,
    "cohort_profile": cohort_profile,
    "cohort_risk_matrix": cohort_risk_matrix,
    "revenue_by_period": revenue_by_period,
    "sim_monthly_portfolio": sim_monthly_portfolio,
    "sim_monthly_revenue": sim_monthly_revenue,
    "sim_tenure_curve": sim_tenure_curve,
}