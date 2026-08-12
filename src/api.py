"""
Phase 2 — FastAPI metrics API.

Run:  uvicorn api:app --reload
Docs: http://127.0.0.1:8000/docs

Read-only by design. Every endpoint is backed by a SQL view; no endpoint
computes anything in Python. Power BI and the Phase 4/5 LLM layers both
consume this API, guaranteeing they agree on every number.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

import metrics

app = FastAPI(
    title="Telecom Decision Intelligence — Metrics API",
    description=(
        "Deterministic metrics layer. All figures computed in SQL. "
        "Data is a SINGLE SNAPSHOT with no time dimension: comparisons "
        "are valid between segments and cohorts only, never across time."
    ),
    version="0.2.0",
)


@app.get("/health", tags=["meta"])
def health():
    try:
        n = metrics.kpi_summary()["data"]["total_customers"]
        return {"status": "ok", "customers": n}
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {exc}")


@app.get("/meta/constraints", tags=["meta"])
def constraints():
    """The snapshot constraints. Phase 4/5 inject these into every prompt."""
    return metrics.SNAPSHOT_META


@app.get("/metrics/kpi-summary", tags=["metrics"])
def kpi_summary():
    return metrics.kpi_summary()


@app.get("/metrics/churn-by-state", tags=["metrics"])
def churn_by_state(
    min_customers: int = Query(0, ge=0, description="Suppress small states"),
    limit: int | None = Query(None, ge=1, le=51),
    order: str = Query("churn_rate_pct"),
):
    try:
        return metrics.churn_by_state(min_customers, limit, order)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/metrics/revenue-by-state", tags=["metrics"])
def revenue_by_state(limit: int | None = Query(None, ge=1, le=51)):
    return metrics.revenue_by_state(limit)


@app.get("/metrics/churn-by-service-calls", tags=["metrics"])
def churn_by_service_calls():
    return metrics.churn_by_service_calls()


@app.get("/metrics/churn-by-plan", tags=["metrics"])
def churn_by_plan():
    return metrics.churn_by_plan()


@app.get("/metrics/risk-segments", tags=["metrics"])
def risk_segments():
    return metrics.risk_segments()


@app.get("/metrics/cohort-profile", tags=["metrics"])
def cohort_profile():
    return metrics.cohort_profile()


@app.get("/metrics/cohort-risk-matrix", tags=["metrics"])
def cohort_risk_matrix():
    return metrics.cohort_risk_matrix()


@app.get("/metrics/revenue-by-period", tags=["metrics"])
def revenue_by_period():
    return metrics.revenue_by_period()


@app.get("/customers/{customer_id}", tags=["customers"])
def get_customer(customer_id: int):
    row = metrics.customer(customer_id)
    if row is None:
        raise HTTPException(404, f"customer {customer_id} not found")
    return row


@app.get("/customers", tags=["customers"])
def high_risk_customers(
    limit: int = Query(100, ge=1, le=1000),
    min_risk_factors: int = Query(1, ge=0, le=2),
):
    return metrics.high_risk_customers(limit, min_risk_factors)


@app.get("/briefing", tags=["llm"])
def briefing():
    """Consolidated payload for the Phase 4 narration layer."""
    return metrics.full_briefing()



# ==========================================================================
# Phase 4 — narration endpoints
# ==========================================================================
import narrate
import llm_provider


@app.get("/llm/status", tags=["llm"])
def llm_status():
    """Which provider is active and whether it can be reached."""
    return llm_provider.provider_status()


@app.get("/narrate/kpi", tags=["llm"])
def narrate_kpi():
    return narrate.narrate_kpi()


@app.get("/narrate/risk-segments", tags=["llm"])
def narrate_risk_segments():
    return narrate.narrate_risk_segments()


@app.get("/narrate/explain/{metric_name}", tags=["llm"])
def explain_chart(metric_name: str):
    """Module 8 — AI Chart Explainer."""
    res = narrate.explain_chart(metric_name)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.get("/narrate/ask", tags=["llm"])
def ask(
    question: str = Query(..., min_length=3, max_length=500),
    metric_name: str | None = Query(None),
):
    """Constrained Q&A over a metric payload. Phase 5 generalises this."""
    res = narrate.answer_question(question, metric_name)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


# ==========================================================================
# Phase 5 — natural language query
# ==========================================================================
import text_to_sql


@app.get("/ask", tags=["llm"])
def ask_question(
    question: str = Query(..., min_length=3, max_length=500),
    show_sql: bool = Query(True),
):
    """Module 5 — Natural Language Interface.

    Generates SQL, validates it, executes read-only, narrates the rows.
    """
    res = text_to_sql.ask(question)
    if not show_sql:
        res.pop("sql", None)
    return res


# ==========================================================================
# Phase 6 — recommendation engine
# ==========================================================================
import recommend
import rules as rules_config


@app.get("/recommendations", tags=["recommendations"])
def recommendations():
    """Module 6 — deterministic rule-based recommendations."""
    return recommend.recommendations_payload()


@app.get("/recommendations/narrated", tags=["recommendations"])
def recommendations_narrated():
    """Same segments, with an LLM-written justification (validated)."""
    return recommend.narrate_recommendations()


@app.get("/recommendations/rules", tags=["recommendations"])
def list_rules():
    """The rule definitions and economic assumptions driving the output."""
    return {"rules": rules_config.rules_by_priority(),
            "economics": rules_config.ECONOMICS,
            "validation": rules_config.validate_rules() or "valid"}


@app.get("/recommendations/{rule_id}/customers", tags=["recommendations"])
def rule_customers(rule_id: str, limit: int = Query(100, ge=1, le=1000)):
    """The actual customers a rule targets — the audit trail."""
    try:
        df = recommend.customers_for_rule(rule_id, limit)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"rule_id": rule_id, "count": len(df),
            "customers": df.to_dict(orient="records")}



# ==========================================================================
# Phase 3 — charts (rendered on request, never cached to disk)
# ==========================================================================
from fastapi.responses import HTMLResponse, Response

import chart_specs
import charts as charts_mod


@app.get("/charts", tags=["charts"])
def list_charts():
    """Every available chart, with its kind and backing metric."""
    return {"charts": chart_specs.list_charts(),
            "validation": chart_specs.validate_specs() or "valid"}


@app.get("/charts/{name}.png", tags=["charts"])
def chart_png(name: str, dpi: int = Query(110, ge=60, le=300)):
    """Static PNG. Same spec and data as the HTML version."""
    try:
        png = charts_mod.render_matplotlib(name, dpi=dpi)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return Response(content=png, media_type="image/png")


@app.get("/charts/{name}.html", tags=["charts"], response_class=HTMLResponse)
def chart_html(name: str):
    """Interactive Plotly version."""
    try:
        return HTMLResponse(charts_mod.render_plotly(name, full_html=True))
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.get("/charts/{name}/data", tags=["charts"])
def chart_data(name: str):
    """The exact rows plotted — what the explanation is grounded in."""
    try:
        spec, df = charts_mod.chart_data(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return {"chart": name, "title": spec["title"],
            "subtitle": spec.get("subtitle"),
            "caption": spec.get("caption"),
            "rows": df.to_dict(orient="records")}


@app.get("/charts/{name}/explain", tags=["charts"])
def chart_explain(name: str):
    """Module 8 — AI Chart Explainer, grounded in the plotted data."""
    try:
        return charts_mod.explain(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc))