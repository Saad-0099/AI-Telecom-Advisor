"""
Phase 9 — analytics pages.

Structure on every page:

    HEADER  ->  KPI ROW  ->  MAIN CHART  ->  SECONDARY  ->  AI  ->  CAVEATS

Every figure comes from components.backend, the only route to the business
logic. No page computes anything.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components import backend as be
from components.ui import (ai_block, card_close, card_open, kpi_row, notice,
                           page_header, section, verification)


# ==========================================================================
def chart_card(name: str, height: int = 380, key: str = "",
               show_explain: bool = True) -> None:
    """A chart inside a card, with an optional on-demand AI explanation.

    Explanations are NOT generated automatically: each is an LLM call
    against a limited daily quota, so the user asks for the ones they want.
    """
    spec = be.chart_spec(name)
    card_open(spec["title"], spec.get("subtitle", ""))
    st.plotly_chart(be.chart_figure(name, height), use_container_width=True,
                    key=f"c_{name}_{key}")
    if spec.get("caption"):
        st.caption(spec["caption"])
    card_close()

    if not show_explain:
        return

    state = f"exp_{name}_{key}"
    col, _ = st.columns([1, 3])
    with col:
        if st.button("✦  Explain this chart", key=f"b_{state}"):
            with st.spinner("Reading the plotted data…"):
                st.session_state[state] = be.explain_chart(name)

    result = st.session_state.get(state)
    if result:
        render_explanation(result)


def render_explanation(result: dict) -> None:
    if result.get("error"):
        st.error(f"Explanation unavailable — {result['error']}")
        return
    v = result.get("validation") or {}
    ai_block("answer", "AI explanation", result["text"], "✦")
    verification(result.get("valid", False), v.get("numbers_checked", 0),
                 "the plotted data")
    if not result.get("valid"):
        for x in v.get("violations", []):
            st.warning(f"**{x['type']}** — {x['detail']}")


# ==========================================================================
def overview() -> None:
    page_header(
        "Executive Overview",
        "The current state of the portfolio, and where the risk sits.",
        "observed")

    k = be.kpi()
    kpi_row([
        {"label": "Total customers", "value": f"{k['total_customers']:,}",
         "sub": "Records in the dataset", "icon": "◱", "accent": "accent"},
        {"label": "Churn rate", "value": f"{k['churn_rate_pct']}%",
         "sub": f"{k['churned_customers']:,} customers", "icon": "◲",
         "accent": "danger"},
        {"label": "Total revenue", "value": f"${k['total_revenue']:,.0f}",
         "sub": "Period charges, reconciled", "icon": "◆",
         "accent": "success"},
        {"label": "Revenue at risk", "value": f"${k['revenue_at_risk']:,.0f}",
         "sub": "Attached to churned customers", "icon": "▲",
         "accent": "warning"},
    ])

    section("Risk segmentation")
    chart_card("risk_segments", 400, "ov")

    section("Confirmed churn drivers")
    left, right = st.columns([3, 2], gap="large")
    with left:
        card_open("Driver thresholds",
                  "All three are cliffs. A customer either crosses the line "
                  "or does not — none of them is a gradual trend.")
        st.dataframe(pd.DataFrame(driver_rows()), use_container_width=True,
                     hide_index=True)
        card_close()
    with right:
        card_open("What this cannot tell you",
                  "Stated as content, not fine print.")
        for c in be.caveats():
            st.markdown(f'<div style="font-size:.82rem;color:#8794A8;'
                        f'line-height:1.6;padding:.45rem 0;border-bottom:'
                        f'1px solid #161E2B">{c}</div>',
                        unsafe_allow_html=True)
        card_close()


def driver_rows() -> list[dict]:
    """Regroups rows the backend already computed. No new statistics."""
    def rollup(rows, key):
        out = {}
        for r in rows:
            b = out.setdefault(r[key], {"customers": 0, "churned": 0})
            b["customers"] += r["customers"]
            b["churned"] += r["churned"]
        for b in out.values():
            b["rate"] = round(b["churned"] * 100 / b["customers"], 2)
        return out

    calls = rollup(be.metric("churn_by_service_calls"), "service_call_bucket")
    day = rollup(be.metric("churn_by_day_usage"), "day_usage_bucket")
    plans = be.metric("churn_by_plan")
    intl = [p for p in plans if p["intl_plan"] == "Intl plan"]
    other = [p for p in plans if p["intl_plan"] != "Intl plan"]
    ir = round(sum(p["churned"] for p in intl) * 100
               / sum(p["customers"] for p in intl), 2)
    nr = round(sum(p["churned"] for p in other) * 100
               / sum(p["customers"] for p in other), 2)

    return [
        {"Driver": "Service calls", "Threshold": "4 or more",
         "Below": f"{calls['0-3']['rate']}%",
         "Above": f"{calls['4+']['rate']}%",
         "Customers": f"{calls['4+']['customers']:,}"},
        {"Driver": "International plan", "Threshold": "subscribed",
         "Below": f"{nr}%", "Above": f"{ir}%",
         "Customers": f"{sum(p['customers'] for p in intl):,}"},
        {"Driver": "Daytime charge", "Threshold": "$45 or more",
         "Below": f"{day['normal']['rate']}%",
         "Above": f"{day['heavy']['rate']}%",
         "Customers": f"{day['heavy']['customers']:,}"},
    ]


# ==========================================================================
def churn() -> None:
    page_header(
        "Churn Analytics",
        "Three confirmed drivers, each a threshold rather than a slope.",
        "observed")

    tabs = st.tabs(["Service calls", "Plan combination", "Daytime usage",
                    "Geography"])
    with tabs[0]:
        chart_card("churn_by_service_calls", 400, "ch1")
    with tabs[1]:
        chart_card("churn_by_plan", 400, "ch2")
    with tabs[2]:
        chart_card("churn_by_day_usage", 400, "ch3")
    with tabs[3]:
        notice("info",
               "<b>Read with caution.</b> 51 states average about 65 "
               "customers each, so a handful of churners moves a state "
               "several percentage points. Geography is the weakest "
               "dimension here — behaviour is a better basis for targeting.")
        chart_card("top_states_by_churn", 400, "ch4")


# ==========================================================================
def segments() -> None:
    page_header(
        "Customer Segments",
        "Mutually exclusive segments built from the three drivers, each "
        "with the action it implies.", "observed")

    payload = be.recommendations()
    segs = [s for s in payload["segments"] if s["observed"]["customers"] > 0]
    worst = max(segs, key=lambda s: s["observed"]["churn_rate_pct"])
    biggest = max(segs, key=lambda s: s["observed"]["revenue_at_risk"])

    kpi_row([
        {"label": "Segments", "value": str(len(segs)),
         "sub": "Exhaustive and non-overlapping", "icon": "◈"},
        {"label": "Highest rate",
         "value": f"{worst['observed']['churn_rate_pct']}%",
         "sub": worst["name"][:34], "icon": "▲", "accent": "danger"},
        {"label": "Largest exposure",
         "value": f"${biggest['observed']['revenue_at_risk']:,.0f}",
         "sub": biggest["name"][:34], "icon": "◆", "accent": "warning"},
    ])

    section("Rate against scale")
    a, b = st.columns(2, gap="large")
    with a:
        card_open("Churn rate by segment")
        st.plotly_chart(be.chart_figure("risk_segments", 340),
                        use_container_width=True, key="sg1")
        card_close()
    with b:
        card_open("Customers per segment",
                  "Read rates alongside n: the highest-rate segments are "
                  "small enough to handle individually.")
        st.plotly_chart(be.chart_figure("segment_sizes", 340),
                        use_container_width=True, key="sg2")
        card_close()

    section("Segments and recommended actions")
    card_open()
    st.dataframe(pd.DataFrame([{
        "Segment": s["name"],
        "Customers": s["observed"]["customers"],
        "Churn %": s["observed"]["churn_rate_pct"],
        "Revenue at risk": round(s["observed"]["revenue_at_risk"], 2),
        "Action": s["action"],
        "Net value †": (None if not s.get("projected") else
                        round(s["projected"]["expected"]["net_value"], 2)),
    } for s in segs]), use_container_width=True, hide_index=True)
    card_close()

    e = be.economics()
    notice("projected",
           f"<b>† Projected values rest on assumptions.</b> Save rate "
           f"{e['assumed_save_rate']:.0%} (band {e['save_rate_low']:.0%}–"
           f"{e['save_rate_high']:.0%}), contact cost "
           f"${e['retention_contact_cost']:.2f}, acquisition cost "
           f"${e['acquisition_cost']:.2f}. Industry-typical placeholders, "
           f"not measurements — the dataset contains no cost data.")

    section("Target list")
    actionable = [s for s in segs if s.get("offer")]
    if not actionable:
        st.info("No segment currently carries an offer.")
        return

    card_open("Export", "Every segment traces to real customer IDs a "
                        "retention team could work.")
    choice = st.selectbox("Segment", [s["name"] for s in actionable],
                          key="tgt")
    picked = next(s for s in actionable if s["name"] == choice)
    st.markdown(f"**Action** — {picked['action']}  \n"
                f"**Offer** — {picked['offer']}")
    df = pd.DataFrame(be.rule_customers(picked["rule_id"], 500))
    st.dataframe(df, use_container_width=True, hide_index=True, height=280)
    st.download_button(f"↓  Download {len(df)} customer IDs (CSV)",
                       df.to_csv(index=False).encode("utf-8"),
                       file_name=f"{picked['rule_id']}_targets.csv",
                       mime="text/csv")
    card_close()


# ==========================================================================
def cohorts() -> None:
    page_header(
        "Cohort Analysis",
        "Tenure cohorts derived from account length. The dataset has no "
        "dates, so tenure is the ordered axis.", "observed")

    notice("info",
           "<b>Tenure is not a churn driver here.</b> Churn is flat across "
           "all four cohorts. This was expected to matter and does not — a "
           "genuine finding, and a reason to target by behaviour rather "
           "than by how long someone has been a customer.")

    data = be.metric("cohort_profile")
    kpi_row([{
        "label": c["cohort_label"], "value": f"{c['churn_rate_pct']}%",
        "sub": f"{c['customers']:,} customers · {c['share_pct']}%<br>"
               f"ARPU ${c['arpu']:,.2f}", "icon": "◷",
    } for c in data])

    section("Churn by cohort")
    chart_card("cohort_profile", 360, "co1")

    section("Cohort against service calls")
    chart_card("cohort_risk_matrix", 380, "co2")

    if be.has_simulated():
        simulated_section()


def simulated_section() -> None:
    """Quarantined behind an unmissable notice and its own colour."""
    section("Simulated history")
    notice("simulated",
           "<b>SIMULATED — NOT OBSERVED.</b> Monthly history is generated "
           "from the snapshot because the source dataset has no date "
           "column. It shows portfolio structure only; churn in it is flat "
           "<i>by construction</i>.")

    available = {c["id"] for c in be.chart_list()}
    labels = [("sim_monthly_structure", "Active base"),
              ("sim_monthly_revenue", "Revenue"),
              ("sim_monthly_churn", "Churn (flat)")]
    present = [(n, l) for n, l in labels if n in available]

    if not present:
        st.info("No simulated charts are defined in chart_specs.py.")
        return

    tabs = st.tabs([l for _, l in present])
    for tab, (name, _) in zip(tabs, present):
        with tab:
            chart_card(name, 360, "sim")


# ==========================================================================
def revenue() -> None:
    page_header("Revenue & Risk",
                "Where revenue sits, and where it is exposed.", "observed")

    k = be.kpi()
    kpi_row([
        {"label": "Total revenue", "value": f"${k['total_revenue']:,.0f}",
         "sub": "Period charges, not annualised", "icon": "◆",
         "accent": "success"},
        {"label": "ARPU", "value": f"${k['arpu']:,.2f}",
         "sub": "Per customer", "icon": "◇"},
        {"label": "Revenue at risk", "value": f"${k['revenue_at_risk']:,.0f}",
         "sub": f"{k['revenue_at_risk'] / k['total_revenue'] * 100:.1f}% of "
                f"total", "icon": "▲", "accent": "warning"},
    ])

    section("Exposure by segment")
    notice("info",
           "Rate and exposure rank <b>differently</b>. A large low-rate "
           "segment can carry more absolute revenue risk than a small "
           "high-rate one — so targeting only the worst rates would leave "
           "the biggest dollar exposure unaddressed.")
    chart_card("revenue_at_risk", 380, "rv1")

    section("Revenue by call period")
    chart_card("revenue_by_period", 340, "rv2")